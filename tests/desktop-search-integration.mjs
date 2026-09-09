// SPDX-License-Identifier: MPL-2.0

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { OmniboxEditState } from "../../platform/gecko-chrome/chrome/content/omnibox-edit-state.mjs";

// Execute product source against value-only host stubs. No browser process,
// Profile, DOM, rendered document, timers or network are involved.
const extensionSource = readFileSync(new URL("../../runtime/embedder/extensions/ext-search.js", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("../../platform/gecko-chrome/chrome/content/main.mjs", import.meta.url), "utf8");

function extensionHarness() {
  const submissions = [], navigation = [], windows = [], initialization = [];
  const listeners = new Map(), timers = new Map();
  const normalWindow = { id: "normal-window" }, privateWindow = { id: "private-window" };
  const tabs = new Map([
    [1, { id: 1, window: normalWindow, session: { state: { private: false } } }],
    [2, { id: 2, window: privateWindow, session: { state: { private: true } } }],
  ]);
  const provider = (id, name) => Object.freeze({ id, name,
    template: `https://${id}.invalid/?q={searchTerms}`, suggestionTemplate: "", builtIn: false });
  const initial = [provider("google", "Google"), provider("custom", "Personal search")];
  let snapshot = Object.freeze({ providers: Object.freeze(initial), defaultProviderId: "custom", remoteSuggestionsEnabled: false });
  let initialize = async () => snapshot;
  const service = {
    get snapshot() { return snapshot; },
    init(legacy) { initialization.push(legacy); return initialize(); },
    submission(query, options) {
      assert.ok(snapshot.providers.some(item => item.id === options.providerId), "Core receives a real provider ID");
      submissions.push({ query, providerId: options.providerId, privateMode: options.privateMode });
      return { url: `https://submission.invalid/${options.providerId}?q=${encodeURIComponent(query)}` };
    },
  };
  const tabTracker = {
    activeTab: tabs.get(1),
    getTab(id) { const tab = tabs.get(id); if (!tab) throw new Error("Unknown tab"); return tab; },
  };
  const moduleValues = {
    NavisSearchService: service,
    readSearchProviders: () => initial,
    readDefaultSearchProvider: () => "custom",
    DesktopExtensionTabs: {
      updateTab: (tab, data) => navigation.push({ kind: "update", tab, url: data.url }),
      createTab: (window, url, active, principal) => navigation.push({ kind: "create", window, url, active, principal }),
      on: (name, listener) => listeners.set(name, listener),
      off: (name, listener) => { if (listeners.get(name) === listener) listeners.delete(name); },
      activeTabForWindow: window => [...tabs.values()].find(tab => tab.window === window),
    },
    setTimeout: callback => { const id = timers.size + 1; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
  };
  const context = vm.createContext({
    ExtensionAPI: class {}, ExtensionError: class extends Error {}, tabTracker,
    ChromeUtils: { defineESModuleGetters(target, entries) {
      for (const key of Object.keys(entries)) {
        assert.ok(Object.hasOwn(moduleValues, key), `Unexpected extension dependency: ${key}`);
        Object.defineProperty(target, key, { get: () => moduleValues[key] });
      }
    } },
    Services: { prefs: {}, ww: { openWindow(owner, uri, target, features) {
      const window = { id: "created-window", closed: false, close() { this.closed = true; } };
      windows.push({ owner, uri, target, features, window });
      return window;
    } } },
  });
  new vm.Script(extensionSource, { filename: "embedder/extensions/ext-search.js" }).runInContext(context);
  const principal = { id: "extension-principal" };
  const api = new context.search().getAPI({ principal, incognito: false }).search;
  return { api, submissions, navigation, windows, initialization, tabs, tabTracker, timers, listeners, principal,
    catalogue(providers, defaultProviderId) { snapshot = Object.freeze({ ...snapshot,
      providers: Object.freeze(providers), defaultProviderId }); },
    deferInitialization() { let resolve; initialize = () => new Promise(value => { resolve = value; }); return () => resolve(snapshot); },
    restoreInitialization() { initialize = async () => snapshot; },
  };
}

const extension = extensionHarness();
assert.deepEqual(JSON.parse(JSON.stringify(await extension.api.get())), [
  { name: "Google", alias: "google", isDefault: false },
  { name: "Personal search", alias: "custom", isDefault: true },
]);
assert.equal(extension.initialization[0].remoteSuggestionsEnabled, false);
await extension.api.query({ text: "  custom default  " });
assert.equal(extension.submissions.at(-1).query, "custom default");
assert.equal(extension.submissions.at(-1).providerId, "custom");
assert.equal(extension.navigation.at(-1).tab.id, 1);
await extension.api.search({ query: "named", engine: "Personal search" });
assert.equal(extension.submissions.at(-1).providerId, "custom");
assert.equal(extension.navigation.at(-1).kind, "create");
assert.equal(extension.navigation.at(-1).principal, extension.principal);
await extension.api.search({ query: "by ID", engine: "google", tabId: 1 });
assert.equal(extension.submissions.at(-1).providerId, "google");
// Target Session privacy must win over the currently selected tab's privacy.
extension.tabTracker.activeTab = extension.tabs.get(1);
await extension.api.search({ query: "private target", tabId: 2 });
assert.equal(extension.submissions.at(-1).privateMode, true);
extension.tabTracker.activeTab = extension.tabs.get(2);
await extension.api.query({ text: "normal target", tabId: 1 });
assert.equal(extension.submissions.at(-1).privateMode, false);
await extension.api.query({ text: "private active" });
assert.equal(extension.submissions.at(-1).privateMode, true);
await extension.api.search({ query: "private new tab" });
assert.equal(extension.submissions.at(-1).privateMode, true);
assert.equal(extension.navigation.at(-1).window, extension.tabs.get(2).window);
await extension.api.search({ query: "private new window", disposition: "NEW_WINDOW" });
assert.equal(extension.submissions.at(-1).privateMode, true);
assert.match(extension.windows.at(-1).features, /,private(?:,|$)/);
const opened = extension.windows.at(-1).window;
const navigationCount = extension.navigation.length;
extension.listeners.get("tab-created")("tab-created", { nativeTab: extension.tabs.get(1) });
assert.equal(extension.navigation.length, navigationCount, "A different window cannot consume the pending search");
const openedTab = { id: 3, window: opened, session: { state: { private: true } } };
extension.listeners.get("tab-created")("tab-created", { nativeTab: openedTab });
assert.equal(extension.navigation.at(-1).tab, openedTab);
assert.equal(extension.listeners.size, 0);
assert.equal(extension.timers.size, 0);

extension.catalogue([{ id: "custom", name: "Renamed custom" }, { id: "google", name: "Renamed Google" }], "google");
const changed = await extension.api.get();
assert.deepEqual(Array.from(changed, item => item.name), ["Renamed custom", "Renamed Google"]);
assert.equal(changed[1].isDefault, true);
await extension.api.query({ text: "changed default" });
assert.equal(extension.submissions.at(-1).providerId, "google");
await extension.api.search({ query: "renamed engine", engine: "Renamed custom" });
assert.equal(extension.submissions.at(-1).providerId, "custom");
for (const invoke of [
  () => extension.api.search({ query: "missing", engine: "Personal search" }),
  () => extension.api.search({ query: "missing", engine: "unknown" }),
  () => extension.api.query({ text: "", tabId: 1 }),
  () => extension.api.query({ text: "query", tabId: 99 }),
  () => extension.api.query({ text: "query", tabId: 1, disposition: "CURRENT_TAB" }),
]) {
  const before = extension.navigation.length;
  await assert.rejects(invoke());
  assert.equal(extension.navigation.length, before, "Rejected input/provider/tab never falls back to navigation");
}
const completeInitialization = extension.deferInitialization();
const beforeReady = extension.submissions.length;
const waiting = extension.api.query({ text: "wait for Core" });
assert.equal(extension.submissions.length, beforeReady);
completeInitialization();
await waiting;
extension.restoreInitialization();
extension.catalogue([{ id: "custom", name: "Custom only" }], "missing");
const beforeMissing = extension.navigation.length;
await assert.rejects(extension.api.get());
await assert.rejects(extension.api.query({ text: "missing default" }));
assert.equal(extension.navigation.length, beforeMissing, "A missing default cannot silently select the first provider");

const start = mainSource.indexOf("  const loadAddress = () => {");
const end = mainSource.indexOf("  const reloadOrStop =", start);
assert.ok(start > 0 && end > start, "Actual main.mjs loadAddress handler is located");
const loadAddressSource = mainSource.slice(start, end);
function addressHarness({ input = "typed query", query = input.trim(), choices = [], selection = 0,
  privateMode = false, active = true, resolveError = false, loadError = false } = {}) {
  const loads = [], searches = [], resolutions = [], switches = [], validity = [], status = [];
  let cancelled = 0;
  const session = { id: 1, loadUri(url) { if (loadError) throw new Error("Navigation rejected"); loads.push(url); } };
  const edit = new OmniboxEditState();
  edit.beginEditing();
  const records = new Map([[1, { session }], [2, { session: { id: 2 } }]]);
  const context = vm.createContext({
    activeRecord: active ? records.get(1) : null, records,
    address: { value: input }, privateMode, addressEditState: edit,
    ordinarySuggestionQuery: query, extensionOmniboxSelection: selection,
    ordinaryOmniboxChoices: () => choices,
    extensionOmniboxState: { active: false },
    runtime: {
      searchText(text, options) { searches.push({ text, privateMode: options.privateMode }); return { url: `https://core.invalid/?q=${encodeURIComponent(text)}` }; },
      resolveAddressInput(value, options) {
        resolutions.push({ value, privateMode: options.privateMode });
        if (resolveError) throw new Error("Invalid address");
        return { url: "https://resolved.invalid/" };
      },
    },
    cancelExtensionOmnibox: () => { cancelled++; },
    selectSession: record => switches.push(record.session.id),
    setOmniboxValidity: (_address, message) => validity.push(message ?? ""),
    t: key => key, showTransientStatus: message => status.push(message),
  });
  new vm.Script(`${loadAddressSource}\nglobalThis.submitAddress = loadAddress;`, { filename: "main.mjs:loadAddress" }).runInContext(context);
  return { submit: context.submitAddress, context, edit, loads, searches, resolutions, switches, validity, status,
    cancelled: () => cancelled };
}

let address = addressHarness({ choices: [{ kind: "tab", id: "2", url: "https://open.invalid/" }] });
assert.equal(address.submit(), true);
assert.deepEqual(address.switches, [2]);
assert.equal(address.loads.length, 0);
assert.equal(address.resolutions.length, 0);
assert.equal(address.cancelled(), 1);
assert.equal(address.edit.editing, false);
address = addressHarness({ choices: [{ kind: "tab", id: "removed", url: "https://old-tab.invalid/" }] });
assert.equal(address.submit(), true);
assert.deepEqual(address.loads, ["https://old-tab.invalid/"], "A closed local tab can fall back to its offered URL");
assert.equal(address.switches.length, 0);
for (const privateMode of [false, true]) {
  address = addressHarness({ privateMode, choices: [{ kind: "search", text: "https://suggested.invalid/private",
    url: "https://must-not-open.invalid/" }] });
  assert.equal(address.submit(), true);
  assert.deepEqual(address.searches, [{ text: "https://suggested.invalid/private", privateMode }]);
  assert.equal(address.resolutions.length, 0, "A remote suggestion always forces search, including URL-shaped text");
  assert.match(address.loads[0], /^https:\/\/core\.invalid\/\?q=https%3A/);
}
for (const kind of ["bookmark", "history", "visit"]) {
  address = addressHarness({ choices: [{ kind, url: "https://offered.invalid/" }] });
  assert.equal(address.submit(), true);
  assert.deepEqual(address.loads, ["https://offered.invalid/"]);
}
for (const options of [
  { query: "stale query", choices: [{ kind: "search", text: "stale remote" }] },
  { selection: 99, choices: [{ kind: "search", text: "out of range" }] },
  { selection: -1, choices: [{ kind: "search", text: "negative selection" }] },
]) {
  address = addressHarness({ ...options, privateMode: true });
  assert.equal(address.submit(), true);
  assert.deepEqual(address.resolutions, [{ value: "typed query", privateMode: true }]);
  assert.equal(address.searches.length, 0);
  assert.deepEqual(address.loads, ["https://resolved.invalid/"]);
}
for (const options of [{ resolveError: true }, { loadError: true }]) {
  address = addressHarness(options);
  assert.equal(address.submit(), false);
  assert.equal(address.edit.rejected, true);
  assert.equal(address.context.address.value, "typed query");
  assert.deepEqual(address.validity, ["chrome.invalidAddress"]);
  assert.deepEqual(address.status, ["chrome.invalidAddress"]);
  assert.equal(address.cancelled(), 0);
}
address = addressHarness({ active: false });
assert.equal(address.submit(), false);
assert.equal(address.loads.length + address.resolutions.length + address.searches.length, 0);
console.log("Desktop search integration: actual extension and omnibox handlers passed catalogue/default/privacy/submission/staleness contracts");
