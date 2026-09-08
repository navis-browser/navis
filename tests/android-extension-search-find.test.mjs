import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const root = new URL("../", import.meta.url);
const read = name => fs.readFileSync(new URL(name, root), "utf8");
const component = "gecko/mobile/shared/components/extensions/";
const plain = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));

function environment() {
  const calls = [], tabs = new Map();
  const permissions = new Set(["search", "find"]);
  const extension = { id: "focused@test", privateBrowsingAllowed: false,
    hasPermission: value => permissions.has(value),
    tabManager: { canAccessTab: tab => tabs.get(tab.id) === tab && (!tab.private || extension.privateBrowsingAllowed) } };
  const tracker = { activeTab: null, getTab: (id, fallback) => {
    if (tabs.has(id)) return tabs.get(id);
    if (fallback !== undefined) return fallback;
    throw Error("Invalid tab ID");
  }, getId: tab => tab.id };
  const context = { extension, incognito: false, privateBrowsingAllowed: false,
    checkLoadURL: url => url.startsWith("https://"),
    apiCan: { asyncFindAPIPath: async path => {
      assert.equal(path, "windows.create");
      return async args => { calls.push(["window", plain(args)]); throw Error("windows backend pending"); };
    } } };
  const transport = { async respond(request) {
    return request.operation === "search:get"
      ? '[{"name":"Bing","alias":"bing","isDefault":true}]'
      : '{"url":"https://www.bing.com/search?q=hello"}';
  } };
  const bridge = {
    async update(id, tab, args) { calls.push(["update", id, tab, plain(args)]); },
    async create(id, args) { calls.push(["create", id, plain(args)]); },
  };
  const realm = { ExtensionUtils: { ExtensionError: Error }, ExtensionAPI: class {}, tabTracker: tracker,
    ChromeUtils: {
      importESModule(uri) {
        if (uri.includes("Messaging")) return { EventDispatcher: { byName: () => ({
          sendRequestForResult: (_event, request) => {
            calls.push(["profile", plain(request)]); return transport.respond(request);
          },
        }) } };
        if (uri.includes("ExtensionUtils")) return { ExtensionError: Error };
        if (uri.includes("ExtensionProfile")) return { NavisAndroidExtensionProfile: realm.profile };
        if (uri.includes("WebExtensionHost")) return { NavisAndroidExtensionTabBridge: bridge };
        if (uri.includes("PrivateBrowsingUtils")) return { PrivateBrowsingUtils: { isBrowserPrivate: browser => !!browser.private } };
        throw Error(`Unexpected module ${uri}`);
      },
      defineESModuleGetters(target, entries) {
        for (const [key, uri] of Object.entries(entries)) {
          Object.defineProperty(target, key, { configurable: true, get: () => realm.ChromeUtils.importESModule(uri)[key] });
        }
      },
    },
    Services: { scriptloader: { loadSubScript(uri) {
      assert.equal(uri, "chrome://geckoview/content/ext-find-gecko.js");
      vm.runInContext(read("gecko/browser/components/extensions/parent/ext-find.js"), realm);
    } } },
  };
  vm.createContext(realm);
  // ES module bindings stay private to their module, unlike parent API subscript globals.
  vm.runInContext("{" + read("gecko/mobile/shared/modules/navis/NavisAndroidExtensionProfile.sys.mjs")
    .replace("export const NavisAndroidExtensionProfile", "this.profile") + "}", realm);
  function load(name) {
    vm.runInContext(read(`${component}ext-${name}.js`), realm);
    const instance = new realm[name](); instance.extension = extension;
    return instance.getAPI(context)[name];
  }
  function actor(count) {
    return {
      async sendQuery(message, params) {
        calls.push([message, plain(params)]);
        return message.endsWith("CollectResults")
          ? { count, rangeData: [{ text: params.queryphrase }], rectData: [{ rectsAndTexts: [] }] }
          : "Success";
      },
      sendAsyncMessage(message, params) { calls.push([message, plain(params)]); },
    };
  }
  function tab(id, count = 2) {
    const main = actor(count);
    const browser = { navisNativePresentation: false, contentPrincipal: { isSystemPrincipal: false },
      currentURI: { scheme: "https", spec: "https://example.test/" },
      browsingContext: { children: [], currentWindowGlobal: { getActor: name => {
        assert.equal(name, "ExtFind"); return main;
      } } } };
    const result = { id, browser, linkedBrowser: browser };
    tabs.set(id, result); tracker.activeTab ??= result;
    return { tab: result, browser, actor: main };
  }
  return { realm, tabs, tracker, context, extension, permissions, calls, transport, bridge, load, actor, tab };
}

test("search uses live product lookup and distinct current/new/default routes", async () => {
  const f = environment(); f.tab(7); const api = f.load("search");
  assert.equal((await api.get())[0].alias, "bing");
  await api.query({ text: "hello" });
  assert.deepEqual(f.calls.at(-1), ["update", "focused@test", 7, { url: "https://www.bing.com/search?q=hello" }]);
  await api.search({ query: "hello", engine: "Bing" });
  assert.deepEqual(f.calls.at(-1), ["create", "focused@test", { url: "https://www.bing.com/search?q=hello", active: true }]);
  assert.equal(JSON.parse(f.calls.at(-2)[1].arguments).engine, "Bing");
  await api.search({ query: "hello", tabId: 7 });
  assert.equal(f.calls.at(-1)[0], "update");
  await assert.rejects(api.query({ text: "hello", disposition: "NEW_WINDOW" }), /windows backend pending/);
  assert.deepEqual(f.calls.at(-1), ["window", { url: "https://www.bing.com/search?q=hello", incognito: false }]);
});

test("search validates permission, query, URL and target without navigation on rejection", async () => {
  const f = environment(); const { tab } = f.tab(7); const api = f.load("search");
  await assert.rejects(api.query({ text: "hello", tabId: 7, disposition: "NEW_TAB" }));
  await assert.rejects(api.query({ text: " " }));
  await assert.rejects(api.query({ text: "x".repeat(8193) }));
  await assert.rejects(api.query({ text: "x", tabId: 42 }));
  tab.private = true;
  await assert.rejects(api.query({ text: "x" }));
  tab.private = false; f.permissions.delete("search");
  assert.throws(() => api.get());
  assert.equal(f.calls.length, 0);
  f.permissions.add("search");
  f.transport.respond = async () => '{"url":"chrome://navis/content/main.xhtml"}';
  await assert.rejects(api.query({ text: "x" }), /Invalid search URL/);
  assert.equal(f.calls.filter(call => call[0] !== "profile").length, 0);
});

test("delayed search retains its chosen tab and drops revoked/closed requests", async () => {
  for (const invalidate of [f => f.tabs.delete(7), f => f.permissions.delete("search"),
    f => { f.context.unloaded = true; }, f => { f.extension.hasShutdown = true; }]) {
    const f = environment(); f.tab(7); const other = f.tab(8); const api = f.load("search");
    let reply; f.transport.respond = () => new Promise(resolve => { reply = resolve; });
    const pending = api.query({ text: "hello" });
    f.tracker.activeTab = other.tab; invalidate(f);
    reply('{"url":"https://www.bing.com/search?q=hello"}');
    await assert.rejects(pending);
    assert.equal(f.calls.filter(call => call[0] !== "profile").length, 0);
  }
  const f = environment(); f.tab(7); const other = f.tab(8); const api = f.load("search");
  let reply; f.transport.respond = () => new Promise(resolve => { reply = resolve; });
  const pending = api.query({ text: "hello" }); f.tracker.activeTab = other.tab;
  reply('{"url":"https://www.bing.com/search?q=hello"}'); await pending;
  assert.equal(f.calls.at(-1)[2], 7);
});

test("find executes Gecko's actual frame merge, highlight and clear algorithm", async () => {
  const f = environment(); const { browser } = f.tab(7); const child = f.actor(1);
  browser.browsingContext.children.push({ children: [], currentWindowGlobal: { getActor: () => child } });
  const api = f.load("find");
  const result = await api.find("needle", { includeRangeData: true, includeRectData: true, entireWord: true });
  assert.equal(result.count, 3);
  assert.deepEqual(plain(result.rangeData.map(item => item.framePos)), [0, 1]);
  assert.equal(result.rectData.length, 2);
  assert.equal(f.calls.filter(call => call[0] === "ext-Finder:CollectResults").length, 2);
  await api.highlightResults({ rangeIndex: 2, noScroll: true });
  assert.ok(f.calls.some(call => call[0] === "ext-Finder:HighlightResults" && call[1].rangeIndex === 0));
  await assert.rejects(api.highlightResults({ rangeIndex: 9 }), error => /out of range/.test(error.message));
  await api.removeHighlighting();
  assert.equal(f.calls.filter(call => call[0] === "ext-Finder:ClearHighlighting").length, 5);
});

test("find rejects native pages, private denial, privileged documents and invalid targets", async () => {
  const f = environment(); const { tab, browser } = f.tab(7); const api = f.load("find");
  browser.navisNativePresentation = true;
  await assert.rejects(api.find("x")); await assert.rejects(api.removeHighlighting());
  delete browser.navisNativePresentation; await assert.rejects(api.find("x"));
  browser.navisNativePresentation = false; tab.private = true; await assert.rejects(api.find("x"));
  tab.private = false; browser.contentPrincipal.isSystemPrincipal = true; await assert.rejects(api.find("x"));
  browser.contentPrincipal.isSystemPrincipal = false; f.permissions.clear(); await assert.rejects(api.find("x"));
  await assert.rejects(api.find("x", { tabId: 42 }));
  assert.equal(f.calls.length, 0);
});

test("serialized find rejects old-document completion before searching the new document", async () => {
  const f = environment(); const { browser, actor } = f.tab(7); const api = f.load("find");
  let reply; actor.sendQuery = () => new Promise(resolve => { reply = resolve; });
  const old = api.find("old"); const rejected = assert.rejects(old, /accessible web document/); await tick();
  const replacement = f.actor(5);
  browser.browsingContext.currentWindowGlobal = { getActor: () => replacement };
  const next = api.find("new");
  assert.equal(f.calls.length, 0);
  reply({ count: 1 }); await rejected;
  assert.equal((await next).count, 5);
  await api.highlightResults({});
  assert.equal(f.calls[0][1].queryphrase, "new");
});

test("find drops results when permission or native presentation changes while actors reply", async () => {
  for (const invalidate of [f => f.permissions.clear(), f => { f.context.unloaded = true; },
    (f, browser) => { browser.navisNativePresentation = true; }]) {
    const f = environment(); const { browser, actor } = f.tab(7); const api = f.load("find");
    let reply; actor.sendQuery = () => new Promise(resolve => { reply = resolve; });
    const pending = api.find("secret"); const rejected = assert.rejects(pending); await tick();
    invalidate(f, browser); reply({ count: 8 }); await rejected;
  }
});
