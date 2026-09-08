import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
import {
  NAVIS_BUILTIN_SEARCH_PROVIDERS,
  NAVIS_DEFAULT_ACCENT,
  readSearchProviders,
  readDefaultSearchProvider,
  mutateSearchProviders,
  setDefaultSearchProvider,
  validateSearchProvider,
  readAppearanceSettings,
  setAppearanceSetting,
  readDownloadSettings,
  setDownloadSetting,
} from "../../runtime/embedder/modules/NavisSettingsPolicy.sys.mjs";
import { resolveDesktopAddressInput } from "../../runtime/embedder/modules/DesktopAddressInput.sys.mjs";
import { getNavisInternalPage, resolveNavisInternalPageURI } from "../../runtime/embedder/modules/DesktopInternalPages.sys.mjs";
import { renderNavisInternalPage } from "../../platform/gecko-chrome/chrome/content/internal-pages.mjs";

function preferences() {
  const values = new Map();
  return {
    values,
    getStringPref: (key, fallback) => values.get(key) ?? fallback,
    getBoolPref: (key, fallback) => values.get(key) ?? fallback,
    setStringPref: (key, value) => values.set(key, value),
    setBoolPref: (key, value) => values.set(key, value),
    setIntPref: (key, value) => values.set(key, value),
    clearUserPref: key => values.delete(key),
  };
}

const prefs = preferences();
assert.equal(readSearchProviders(prefs), NAVIS_BUILTIN_SEARCH_PROVIDERS);
const custom = { name: "Example", template: "https://example.com/?q={searchTerms}&other={searchTerms}" };
mutateSearchProviders(prefs, "add", custom, () => "070c723c-3346-4245-bf5c-07ccdd2b85a7");
const id = readSearchProviders(prefs).at(-1).id;
setDefaultSearchProvider(prefs, id);
assert.equal(readDefaultSearchProvider(prefs), id);
assert.equal(resolveDesktopAddressInput("你好 world", id, readSearchProviders(prefs)).url, "https://example.com/?q=%E4%BD%A0%E5%A5%BD%20world&other=%E4%BD%A0%E5%A5%BD%20world");
mutateSearchProviders(prefs, "move", { id, position: 0 });
assert.equal(readSearchProviders(prefs)[0].id, id);
mutateSearchProviders(prefs, "update", { id, name: "My provider", template: custom.template });
assert.equal(readSearchProviders(prefs)[0].name, "My provider");
mutateSearchProviders(prefs, "update", { id: "google", name: "My Google", template: custom.template });
assert.equal(readSearchProviders(prefs).find(item => item.id === "google").builtIn, true);
for (const builtin of NAVIS_BUILTIN_SEARCH_PROVIDERS) {
  const before = new Map(prefs.values);
  assert.throws(() => mutateSearchProviders(prefs, "remove", { id: builtin.id }), /cannot be removed/);
  assert.deepEqual(prefs.values, before);
}
for (const template of ["javascript:alert('{searchTerms}')", "https://{searchTerms}.example.com/", "https://user:pass@example.com/{searchTerms}", "https://example.com/", "data:text/plain,{searchTerms}"]) {
  assert.throws(() => validateSearchProvider({ id, name: "Bad", template }));
}
for (const position of [-1, 64, 1.5, "2"]) {
  const before = new Map(prefs.values);
  assert.throws(() => mutateSearchProviders(prefs, "move", { id, position }));
  assert.deepEqual(prefs.values, before);
}
mutateSearchProviders(prefs, "remove", { id });
assert.equal(readDefaultSearchProvider(prefs), "google");
prefs.setStringPref("navis.search.providers", JSON.stringify([{ ...custom, id }]));
assert.equal(readSearchProviders(prefs), NAVIS_BUILTIN_SEARCH_PROVIDERS);
prefs.setStringPref("navis.search.providers", "broken");
assert.equal(readSearchProviders(prefs), NAVIS_BUILTIN_SEARCH_PROVIDERS);

assert.deepEqual(readAppearanceSettings(prefs), { theme: "system", accent: "#0b57d0", defaultAccent: NAVIS_DEFAULT_ACCENT, bookmarkBar: "always", historyButton: false });
prefs.setBoolPref("navis.history.sidebar.visible", true);
assert.equal(readAppearanceSettings(prefs).historyButton, true, "Legacy visibility remains the fallback until the new preference is explicitly chosen");
prefs.setBoolPref("navis.history.button.visible", false);
assert.equal(readAppearanceSettings(prefs).historyButton, false, "Explicit new false must override legacy true");
prefs.clearUserPref("navis.history.sidebar.visible");
prefs.clearUserPref("navis.history.button.visible");
setAppearanceSetting(prefs, "theme", "dark");
assert.equal(prefs.values.get("layout.css.prefers-color-scheme.content-override"), 0);
setAppearanceSetting(prefs, "theme", "system");
assert.equal(prefs.values.has("ui.systemUsesDarkTheme"), false);
setAppearanceSetting(prefs, "accent", "#AB12cd");
assert.equal(readAppearanceSettings(prefs).accent, "#ab12cd");
setAppearanceSetting(prefs, "bookmarkBar", "newtab");
assert.equal(readAppearanceSettings(prefs).bookmarkBar, "newtab");
for (const [key, value] of [["theme", "other"], ["accent", "red"], ["historyButton", 1], ["unknown", true]]) {
  const before = new Map(prefs.values);
  assert.throws(() => setAppearanceSetting(prefs, key, value));
  assert.deepEqual(prefs.values, before);
}
assert.deepEqual(readDownloadSettings(prefs), { askBeforeSaving: true, deletePrivateOnExit: false, openWhenComplete: false });
setDownloadSetting(prefs, "askBeforeSaving", false);
assert.equal(prefs.values.get("browser.download.useDownloadDir"), true);
assert.equal(readDownloadSettings(prefs).askBeforeSaving, false);
assert.throws(() => setDownloadSetting(prefs, "openWhenComplete", "true"));

const diagnostics = { application: [], engine: [], graphics: [], media: [], network: [], capabilities: [], system: [] };
for (const locale of ["en-US", "zh-CN"]) {
  for (const route of ["", "search", "privacy", "appearance", "downloads", "help"]) {
    const page = getNavisInternalPage("settings", route);
    assert.ok(page);
    const html = renderNavisInternalPage({ page, pages: [], diagnostics, locale, nonce: "test" });
    const nav = html.match(/<nav class="settings-nav"[\s\S]*?<\/nav>/)[0];
    assert.deepEqual([...nav.matchAll(/data-settings-route="([^"]+)"/g)].map(match => match[1]), ["search", "privacy", "appearance", "downloads", "help"]);
    assert.equal(nav.includes('href="navis://downloads/"'), false);
    assert.equal(nav.includes('href="navis://history/"'), false);
    assert.equal([...html.matchAll(/data-settings-section="/g)].length, 5);
    assert.match(html, /history\.pushState\(null, "", link\.href\)/);
    assert.match(html, /addEventListener\("popstate"/);
    assert.match(html, /if \(!provider\.builtIn\)/);
    assert.match(html, /href="navis:\/\/credits\/"/);
    if (route === "search") {
      const providerButton = html.match(/<button id="provider-add"[^>]*>([\s\S]*?)<\/button>/);
      assert.ok(providerButton, "Provider add action is present");
      assert.match(providerButton[0], /aria-label="[^"]+"/);
      assert.match(providerButton[1], /<svg/);
      const actionStyle = html.match(/\.settings-action\s*\{([^}]+)\}/)?.[1] ?? "";
      const copyStyle = html.match(/\.settings-row-copy\s*\{([^}]+)\}/)?.[1] ?? "";
      const paragraphStyle = html.match(/\.settings-row-copy p\s*\{([^}]+)\}/)?.[1] ?? "";
      assert.match(actionStyle, /flex-shrink:\s*0\s*;/, "Shared actions must not shrink under a long template description");
      assert.match(actionStyle, /white-space:\s*nowrap\s*;/, "Shared text actions declare their single-line contract, not a measured layout result");
      assert.match(copyStyle, /min-width:\s*0\s*;/, "The description must be allowed to yield width to the action");
      assert.match(paragraphStyle, /overflow-wrap:\s*anywhere\s*;/, "Long URL/template words must wrap instead of squeezing Add");
    }
    const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
    assert.equal(new Set(ids).size, ids.length, "No duplicate settings controls");
    for (const [, script] of html.matchAll(/<script nonce="test">([\s\S]*?)<\/script>/g)) {
      new vm.Script(script);
    }
  }
}

// Execute the shipped click handler against a bounded History API stub. This
// checks failure handling, not whether an installed native runtime permits SPA.
const settingsDocument = renderNavisInternalPage({
  page: getNavisInternalPage("settings"), pages: [], diagnostics, locale: "en-US", nonce: "route-test",
});
const settingsScript = [...settingsDocument.matchAll(/<script nonce="route-test">([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).find(script => script.includes("const showRoute ="));
const filterSource = settingsScript.slice(settingsScript.indexOf("  const filterSettings ="), settingsScript.indexOf("  const showRoute ="));
for (const query of ["搜索", "engine", "自定义名称", "不存在的设置"]) {
  const card = { dataset: { search: "search engine provider" }, textContent: "搜索引擎 自定义名称", setAttribute() {} };
  const noResults = {};
  vm.runInNewContext(filterSource + "\nfilterSettings();", {
    search: { value: query, addEventListener() {} }, cards: [card], sections: [], noResults,
  });
  assert.equal(card.hidden, query === "不存在的设置", "Search combines localized visible text and supplementary keywords");
  assert.equal(noResults.hidden, !card.hidden);
}
const clickHook = settingsScript.slice(
  settingsScript.indexOf('  for (const link of routeLinks) {\n    link.addEventListener("click"'),
  settingsScript.indexOf('  document.querySelector(".settings-brand")'),
);
assert(clickHook.includes("history.pushState"));
for (const rejected of [false, true]) {
  const navigationCalls = [];
  const location = { href: "navis://settings/search", pathname: "/search" };
  const search = { value: "query to preserve if rejected" };
  let click;
  const link = { href: "navis://settings/privacy", addEventListener: (_type, handler) => { click = handler; } };
  const navigationContext = {
    routeLinks: [link], location, search, busy: true, requestTimer: 7,
    history: { pushState(_state, _title, uri) {
      navigationCalls.push("push");
      if (rejected) throw Object.assign(new Error("Native route rewrite rejected"), { name: "SecurityError" });
      location.href = uri;
      location.pathname = new URL(uri).pathname;
    } },
    showRoute: () => navigationCalls.push("render:" + location.pathname),
    clearTimeout: () => navigationCalls.push("clear-timer"),
    dispatchCommand: command => navigationCalls.push(command),
    setStatus: message => navigationCalls.push("status:" + message),
    window: { scrollTo: () => navigationCalls.push("scroll"), NavisL10n: { text: id => id } },
  };
  vm.runInNewContext(clickHook, navigationContext);
  const event = { button: 0, preventDefault: () => navigationCalls.push("prevent") };
  assert.doesNotThrow(() => click(event));
  if (rejected) {
    assert.deepEqual(navigationCalls, ["prevent", "push", "status:settings.failed"]);
    assert.equal(location.pathname, "/search");
    assert.equal(search.value, "query to preserve if rejected");
    assert.equal(navigationContext.busy, true, "A rejected route must not cancel an existing settings request");
  } else {
    assert.deepEqual(navigationCalls, ["prevent", "push", "render:/privacy", "scroll", "settings:get"]);
    assert.equal(search.value, "");
    assert.equal(navigationContext.busy, true, "A route change must preserve the active request and its pending writes");
  }
  navigationCalls.length = 0;
  for (const modifier of ["ctrlKey", "metaKey", "shiftKey", "altKey"]) {
    click({ ...event, [modifier]: true });
  }
  click({ ...event, button: 1 });
  assert.deepEqual(navigationCalls, [], "Modified clicks retain the normal browser link action");
}

const lifecycleSource = (await readFile(new URL("../../runtime/embedder/modules/DesktopDownloadPreferences.sys.mjs", import.meta.url), "utf8"))
  .replace(/^import .*;\n/gm, "")
  .replaceAll("export ", "") + "\nglobalThis.Lifecycle = DesktopDownloadLifecycle;";
const removed = [];
const launched = [];
const launchOptions = [];
const callbacks = new Map();
let blocker;
const files = new Map();
const lifecyclePrefs = preferences();
const context = {
  Services: { prefs: lifecyclePrefs, obs: { addObserver: (callback, topic) => callbacks.set(topic, callback) } },
  AsyncShutdown: { profileBeforeChange: { addBlocker: (_label, callback) => { blocker = callback; } } },
  readDownloadSettings,
  setDownloadSetting,
  PathUtils: { isAbsolute: path => path.startsWith("/") },
  IOUtils: { stat: async path => ({ ...files.get(path) }), remove: async path => { removed.push(path); files.delete(path); } },
  console,
};
vm.runInNewContext(lifecycleSource, context);
const lifecycle = new context.Lifecycle();
const download = (path, privateMode = false) => ({
  source: { isPrivate: privateMode }, target: { path }, succeeded: false, stopped: false,
  cancel: async function() { this.stopped = true; }, removePartialData: async () => undefined,
  launch: async options => { launched.push(path); launchOptions.push(structuredClone(options)); },
});
setDownloadSetting(lifecyclePrefs, "openWhenComplete", true);
const old = download("/downloads/old.txt"); old.succeeded = true;
lifecycle.observe(old, { existing: true });
assert.deepEqual(launched, []);
const normal = download("/downloads/new.txt");
lifecycle.observe(normal); normal.succeeded = true; normal.stopped = true;
lifecycle.observe(normal); lifecycle.observe(normal);
assert.deepEqual(launched, ["/downloads/new.txt"]);
assert.deepEqual(launchOptions, [{ useSystemDefault: true }], "Automatic Open uses the system handler, matching manual Open");
const geckoOwned = download("/downloads/gecko-open.txt");
geckoOwned.launchWhenSucceeded = true;
lifecycle.observe(geckoOwned);
geckoOwned.succeeded = true;
lifecycle.observe(geckoOwned);
assert.deepEqual(launched, ["/downloads/new.txt"], "Gecko's existing per-download Open choice owns its completion launch");
setDownloadSetting(lifecyclePrefs, "deletePrivateOnExit", true);
for (const path of ["/downloads/private.txt", "/downloads/changed.txt", "/downloads/symlink.txt"]) {
  const item = download(path, true);
  files.set(path, { type: "regular", size: 10, lastModified: 123 });
  lifecycle.observe(item);
  item.succeeded = true; item.stopped = true;
  lifecycle.observe(item);
}
await Promise.resolve();
files.set("/downloads/changed.txt", { type: "regular", size: 20, lastModified: 456 });
files.set("/downloads/symlink.txt", { type: "symlink", size: 10, lastModified: 123 });
await blocker();
assert.deepEqual(removed, ["/downloads/private.txt"]);

// Drive the real actor with independently completing requests. A background
// diagnostic read and an interactive settings command may overlap in the SPA.
const actorSource = (await readFile(new URL("../../runtime/embedder/components/DesktopInternalPageChild.sys.mjs", import.meta.url), "utf8"))
  .replace(/^import .*;\n/gm, "")
  .replace("export class DesktopInternalPageChild", "class DesktopInternalPageChild") +
  "\nglobalThis.InternalPageActor = DesktopInternalPageChild;";
const actorContext = {
  JSWindowActorChild: class {},
  resolveNavisInternalPageURI,
  Cu: { waiveXrays: value => value, cloneInto: value => structuredClone(value) },
};
vm.runInNewContext(actorSource, actorContext);
const actor = new actorContext.InternalPageActor();
const responses = [];
const requests = [];
const actorDocument = {};
const setActorRoute = (id, route = "") => {
  const page = getNavisInternalPage(id, route);
  actorDocument.documentURIObject = {
    schemeIs: scheme => scheme === "navis", asciiHost: id,
    hasUserPass: false, port: -1, hasQuery: false,
    filePath: page.path, specIgnoringRef: page.url,
  };
};
actor.contentWindow = {
  document: actorDocument,
  CustomEvent: class { constructor(type, { detail }) { Object.assign(this, { type, detail }); } },
  dispatchEvent: event => responses.push(event),
};
actor.sendQuery = (_topic, data) => new Promise((resolve, reject) => requests.push({ data, resolve, reject }));
const sendActorCommand = (type, command, value) => actor.handleEvent({ target: actorDocument, type, detail: { command, value } });
const settleActor = () => new Promise(resolve => setImmediate(resolve));
setActorRoute("settings", "search");
sendActorCommand("NavisSettingsCommand", "settings:get");
sendActorCommand("NavisDiagnosticsCommand", "diagnostics:get");
requests[0].resolve({ pageKey: "settings", outcome: "ready" });
await settleActor();
assert.equal(responses[0]?.type, "NavisSettingsState", "Diagnostics must not discard a settings response");
setActorRoute("settings", "appearance");
requests[1].resolve({ pageKey: "settings/search", outcome: "ready", diagnostics: [] });
await settleActor();
assert.equal(responses[1]?.type, "NavisDiagnosticsState", "An in-document route change preserves compatible diagnostic reads");
sendActorCommand("NavisSettingsCommand", "settings:get");
sendActorCommand("NavisSettingsCommand", "settings:get");
requests[2].resolve({ pageKey: "settings", outcome: "ready", old: true });
requests[3].resolve({ pageKey: "settings", outcome: "ready", latest: true });
await settleActor();
assert.equal(responses.length, 3);
assert.equal(responses[2].detail.latest, true, "Same-channel stale snapshots remain suppressed");
sendActorCommand("NavisSettingsCommand", "settings:get");
sendActorCommand("NavisDiagnosticsCommand", "diagnostics:get");
requests[4].reject(new Error("settings read failed"));
await settleActor();
assert.equal(responses[3].type, "NavisSettingsState");
assert.equal(responses[3].detail.outcome, "failed", "A diagnostic read cannot hide a setting failure");
setActorRoute("credits");
requests[5].resolve({ pageKey: "settings/appearance", outcome: "ready", diagnostics: [] });
await settleActor();
assert.equal(responses.length, 4, "Responses from a previous page are never dispatched to another product page");
setActorRoute("settings", "appearance");
sendActorCommand("NavisSettingsCommand", "settings:get");
// After a UI read timeout the queued write replaces the actor's latest request.
sendActorCommand("NavisSettingsCommand", "settings:set-appearance", { key: "accent", value: "#345678" });
requests[6].resolve({ pageKey: "settings", outcome: "ready", appearance: { accent: "#0b57d0" } });
await settleActor();
assert.equal(responses.length, 4, "The actual actor must suppress an expired read after the next write starts");
requests[7].resolve({ pageKey: "settings", outcome: "appearance-updated", appearance: { accent: "#345678" } });
await settleActor();
assert.equal(responses.length, 5);
assert.equal(responses[4].detail.appearance.accent, "#345678");

// Download receipts are deliberately outside the mutation-response channel.
setActorRoute("downloads");
actorDocument.visibilityState = "visible";
actorDocument.hasFocus = () => true;
sendActorCommand("NavisManagementCommand", "downloads:cancel", "download-1");
const cancellation = requests.at(-1);
sendActorCommand("NavisManagementCommand", "downloads:acknowledge", [{ id: "download-2", token: "completion-2", ignored: true }]);
const receipt = requests.at(-1);
assert.notEqual(receipt, cancellation);
assert.deepEqual(structuredClone(receipt.data.value), [{ id: "download-2", token: "completion-2" }]);
receipt.resolve({ pageKey: "downloads", outcome: "acknowledged" });
cancellation.resolve({ pageKey: "downloads", outcome: "ready", revision: 3, items: [] });
await settleActor();
assert.equal(responses.length, 6, "Receipts cannot swallow a cancel reply or generate an unrelated management reply");
assert.equal(responses.at(-1).detail.revision, 3);
const requestCount = requests.length;
for (const value of [null, [{ id: "x", token: "" }], [{ id: "x", token: "x".repeat(65) }],
                     [{ id: "x".repeat(129), token: "x" }], Array(501).fill({ id: "x", token: "x" })]) {
  sendActorCommand("NavisManagementCommand", "downloads:acknowledge", value);
}
actorDocument.hasFocus = () => false;
sendActorCommand("NavisManagementCommand", "downloads:acknowledge", [{ id: "x", token: "x" }]);
actorDocument.hasFocus = () => true;
actorDocument.visibilityState = "hidden";
sendActorCommand("NavisManagementCommand", "downloads:acknowledge", [{ id: "x", token: "x" }]);
assert.equal(requests.length, requestCount, "Invalid, hidden or unfocused receipts cannot reach Core");
actor.receiveMessage({ name: "DesktopInternalPage:DownloadsChanged", data: { pageKey: "downloads", revision: 4, items: [] } });
assert.equal(responses.at(-1).type, "NavisDownloadsChanged");
assert.equal(responses.at(-1).detail.revision, 4);
const responseCount = responses.length;
setActorRoute("settings", "downloads");
actor.receiveMessage({ name: "DesktopInternalPage:DownloadsChanged", data: { revision: 5 } });
sendActorCommand("NavisManagementCommand", "downloads:acknowledge", [{ id: "x", token: "x" }]);
assert.equal(responses.length, responseCount, "Download pushes cannot leak to another internal route");
assert.equal(requests.length, requestCount);
console.log("PASS settings policy: provider CRUD/rejection, appearance, downloads, six SPA entries in two languages, successful/rejected SPA click handling, download cleanup ownership, and independent settings/diagnostic responses");
console.log("PASS download actor: bounded foreground receipts, mutation replies preserved, route-scoped live pushes");
