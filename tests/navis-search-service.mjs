import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import vm from "node:vm";
import { classifyAddressInput } from "../embedder/modules/DesktopAddressInput.sys.mjs";
import { rankLocalSuggestions } from "../embedder/modules/NavisSuggestionPolicy.sys.mjs";

// Execute the retained upstream JS, with only Gecko host primitives replaced.
// Never start a product process, access a real Profile, or perform network I/O.
const root = new URL("../", import.meta.url);
const patch = readFileSync(new URL("patches/gecko/0107-restore-offline-navis-search-service.patch", root), "utf8");
function source(path) {
  let text = readFileSync(new URL(`gecko/${path}`, root), "utf8");
  const section = patch.split(`diff --git a/${path} b/${path}\n`)[1]?.split("diff --git ")[0];
  for (const hunk of section?.split(/^@@ .* @@.*\n/m).slice(1) ?? []) {
    const lines = hunk.trimEnd().split("\n");
    const before = lines.filter(line => /^[ -]/.test(line)).map(line => line.slice(1)).join("\n");
    const after = lines.filter(line => /^[ +]/.test(line)).map(line => line.slice(1)).join("\n");
    if (text.includes(before)) text = text.replace(before, after);
    else assert.ok(text.includes(after), `Semantic port hunk does not apply: ${path}`);
  }
  return text;
}

function environment(saved, { fixtureResponse = false } = {}) {
  let disk = saved;
  let writes = 0;
  let networkCalls = 0;
  let failWrite = false;
  const observers = new Map();
  const cache = new Map();
  const prefs = new Map();
  const defaultPrefs = new Map();
  const appConstants = { MOZ_NAVIS_CORE: true, MOZ_DESKTOP_EMBEDDER: true };
  const uri = spec => {
    const value = new URL(spec);
    return {
      spec, host: value.hostname, asciiHost: value.hostname,
      scheme: value.protocol.slice(0, -1), query: value.search.slice(1),
      pathQueryRef: value.pathname + value.search + value.hash,
      schemeIs: scheme => value.protocol === `${scheme}:`,
      QueryInterface() { return this; },
      mutate() { let current = new URL(spec); return {
        setQuery(query) { current.search = query; return this; },
        setRef(ref) { current.hash = ref; return this; },
        finalize() { return uri(current.href); },
      }; },
    };
  };
  const preferenceBranch = (defaultsOnly = false) => new Proxy({}, { get(_target, key) {
      if (key === "getDefaultBranch") return () => preferenceBranch(true);
      if (key === "getBranch") return () => preferenceBranch(defaultsOnly);
      if (key === "addObserver" || key === "removeObserver") return () => {};
      if (key === "getChildList") return () => [];
      if (key.startsWith("get")) return (name, fallback) =>
        (defaultsOnly ? undefined : prefs.get(name)) ?? defaultPrefs.get(name) ?? fallback;
      if (key.startsWith("set")) return (name, value) => (defaultsOnly ? defaultPrefs : prefs).set(name, value);
      throw new Error(`Unexpected preference API ${key}`);
    } });
  const Services = {
    prefs: preferenceBranch(),
    obs: {
      addObserver(observer, topic) { if (!observers.has(topic)) observers.set(topic, new Set()); observers.get(topic).add(observer); },
      removeObserver(observer, topic) { observers.get(topic)?.delete(observer); },
      notifyObservers(subject, topic, verb) { for (const observer of observers.get(topic) ?? []) observer.observe(subject, topic, verb); },
    },
    io: { newURI: uri },
    textToSubURI: { ConvertAndEscape: (_charset, value) => encodeURIComponent(value).replaceAll("%20", "+") },
    uuid: { generateUUID: () => `{${randomUUID()}}` },
    locale: { appLocaleAsBCP47: "en-US" },
    appinfo: { name: "Navis", version: "1.0", OS: "Linux" },
    env: { exists: () => false },
    startup: { shuttingDown: false },
    tm: { dispatchToMainThread: callback => queueMicrotask(callback) },
  };
  const inert = new Proxy(() => 0, { get: () => inert, apply: () => 0 });
  const Ci = new Proxy({}, { get: (_target, key) => ({
    nsIEnterprisePolicies: { ACTIVE: 1 },
    nsIContentPolicy: { TYPE_OTHER: 1 },
    nsIChannel: { LOAD_ANONYMOUS: 1, INHIBIT_PERSISTENT_CACHING: 2 },
  })[key] ?? key });
  const Cc = new Proxy({}, { get: (_target, key) => ({ createInstance() {
    if (key === "@mozilla.org/security/hash;1") {
      let hash;
      return { SHA256: 1, init: () => { hash = createHash("sha256"); }, update: value => hash.update(value), finish: () => hash.digest("base64") };
    }
    if (key === "@mozilla.org/timer;1") return { initWithCallback() {}, cancel() {} };
    throw new Error(`Unexpected XPCOM dependency: ${key}`);
  } }) });
  const host = {
    Services, Ci, Cc, Cr: { NS_OK: 0 },
    ChromeUtils: { generateQI: () => function () { return this; }, importESModule: load,
      defineLazyGetter: (target, key, getter) => Object.defineProperty(target, key, { get: getter }) },
    Components: { Constructor: function () { return function () {}; },
      Exception: function (message) { return new Error(message); } },
    Glean: inert, URL: class extends URL { get URI() { return uri(this.href); } }, URLSearchParams, TextEncoder, structuredClone,
    DOMException: Object.assign(DOMException, { isInstance: value => value instanceof DOMException }),
    PathUtils: { profileDir: "/in-memory-profile", join: (...parts) => parts.join("/"), filename: path => path.split("/").pop() },
    IOUtils: {
      async readJSON() { if (!disk) throw new DOMException("No in-memory settings", "NotFoundError"); return structuredClone(disk); },
      async writeJSON(_path, value) {
        if (failWrite) { failWrite = false; throw new Error("Injected in-memory write failure"); }
        disk = structuredClone(value); writes++;
      },
      profileBeforeChange: { addBlocker() {} },
    },
    console: { ...console, createInstance: () => ({ debug() {}, log() {}, warn() {},
      error(...args) {
        if (!args.some(value => value?.message === "Injected in-memory write failure")) console.error(...args);
      },
    }) },
    XMLHttpRequest: class {
      constructor() {
        networkCalls++;
        if (!fixtureResponse) throw new Error("Network forbidden in contract tests");
        this.channel = { loadFlags: 0 };
        this.listeners = new Map();
      }
      open(method, url) { assert.equal(method, "GET"); this.url = new URL(url); }
      setOriginAttributes(value) { assert.equal(value.privateBrowsingId, 0); }
      addEventListener(type, callback) { this.listeners.set(type, callback); }
      abort() { this.listeners.get("abort")?.(); }
      send() {
        assert.equal(this.channel.loadFlags, 3, "Remote transport remains anonymous and non-persistent");
        const query = this.url.searchParams.get("query");
        assert.equal(query, "navis fixture", "Only a synthetic Bing request reaches this in-memory transport");
        this.status = 200;
        this.readyState = 4;
        this.response = [query, ["navis synthetic alpha", "navis synthetic beta"]];
        queueMicrotask(() => this.listeners.get("load")());
      }
    },
  };
  const XPCOMUtils = { defineLazyPreferenceGetter(target, key, pref, fallback) {
    Object.defineProperty(target, key, { get: () => Services.prefs.getBoolPref(pref, fallback) });
  }, declareLazy(definitions) {
    const result = {};
    for (const [key, definition] of Object.entries(definitions)) Object.defineProperty(result, key, {
      configurable: true,
      get() {
        if (definition.pref) return Services.prefs.getBoolPref(definition.pref, definition.default);
        const value = typeof definition === "string" ? load(definition)[key]
          : typeof definition === "function" ? definition()
          : (() => { throw new Error(`Unexpected lazy service ${key}`); })();
        Object.defineProperty(result, key, { value, configurable: true, writable: true });
        return value;
      },
    });
    return result;
  } };
  function load(specifier) {
    if (cache.has(specifier)) return cache.get(specifier);
    if (specifier.endsWith("/XPCOMUtils.sys.mjs")) return { XPCOMUtils };
    if (specifier.endsWith("/AppConstants.sys.mjs")) return { AppConstants: appConstants };
    if (specifier.endsWith("/ObjectUtils.sys.mjs")) return { ObjectUtils: { deepEqual: isDeepStrictEqual } };
    if (specifier.endsWith("/DeferredTask.sys.mjs")) return { DeferredTask: class {
      constructor(task) { this.task = task; }
      arm() {} disarm() {} async finalize() { await this.task(); }
    } };
    let text;
    if (specifier.startsWith("moz-src:///toolkit/components/search/")) {
      text = source(specifier.slice("moz-src:///".length));
    } else if (/^resource:\/\/gre\/modules\/NavisSearch(?:Service|Policy)\.sys\.mjs$/.test(specifier)) {
      text = readFileSync(new URL(`embedder/modules/${specifier.split("/").pop()}`, root), "utf8");
    } else throw new Error(`Unexpected product dependency: ${specifier}`);
    const names = [...text.matchAll(/^export (?:const|var|class|function) (\w+)/gm)].map(match => match[1]);
    text = text.replace(/^import\s*\{([\s\S]*?)\}\s*from\s*"([^"]+)";/gm,
      (_match, imports, target) => `const {${imports}} = __import(${JSON.stringify(target)});`)
      .replace(/^export (const|var|class|function) /gm, "$1 ");
    const context = vm.createContext({ ...host, __import: load });
    const result = new vm.Script(`${text}\n;({${names.join(",")}})`, { filename: specifier }).runInContext(context);
    cache.set(specifier, result);
    return result;
  }
  const { SearchService } = load("moz-src:///toolkit/components/search/SearchService.sys.mjs");
  const { NavisSearchAdapter } = load("resource://gre/modules/NavisSearchService.sys.mjs");
  const { NAVIS_SEARCH_CATALOGUE, isNavisSuggestionQuery } = load("resource://gre/modules/NavisSearchPolicy.sys.mjs");
  let requests = [];
  const adapter = new NavisSearchAdapter({ searchService: SearchService, createSuggestionController: () => {
    let resolve;
    const request = { stopped: 0, options: null, stop() { this.stopped++; },
      fetch(options) { this.options = options; return new Promise(value => { resolve = value; }); },
      complete(values) { resolve({ remote: values.map(value => ({ value })) }); } };
    requests.push(request);
    return request;
  } });
  return { adapter, SearchService, Services, prefs, defaultPrefs, appConstants, NAVIS_SEARCH_CATALOGUE, isNavisSuggestionQuery, requests,
    saved: () => structuredClone(disk), writes: () => writes, networkCalls: () => networkCalls,
    failNextWrite: () => { failWrite = true; },
    flush: () => SearchService._settings._write(), load, observers };
}

const env = environment();
const { adapter, NAVIS_SEARCH_CATALOGUE: catalogue } = env;
const baiduEndpoint = "https://www.baidu.com/su?ie=utf-8&action=opensearch&wd={searchTerms}";
assert.equal(catalogue.find(provider => provider.id === "baidu").suggestionTemplate, baiduEndpoint);

// Reproduce the already-imported pre-fix store, not a reset of legacy settings.
const oldProfile = environment();
await oldProfile.adapter.init({ providers: [...catalogue].reverse().map(provider => ({ ...provider,
  ...(provider.id === "baidu" ? { name: "我的百度", suggestionTemplate: "" } : {}),
})), defaultProviderId: "bing", remoteSuggestionsEnabled: true });
const oldDisk = oldProfile.saved();
delete oldDisk.metaData["navis-baidu-suggestions-version"];
const oldSnapshot = JSON.parse(JSON.stringify(oldProfile.adapter.snapshot));
const upgraded = environment(oldDisk);
await upgraded.adapter.init({ providers: catalogue, defaultProviderId: "google", remoteSuggestionsEnabled: false });
const expectedUpgrade = structuredClone(oldSnapshot);
expectedUpgrade.providers.find(provider => provider.id === "baidu").suggestionTemplate = baiduEndpoint;
assert.deepEqual(JSON.parse(JSON.stringify(upgraded.adapter.snapshot)), expectedUpgrade,
  "Only the omitted Baidu endpoint changes; names/order/default/consent survive");
assert.equal(upgraded.saved().metaData["navis-baidu-suggestions-version"], 1);
assert.equal(upgraded.saved().metaData["navis-import-version"], 1);
const baiduEngine = (await upgraded.SearchService.getVisibleEngines()).find(engine => engine.getAttr("navis-provider-id") === "baidu");
assert.equal(baiduEngine.getSubmission("尼泊尔", "application/x-suggestions+json").uri.spec,
  "https://www.baidu.com/su?ie=utf-8&action=opensearch&wd=%E5%B0%BC%E6%B3%8A%E5%B0%94");
await upgraded.adapter.upsert({ ...upgraded.adapter.snapshot.providers.find(provider => provider.id === "baidu"), suggestionTemplate: "" });
const cleared = environment(upgraded.saved());
await cleared.adapter.init();
assert.equal(cleared.adapter.snapshot.providers.find(provider => provider.id === "baidu").suggestionTemplate, "",
  "A deliberate clear after the one-time upgrade survives restart");
assert.equal(cleared.writes(), 0, "Completed endpoint upgrade does not rewrite startup settings");
for (const edit of [
  { suggestionTemplate: "https://custom.example/suggest?q={searchTerms}" },
  { template: "https://custom.example/search?q={searchTerms}" },
]) {
  const fixture = environment();
  await fixture.adapter.init({ providers: catalogue.map(provider => provider.id === "baidu"
    ? { ...provider, suggestionTemplate: "", ...edit } : provider), remoteSuggestionsEnabled: false });
  const saved = fixture.saved(); delete saved.metaData["navis-baidu-suggestions-version"];
  const next = environment(saved); await next.adapter.init();
  assert.equal(JSON.stringify(next.adapter.snapshot), JSON.stringify(fixture.adapter.snapshot),
    "Custom suggestion endpoints/search templates and opt-out remain untouched");
}
const failedUpgrade = environment(oldDisk);
failedUpgrade.failNextWrite();
await assert.rejects(failedUpgrade.adapter.init(), /Injected/);
assert.deepEqual(failedUpgrade.saved(), oldDisk, "Failed atomic upgrade leaves disk unchanged");
assert.equal((await failedUpgrade.SearchService.getVisibleEngines())
  .find(engine => engine.getAttr("navis-provider-id") === "baidu")
  .getURLOfType("application/x-suggestions+json"), null, "Failed upgrade rolls back the live endpoint");
await failedUpgrade.adapter.init();
assert.equal(failedUpgrade.adapter.snapshot.providers.find(provider => provider.id === "baidu").suggestionTemplate, baiduEndpoint,
  "Failed upgrade can retry without resetting the initial provider migration");
console.log("Baidu catalogue + one-time Profile endpoint upgrade: persistence, identity/order/default/consent, custom values, later clears and failure retry passed");
assert.throws(() => adapter.snapshot, /not ready/);
const legacy = [...catalogue].reverse().map(provider => ({ ...provider, suggestionTemplate: undefined }));
legacy[0] = { ...legacy[0], name: "My search", template: "https://edited.example/?q={searchTerms}" };
legacy.push({ id: "custom-1", name: "Google", template: "https://custom.example/?term={searchTerms}" });
await adapter.init({ providers: legacy, defaultProviderId: "custom-1" });
assert.equal(adapter.snapshot.defaultProviderId, "custom-1");
assert.deepEqual(Array.from(adapter.snapshot.providers, provider => provider.id), legacy.map(provider => provider.id));
assert.equal(adapter.snapshot.providers[0].suggestionTemplate, "");
assert.equal(adapter.snapshot.providers.at(-1).name, "Google", "Duplicate display names are preserved");
assert.ok(Object.isFrozen(adapter.snapshot) && Object.isFrozen(adapter.snapshot.providers[0]));
assert.equal(adapter.submission("a & 中").url, "https://custom.example/?term=a+%26+%E4%B8%AD");
assert.equal(adapter.submission("private", { privateMode: true }).url, "https://custom.example/?term=private");
await adapter.suggestions("plain query");
assert.equal(env.requests.length, 0, "No controller before explicit consent");
const before = JSON.stringify(adapter.snapshot);
await assert.rejects(adapter.remove("google"), /cannot be removed/);
await assert.rejects(adapter.remove("missing"), /Unknown/);
await assert.rejects(adapter.move("google", -1), /Invalid/);
await assert.rejects(adapter.setDefault("missing"), /Unknown/);
assert.throws(() => adapter.upsert({ id: "bad", name: "Bad", template: "javascript:{searchTerms}" }), /HTTP/);
assert.throws(() => adapter.setRemoteSuggestionsEnabled("true"), /boolean/);
assert.equal(JSON.stringify(adapter.snapshot), before, "Rejected operations do not mutate state");
await adapter.setRemoteSuggestionsEnabled(true);
for (const query of ["https://example.org/private", "file:///secrets", "/private/path", "C:\\private", "\\\\server\\share", "localhost", "127.0.0.1", "example.com", "me@example.org"]) {
  assert.equal(env.isNavisSuggestionQuery(query), false, query);
  await adapter.suggestions(query);
}
await adapter.suggestions("private terms", { privateMode: true });
await adapter.suggestions("plain query", { providerId: "custom-1" });
assert.equal(env.requests.length, 0, "Private/address/path/unconfigured requests never create a controller");
const signal = new AbortController();
const pending = adapter.suggestions("normal query", { providerId: "google", signal: signal.signal });
await Promise.resolve();
assert.equal(env.requests.length, 1);
assert.equal(env.requests[0].options.maxLocalResults, 0, "FormHistory is not imported");
signal.abort();
env.requests[0].complete(["late"]);
assert.equal((await pending).length, 0);
const allowed = adapter.suggestions("normal query", { providerId: "google" });
await Promise.resolve();
env.requests.at(-1).complete(["one", "one", "two", "bad\u0000value"]);
assert.deepEqual(Array.from(await allowed), ["one", "two"]);
const stale = adapter.suggestions("normal query", { providerId: "google" });
await Promise.resolve();
await adapter.setRemoteSuggestionsEnabled(false);
env.requests.at(-1).complete(["stale"]);
assert.equal((await stale).length, 0);
await adapter.upsert({ id: "custom-1", name: "Edited", template: "https://edited.example/?q={searchTerms}", suggestionTemplate: "" });
await adapter.move("custom-1", 0);
await env.flush();
assert.ok(env.writes() >= 5, "Mutations await Gecko's sole settings writer");
const restarted = environment(env.saved());
await restarted.adapter.init({ providers: catalogue, defaultProviderId: "bing", remoteSuggestionsEnabled: true });
assert.equal(restarted.adapter.snapshot.defaultProviderId, "custom-1", "Migration runs once");
assert.equal(restarted.adapter.snapshot.providers[0].name, "Edited");
assert.equal(restarted.adapter.snapshot.remoteSuggestionsEnabled, false);
for (const mutate of [
  () => restarted.adapter.upsert({ id: "custom-1", name: "Failed edit", template: "https://failed.example/?q={searchTerms}" }),
  () => restarted.adapter.upsert({ id: "failed-new", name: "Failed add", template: "https://failed.example/?q={searchTerms}" }),
  () => restarted.adapter.setDefault("bing"),
  () => restarted.adapter.move("google", 0),
  () => restarted.adapter.setRemoteSuggestionsEnabled(true),
  () => restarted.adapter.remove("custom-1"),
]) {
  const previousState = JSON.stringify(restarted.adapter.snapshot);
  const previousDisk = JSON.stringify(restarted.saved());
  restarted.failNextWrite();
  await assert.rejects(mutate(), /Injected/);
  assert.equal(JSON.stringify(restarted.adapter.snapshot), previousState, "Write failure rolls back the public state");
  assert.equal(JSON.stringify(restarted.saved()), previousDisk, "Atomic write failure preserves durable state");
}
const native = restarted.SearchService.defaultEngine;
const previousName = native.name;
const previousURL = native.getURLOfType("text/html").template;
assert.throws(() => native.updateWithFormInfo({ name: "Incomplete edit", url: "https://new.example/?q={searchTerms}", suggestUrl: "javascript:{searchTerms}" }), /invalid scheme/);
assert.equal(native.name, previousName, "All new URLs are validated before changing the engine name");
assert.equal(native.getURLOfType("text/html").template, previousURL);
await restarted.adapter.remove("custom-1");
assert.equal(restarted.adapter.snapshot.defaultProviderId, "google", "Gecko owns default fallback");
await restarted.flush();
assert.equal(restarted.saved().engines.length, 4);
assert.equal(restarted.networkCalls(), 0);
assert.ok(!env.observers.has("intl:app-locales-changed"));
// Also exercise the retained controller's own guard: callers cannot bypass the
// Profile consent gate by importing Gecko's controller directly.
const direct = restarted.load("resource://gre/modules/NavisSearchService.sys.mjs").NavisSearchService;
restarted.prefs.set("browser.search.suggest.timeout", 9000);
await direct.init();
assert.equal(restarted.defaultPrefs.get("browser.search.suggest.timeout"), 2000);
assert.equal(restarted.prefs.get("browser.search.suggest.timeout"), 9000,
  "Shared cold-connection budget must not replace an explicit user preference");
assert.equal(restarted.Services.prefs.getIntPref("browser.search.suggest.timeout"), 9000);
const { SearchSuggestionController } = restarted.load("moz-src:///toolkit/components/search/SearchSuggestionController.sys.mjs");
const controller = new SearchSuggestionController();
const engine = restarted.SearchService.defaultEngine;
assert.equal(await controller.fetch({ searchString: "ordinary query", engine, inPrivateBrowsing: false }), null);
await direct.setRemoteSuggestionsEnabled(true);
assert.equal(await controller.fetch({ searchString: "private query", engine, inPrivateBrowsing: true }), null);
assert.equal(await controller.fetch({ searchString: "file:///private", engine, inPrivateBrowsing: false }), null);
assert.equal(restarted.networkCalls(), 0);
// The actual Android bridge must reach the retained controller and return rows
// under GeckoView's false default. Only XHR/Gecko host primitives are simulated.
const android = environment(undefined, { fixtureResponse: true });
assert.match(readFileSync(new URL("gecko/mobile/android/app/geckoview-prefs.js", root), "utf8"),
  /pref\("browser\.search\.suggest\.enabled", false\)/);
android.defaultPrefs.set("browser.search.suggest.enabled", false);
const androidService = android.load("resource://gre/modules/NavisSearchService.sys.mjs").NavisSearchService;
const androidSource = readFileSync(new URL("embedder/modules/NavisAndroidSearch.sys.mjs", root), "utf8")
  .replace(/^import .*;\n/gmu, "").replaceAll("export async function", "async function");
const androidBridge = new Function("NavisSearchService", "classifyAddressInput", "rankLocalSuggestions", "PrivateBrowsingUtils",
  androidSource + "\nreturn {initializeAndroidSearch, queryAndroidSearch};")(
  androidService, classifyAddressInput, rankLocalSuggestions, { isBrowserPrivate: browser => browser.privateMode });
await androidBridge.initializeAndroidSearch({ providers: catalogue, defaultProviderId: "bing" });
const androidBrowser = { privateMode: false, browsingContext: { currentWindowGlobal: {} } };
const androidWindow = Object.assign(new EventTarget(), { tab: { linkedBrowser: androidBrowser }, closed: false });
const androidSuggest = (query = "navis fixture") => androidBridge.queryAndroidSearch(androidWindow, "search:suggest",
  { query, requestId: "android-fixture" });
assert.equal((await androidSuggest()).suggestions.length, 0);
assert.equal(android.networkCalls(), 0, "Android opt-out reaches no remote transport");
await androidBridge.queryAndroidSearch(androidWindow, "search:remote", { enabled: true });
assert.equal(android.Services.prefs.getBoolPref("browser.search.suggest.enabled"), false,
  "Android's legacy default remains false; Core opt-in is the only Navis authority");
assert.deepEqual(Array.from((await androidSuggest()).suggestions), ["navis synthetic alpha", "navis synthetic beta"]);
assert.equal(android.networkCalls(), 1, "Core opt-in reaches real controller despite Android's false legacy default");
androidBrowser.privateMode = true;
assert.equal((await androidSuggest()).suggestions.length, 0);
androidBrowser.privateMode = false;
for (const query of ["", "https://private.example/", "file:///private", "localhost"]) {
  assert.equal((await androidSuggest(query)).suggestions.length, 0);
}
assert.equal(android.networkCalls(), 1, "Private/empty/URL queries still reach no remote transport");
const AndroidController = android.load("moz-src:///toolkit/components/search/SearchSuggestionController.sys.mjs").SearchSuggestionController;
const directAndroid = new AndroidController();
const directAndroidOptions = { searchString: "navis fixture", engine: android.SearchService.defaultEngine,
  inPrivateBrowsing: false, maxLocalResults: 0 };
assert.equal(await directAndroid.fetch({ ...directAndroidOptions, fetchTrending: true }), null);
await androidService.setRemoteSuggestionsEnabled(false);
assert.equal(await directAndroid.fetch(directAndroidOptions), null);
assert.equal(android.networkCalls(), 1, "Direct controller callers cannot bypass Core consent or query guards");
android.appConstants.MOZ_NAVIS_CORE = false;
assert.equal((await directAndroid.fetch(directAndroidOptions)).remote.length, 0);
assert.equal(android.networkCalls(), 1, "Non-Navis retains its false preference gate");
android.defaultPrefs.set("browser.search.suggest.enabled", true);
assert.equal((await directAndroid.fetch(directAndroidOptions)).remote.length, 2);
assert.equal(android.networkCalls(), 2, "Non-Navis retains its true preference behavior");
assert.equal(android.prefs.has("browser.search.suggest.enabled"), false,
  "Core consent never writes a second user setting");
console.log("Android bridge + retained Gecko controller: false legacy default, Core consent, returned rows and non-Navis preference behavior passed");
const { AppProvidedConfigEngine } = restarted.load("moz-src:///toolkit/components/search/ConfigSearchEngine.sys.mjs");
assert.throws(() => new AppProvidedConfigEngine({ config: {} }), /not a Navis capability/);
const retry = environment();
retry.failNextWrite();
await assert.rejects(retry.adapter.init({ providers: legacy, defaultProviderId: "custom-1" }), /Injected/);
await retry.adapter.init();
assert.equal(retry.adapter.snapshot.defaultProviderId, "custom-1", "Migration retry retains its original input");
assert.equal(retry.saved().metaData["navis-import-version"], 1);
const defaultTimeout = environment();
assert.equal(defaultTimeout.defaultPrefs.has("browser.search.suggest.timeout"), false,
  "Importing the injectable adapter must not configure a running backend");
await defaultTimeout.load("resource://gre/modules/NavisSearchService.sys.mjs").NavisSearchService.init();
assert.equal(defaultTimeout.Services.prefs.getIntPref("browser.search.suggest.timeout"), 2000);
assert.equal(defaultTimeout.prefs.has("browser.search.suggest.timeout"), false,
  "The common timeout is a default, not a new persisted user setting");
console.log("Navis SearchService: retained Gecko model/storage/migration/consent/cancellation contracts passed");
