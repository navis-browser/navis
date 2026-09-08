import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { getNavisInternalPage, resolveNavisInternalPageURI } from "../../runtime/embedder/modules/DesktopInternalPages.sys.mjs";
import { renderNavisInternalPage } from "../../platform/gecko-chrome/chrome/content/internal-pages.mjs";

// Execute the shipped script and actors with event/state stubs only.
const diagnostics = { application: [], engine: [], graphics: [], media: [], network: [], capabilities: [], system: [] };
for (const locale of ["en-US", "zh-CN"]) {
  for (const id of ["newtab", "history", "bookmarks", "passwords", "downloads", "extensions", "profiles", "processes", "credits", "support", "urls"]) {
    const html = renderNavisInternalPage({ page: getNavisInternalPage(id), pages: [], diagnostics, locale, nonce: "appearance-test" });
    const source = [...html.matchAll(/<script nonce="appearance-test">([\s\S]*?)<\/script>/g)]
      .map(match => match[1]).find(script => script.includes('window.addEventListener("NavisAppearanceState"'));
    assert.ok(source, `${id} receives the shared appearance handler`);
    const listeners = new Map(), requests = [], properties = new Map();
    const root = { dataset: {}, style: { setProperty: (key, value) => properties.set(key, value) } };
    vm.runInNewContext(source, {
      document: { documentElement: root, dispatchEvent: event => requests.push(event) },
      window: { addEventListener: (type, listener) => listeners.set(type, listener) },
      CustomEvent: class { constructor(type, options) { Object.assign(this, { type }, options); } },
    });
    listeners.get("pageshow")();
    assert.equal(requests[0].type, "NavisAppearanceCommand");
    assert.equal(requests[0].detail.command, "appearance:get");
    for (const theme of ["dark", "light", "system"]) {
      listeners.get("NavisAppearanceState")({ detail: { appearance: { theme, accent: "#123456" } } });
      assert.equal(root.dataset.theme, theme);
      assert.equal(root.style.colorScheme, theme === "system" ? "light dark" : theme);
      assert.equal(properties.get("--accent"), "#123456");
      assert.equal(properties.get("--focus"), "#123456");
    }
    listeners.get("NavisAppearanceState")({ detail: { appearance: { theme: "dark", accent: "url(invalid)" } } });
    assert.equal(root.dataset.theme, "system");
    assert.equal(properties.get("--accent"), "#123456");
  }
}

const actorSource = readFileSync(new URL("../../runtime/embedder/components/DesktopInternalPageChild.sys.mjs", import.meta.url), "utf8")
  .replace(/^import .*;\n/gm, "").replace("export class DesktopInternalPageChild", "class DesktopInternalPageChild") +
  "\nglobalThis.Actor = DesktopInternalPageChild;";
const context = { JSWindowActorChild: class {}, resolveNavisInternalPageURI, Cu: { waiveXrays: value => value, cloneInto: value => structuredClone(value) } };
vm.runInNewContext(actorSource, context);
const actor = new context.Actor(), requests = [], responses = [], document = {};
const route = id => {
  const page = getNavisInternalPage(id);
  document.documentURIObject = { schemeIs: scheme => scheme === "navis", asciiHost: id,
    hasUserPass: false, port: -1, hasQuery: false, filePath: page.path, specIgnoringRef: page.url };
};
actor.contentWindow = { document, CustomEvent: class { constructor(type, { detail }) { Object.assign(this, { type, detail }); } },
  dispatchEvent: event => responses.push(event) };
actor.sendQuery = (_topic, data) => new Promise(resolve => requests.push({ data, resolve }));
const command = (value = undefined) => actor.handleEvent({ target: document, type: "NavisAppearanceCommand", detail: { command: "appearance:get", value } });
const settle = () => new Promise(resolve => setImmediate(resolve));
route("history");
command();
assert.equal(requests.length, 1);
actor.receiveMessage({ name: "DesktopInternalPage:AppearanceChanged", data: { pageKey: "history", appearance: { accent: "#abcdef", theme: "dark" } } });
requests[0].resolve({ pageKey: "history", appearance: { accent: "#123456", theme: "light" } });
await settle();
assert.equal(responses.length, 1, "An old initial read cannot overwrite a newer push");
assert.equal(responses[0].detail.appearance.accent, "#abcdef");
command();
route("downloads");
requests[1].resolve({ pageKey: "history", appearance: { accent: "#123456", theme: "light" } });
actor.receiveMessage({ name: "DesktopInternalPage:AppearanceChanged", data: { pageKey: "history", appearance: {} } });
await settle();
assert.equal(responses.length, 1, "Appearance replies and pushes remain bound to their original route");
command({ arbitrary: true });
assert.equal(requests.length, 2, "Appearance reads accept no mutation payload");
route("settings");
command();
assert.equal(requests.length, 2, "Settings retains its existing request/draft ownership");
document.documentURIObject.schemeIs = () => false;
command();
actor.receiveMessage({ name: "DesktopInternalPage:AppearanceChanged", data: { pageKey: "settings", appearance: {} } });
assert.equal(requests.length, 2);
assert.equal(responses.length, 1, "Ordinary pages cannot use the internal appearance bridge");
route("profiles");
actor.handleEvent({ target: document, type: "NavisManagementCommand",
  detail: { command: "profile:update", value: { userName: "Updated", accentColor: "#123456" } } });
const mutation = requests.at(-1);
actor.receiveMessage({ name: "DesktopInternalPage:ProfileChanged", data: { pageKey: "profiles" } });
assert.equal(responses.at(-1).type, "NavisProfileChanged");
mutation.resolve({ pageKey: "profiles", outcome: "ready" });
await settle();
assert.equal(responses.at(-1).type, "NavisManagementState", "Profile notifications do not supersede mutation replies");
const responseCount = responses.length;
route("history");
actor.receiveMessage({ name: "DesktopInternalPage:ProfileChanged", data: { pageKey: "profiles" } });
assert.equal(responses.length, responseCount, "Profile changes only reach the profiles route");

const engine = readFileSync(new URL("../../runtime/embedder/modules/DesktopEngine.sys.mjs", import.meta.url), "utf8");
const observer = engine.slice(engine.indexOf("    this.#appearanceObserver = () => {"), engine.indexOf('    Services.prefs.addObserver("navis.appearance."'));
const profileObserver = engine.slice(engine.indexOf("    this.#profileObserver = () => {"), engine.indexOf('    Services.obs.addObserver(this.#profileObserver'));
const sent = [], delegated = [];
const sessions = ["history", "downloads", "profiles", "settings", "external", "gone"].map(key => ({ browser: {
  page: key === "external" ? null : { id: key, key },
  browsingContext: { currentWindowGlobal: { getActor() {
    if (key === "gone") throw new Error("Actor destroyed during navigation");
    return { sendAsyncMessage: (type, data) => sent.push({ key, type, data }) };
  } } },
} }));
const observerContext = { sessions, GET_BROWSER: Symbol(), authenticatedInternalPage: browser => browser.page,
  callDelegate: (...args) => delegated.push(args) };
for (const session of sessions) session[observerContext.GET_BROWSER] = () => session.browser;
vm.runInNewContext(`class Runtime {
  #sessions = new Set(sessions); #appearanceDelegates = new Set([{}]); #appearanceObserver; #profileObserver;
  appearanceSettings = { theme: "dark", accent: "#abcdef" };
  constructor() { ${observer} ${profileObserver} }
  notify() { this.#appearanceObserver(); }
  notifyProfile() { this.#profileObserver(); }
} this.runtime = new Runtime(); runtime.notify();`, observerContext);
assert.deepEqual(sent.map(item => item.key), ["history", "downloads", "profiles"]);
assert.equal(delegated.length, 1, "Shell appearance delivery remains active");
assert(sent.every(item => item.type === "DesktopInternalPage:AppearanceChanged" && item.data.pageKey === item.key));
sent.length = 0;
observerContext.runtime.notifyProfile();
assert.deepEqual(sent.map(item => item.key), ["profiles"]);
assert.equal(sent[0].type, "DesktopInternalPage:ProfileChanged");
console.log("PASS standalone internal appearance: all pages/locales, initial read, live push, stale response and authenticated route boundaries; no browser or UI inspection");
