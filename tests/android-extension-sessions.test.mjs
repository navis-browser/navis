import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const read = path => fs.readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const base = "gecko/mobile/shared/";
const profileSource = read(`${base}modules/navis/NavisAndroidExtensionProfile.sys.mjs`);
const apiSource = read(`${base}components/extensions/ext-sessions.js`);
const captureSource = read(`${base}modules/navis/NavisAndroidClosedTabs.sys.mjs`);
const plain = value => JSON.parse(JSON.stringify(value));

function fixture() {
  const calls = [], listeners = new Set(), windows = new Map(), tabs = new Map();
  const permissions = new Set(["sessions"]);
  const transport = {
    respond: async () => "[]",
    sendRequestForResult(event, data) { calls.push(data); return this.respond(data); },
    registerListener(listener, events) { listeners.add({ listener, events }); },
    unregisterListener(listener) { for (const record of listeners) if (record.listener === listener) listeners.delete(record); },
  };
  let windowRemoved;
  const topology = { onWindowRemoved(callback) { windowRemoved = callback; return () => {}; } };
  const realm = { TextEncoder, ExtensionError: Error, ExtensionAPIPersistent: class {},
    EventManager: class { api() { return {}; } },
    ChromeUtils: { importESModule(uri) { return uri.includes("Messaging") ?
      { EventDispatcher: { byName: () => transport } } : { ExtensionError: Error }; } },
    tabTracker: { getTab(id) { return tabs.get(id); } },
    windowTracker: { getWindow(id) { return windows.get(id); }, getId(window) { return window.id; } },
  };
  vm.createContext(realm);
  vm.runInContext(profileSource.replace("export const NavisAndroidExtensionProfile", "this.profile"), realm);
  realm.ChromeUtils.importESModule = uri => uri.includes("TabTopology") ?
    { NavisAndroidExtensionTabTopology: topology } : { NavisAndroidExtensionProfile: realm.profile };
  vm.runInContext(apiSource, realm);
  const extension = { id: "sessions@test", hasPermission: name => permissions.has(name), privateBrowsingAllowed: false,
    tabManager: { canAccessTab(tab) { return !tab.private || extension.privateBrowsingAllowed; },
      convert(tab) { return { id: tab.id, windowId: tab.browser.documentGlobal.id }; } } };
  const context = { extension, canAccessWindow(window) { return !!window && (!window.private || extension.privateBrowsingAllowed); } };
  const owner = new realm.sessions(); owner.extension = extension;
  const add = (id, windowId, privateMode = false) => {
    const window = windows.get(windowId) || { id: windowId, private: privateMode };
    windows.set(windowId, window);
    const tab = { id, private: privateMode, browser: { documentGlobal: window } }; tabs.set(id, tab); return tab;
  };
  add(10, 7); add(11, 8, true);
  return { api: owner.getAPI(context).sessions, owner, profile: realm.profile, context, extension, permissions,
    calls, transport, listeners, windows, tabs, add, removeWindow: id => { windows.delete(id); windowRemoved(id); },
    another(id) { const other = { ...extension, id }; const apiOwner = new realm.sessions(); apiOwner.extension = other;
      return apiOwner.getAPI({ ...context, extension: other }).sessions; } };
}

test("sessions uses permission-scoped profile requests and suppresses revoked late data", async () => {
  const f = fixture();
  f.permissions.clear(); await assert.rejects(f.api.getRecentlyClosed(), /not allowed/); assert.equal(f.calls.length, 0);
  f.permissions.add("sessions"); f.context.incognito = true;
  await assert.rejects(f.api.getRecentlyClosed(), /not allowed/); assert.equal(f.calls.length, 0);
  f.context.incognito = false;
  let reply; f.transport.respond = () => new Promise(resolve => { reply = resolve; });
  const waiting = f.api.getRecentlyClosed(); f.permissions.clear(); reply("[]");
  await assert.rejects(waiting, /not allowed/);
});
test("recent summaries, exact forget IDs and real restored topology map to desktop schema", async () => {
  const f = fixture(); f.transport.respond = async () => JSON.stringify([
    { sessionId: "closed-1", windowId: 7, index: 3, title: "A", url: "https://a.test/", closedAt: 1750000000000 }]);
  const list = await f.api.getRecentlyClosed({ maxResults: 1 });
  assert.equal(list[0].lastModified, 1750000000000); assert.equal(list[0].tab.incognito, false);
  assert.equal(list[0].tab.sessionId, "closed-1"); assert.equal("id" in list[0].tab, false);
  assert.equal(JSON.parse(f.calls[0].arguments).maxResults, 1);
  f.transport.respond = async () => "null"; await f.api.forgetClosedTab(7, "closed-1");
  assert.deepEqual(JSON.parse(f.calls.at(-1).arguments), { windowId: 7, sessionId: "closed-1" });
  await assert.rejects(f.api.forgetClosedWindow("none"), /No closed window/);
  f.transport.respond = async () => '{"tabId":10,"windowId":7,"lastModified":123}';
  assert.deepEqual(plain(await f.api.restore("closed-1")), { lastModified: 123, tab: { id: 10, windowId: 7 } });
  f.tabs.delete(10); await assert.rejects(f.api.restore("closed-1"), /inaccessible tab/);
  assert.doesNotMatch(apiSource, /createTab\(|loadURI\(/);
});
test("tab values are JSON clones, extension namespaced, live-owner and private guarded", async () => {
  const f = fixture(), value = { nested: { n: 1 } };
  await f.api.setTabValue(10, "state", value); value.nested.n = 9;
  const readback = await f.api.getTabValue(10, "state"); assert.equal(readback.nested.n, 1);
  readback.nested.n = 8; assert.equal((await f.api.getTabValue(10, "state")).nested.n, 1);
  assert.equal(await f.another("other@test").getTabValue(10, "state"), undefined);
  await assert.rejects(f.api.getTabValue(11, "state"), /inaccessible/);
  f.extension.privateBrowsingAllowed = true; await f.api.setTabValue(11, "state", "private");
  assert.equal(await f.api.getTabValue(11, "state"), "private");
  f.extension.privateBrowsingAllowed = false; await assert.rejects(f.api.getTabValue(11, "state"));
  await f.api.removeTabValue(10, "state"); assert.equal(await f.api.getTabValue(10, "state"), undefined);
  f.tabs.delete(10); await assert.rejects(f.api.setTabValue(10, "state", 1));
  f.add(10, 7); assert.equal(await f.api.getTabValue(10, "state"), undefined);
});
test("value bounds apply to UTF-8, all keys and cyclic/undefined JSON", async () => {
  const f = fixture();
  for (const key of ["", "k".repeat(257)]) {
    await assert.rejects(f.api.getTabValue(10, key)); await assert.rejects(f.api.removeTabValue(10, key));
  }
  await assert.rejects(f.api.setTabValue(10, "s", "中".repeat(22000)), /storage limit/);
  const cycle = {}; cycle.self = cycle;
  await assert.rejects(f.api.setTabValue(10, "s", cycle), /serializable/);
  await assert.rejects(f.api.setTabValue(10, "s", undefined), /storage limit/);
  await f.api.setTabValue(10, "s", null); assert.equal(await f.api.getTabValue(10, "s"), null);
});
test("window values survive representative-host replacement but not product window removal", async () => {
  const f = fixture(); await f.api.setWindowValue(7, "state", { count: 1 });
  f.windows.set(7, { id: 7, private: false });
  assert.equal((await f.api.getWindowValue(7, "state")).count, 1);
  await assert.rejects(f.api.getWindowValue(8, "state"));
  f.removeWindow(7); await assert.rejects(f.api.getWindowValue(7, "state"));
  f.windows.set(7, { id: 7 }); assert.equal(await f.api.getWindowValue(7, "state"), undefined);
});
test("committed-change listener converts, unregisters and ignores revoked consumers", () => {
  const f = fixture(), seen = [];
  const registration = f.owner.PERSISTENT_EVENTS.onChanged.call(f.owner, { fire: { async: () => seen.push(1) } });
  const emit = () => { for (const { listener, events } of f.listeners) {
    assert.deepEqual(plain(events), ["NavisAndroid:ExtensionProfile:SessionsChanged"]); listener.onEvent();
  } };
  emit(); registration.convert({ async: () => seen.push(2) }); emit();
  f.permissions.clear(); emit(); assert.deepEqual(seen, [1, 2]); registration.unregister(); assert.equal(f.listeners.size, 0);
});

function captureFixture(source = captureSource) {
  const make = (id, url, postData = null, children = []) => ({ ID: id, url, postData,
    get childCount() { return children.length; }, GetChildAt(index) { return children[index]; } });
  const entries = [make(1, "https://a.test/"), make(2, "https://b.test/")];
  const history = { count: entries.length, index: 1, getEntryAtIndex(index) { return entries[index]; } };
  const context = { currentURI: { spec: entries[1].url, schemeIs: value => value === "https" },
    currentWindowGlobal: { documentTitle: "B" }, sessionHistory: history };
  const browser = { navisNativePresentation: false, browsingContext: context, private: false };
  const window = { tab: { linkedBrowser: browser }, closed: false };
  const calls = [];
  const realm = { ChromeUtils: { importESModule(uri) { return uri.includes("PrivateBrowsing") ?
    { PrivateBrowsingUtils: { isBrowserPrivate: value => value.private } } : { SessionHistory: {
      collectFromParent(uri, allow, actual) { calls.push(uri); assert.equal(actual, history);
        return { entries: entries.map(({ ID, url }) => ({ ID, url })), index: history.index + 1 }; },
    } }; } } };
  vm.createContext(realm); vm.runInContext(source.replace("export const NavisAndroidClosedTabs", "this.capture"), realm);
  return { capture: () => realm.capture.capture(window), entries, history, browser, context, window, make, calls };
}
test("closed capture keeps actual entry identities and never captures private/native/nonweb state", () => {
  const f = captureFixture(); const captured = f.capture(); assert.equal(captured.restorable, true);
  assert.deepEqual(JSON.parse(captured.state).history.entries.map(entry => entry.ID), [1, 2]);
  for (const [field, value, reason] of [["private", true, "private"], ["navisNativePresentation", true, "native"]]) {
    const next = captureFixture(); next.browser[field] = value; assert.equal(next.capture().reason, reason); assert.equal(next.calls.length, 0);
  }
  f.context.currentURI.schemeIs = () => false; assert.equal(f.capture().reason, "unsupported-uri");
  f.window.closed = true; assert.throws(f.capture, /owner/);
});
test("POST anywhere in actual history or child frames is not downgraded to a GET snapshot", () => {
  for (const child of [false, true]) {
    const f = captureFixture();
    f.entries[0] = child ? f.make(1, "https://a.test/", null, [f.make(3, "https://frame.test/", {})]) :
      f.make(1, "https://a.test/", {});
    assert.equal(f.capture().reason, "post-data"); assert.equal(f.calls.length, 0);
  }
  const mutation = captureFixture(captureSource.replace("if (entry.postData) return true;", "if (false) return true;"));
  mutation.entries[0].postData = {};
  assert.notEqual(mutation.capture().reason, "post-data", "mutation must fail the production POST assertion");
});
test("capture and API are packaged and only available through the fixed owning-host operation", () => {
  const registration = JSON.parse(read(`${base}components/extensions/ext-android.json`));
  assert.equal(registration.sessions.url, "chrome://geckoview/content/ext-sessions.js");
  const jar = read(`${base}components/extensions/jar.mn`);
  assert.match(jar, /content\/ext-sessions\.js/); assert.match(jar, /schemas\/sessions\.json.*browser\/components/);
  assert.match(read(`${base}modules/navis/moz.build`), /"NavisAndroidClosedTabs.sys.mjs"/);
  const host = read(`${base}chrome/navis/host.js`);
  assert.match(host, /data.operation === "session:capture-closed"/);
  assert.match(host, /return NavisAndroidClosedTabs.capture\(window\)/);
  const installer = read(`${base}modules/navis/NavisAndroidWebExtensionHost.sys.mjs`);
  const permissions = installer.match(/const SUPPORTED_REQUIRED_PERMISSIONS = new Set\(\[([\s\S]*?)\]\);/)[1];
  assert.match(permissions, /"sessions"/);
});
