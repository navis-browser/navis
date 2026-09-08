import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../gecko/mobile/shared/modules/navis/NavisAndroidPresentation.sys.mjs", import.meta.url), "utf8");
function fixture(text = source) {
  const listeners = new Set();
  const timers = new Map();
  let timerId = 0;
  const entries = [{ ID: 1, url: "https://a.test/" }, { ID: 2, url: "https://b.test/" }];
  const uri = spec => ({ spec, equals(other) { return spec === other.spec; } });
  const history = { index: 1, requestedIndex: -1, entries,
    getEntryAtIndex(index) { const entry = this.entries[index]; return { ...entry, URI: uri(entry.url) }; } };
  const context = { currentURI: uri(entries[1].url), currentWindowGlobal: { documentTitle: "B" }, sessionHistory: history };
  const browser = {
    browsingContext: context, securityUI: { state: 4 }, navisNativePresentation: true,
    traversals: [], loads: [], stops: 0,
    stop() { this.stops++; },
    gotoIndex(index) { this.traversals.push(index); history.requestedIndex = index; },
    loadURI(uri, options) { this.loads.push({ uri: uri.spec, options }); },
    addProgressListener(listener) { listeners.add(listener); },
    removeProgressListener(listener) { listeners.delete(listener); },
  };
  const window = { tab: { linkedBrowser: browser }, closed: false,
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; }, clearTimeout(id) { timers.delete(id); } };
  const sandbox = vm.createContext({
    ChromeUtils: { importESModule() { return { SessionHistory: {
      collectFromParent(uri, nonempty, actual) { assert.equal(actual, history); return { entries: actual.entries, index: actual.index + 1 }; },
    } }; }, generateQI() { return () => {}; } },
    Services: { io: { newURI(value) { const url = new URL(value); return { spec: value, schemeIs(scheme) { return url.protocol === `${scheme}:`; } }; } },
      scriptSecurityManager: { getSystemPrincipal() { return "product-principal"; } } },
    Ci: { nsIWebProgressListener: { STATE_IS_BROKEN: 2, STATE_IS_SECURE: 4, STATE_IS_INSECURE: 8, STATE_STOP: 16,
      LOCATION_CHANGE_SAME_DOCUMENT: 32 },
      nsIWebProgress: { NOTIFY_LOCATION: 1, NOTIFY_STATE_DOCUMENT: 2 }, nsIWebNavigation: { LOAD_FLAGS_REPLACE_HISTORY: 64 } },
  });
  vm.runInContext(text.replace("export const NavisAndroidPresentation", "globalThis.api"), sandbox);
  function arrive(index, sameDocument = false) {
    history.index = index;
    history.requestedIndex = -1;
    context.currentURI = uri(history.entries[index].url);
    for (const listener of [...listeners]) listener.onLocationChange({ isTopLevel: true }, null, context.currentURI, sameDocument ? 32 : 0);
  }
  function stop() { for (const listener of [...listeners]) listener.onStateChange({ isTopLevel: true }, null, 16); }
  return { api: sandbox.api, query: (operation, data = {}) => sandbox.api.query(window, operation, data),
    window, browser, history, context, listeners, timers, arrive, stop };
}

let count = 0;
async function test(name, run) { await run(); count++; console.log(`PASS ${name}`); }

await test("presentation captures real history and cannot expose an arbitrary native flag", () => {
  const f = fixture();
  f.browser.navisNativePresentation = false;
  const result = f.query("session:presentation", { native: true });
  assert.equal(f.browser.navisNativePresentation, true);
  assert.equal(f.browser.stops, 1);
  assert.equal(JSON.parse(result.state).history.index, 2);
  assert.equal(result.current.uri, "https://b.test/");
  assert.equal(result.current.security, 3);
  assert.throws(() => f.query("session:presentation", { native: "false" }));
  assert.throws(() => f.query("session:arbitrary"));
  assert.equal(f.browser.loads.length, 0);
});
await test("an already native restore is not stopped again", () => {
  const f = fixture(); f.query("session:presentation", { native: true }); assert.equal(f.browser.stops, 0);
});
await test("showing the same live entry requires no load or traversal", () => {
  const f = fixture();
  const result = f.query("session:traverse", { index: 1, entryId: 2, entryIdentity: "2" });
  assert.equal(result.traversed, false); assert.equal(f.browser.navisNativePresentation, false);
  assert.equal(f.browser.loads.length, 0); assert.equal(f.browser.traversals.length, 0);
});
await test("a requested traversal is not a completed traversal", async () => {
  const f = fixture(); let settled = false;
  const result = f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1" }).then(value => { settled = true; return value; });
  await Promise.resolve(); assert.equal(settled, false); assert.deepEqual(f.browser.traversals, [0]);
  assert.equal(f.listeners.size, 1); f.arrive(0);
  await Promise.resolve(); assert.equal(settled, false); f.stop();
  assert.equal((await result).current.uri, "https://a.test/");
  assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0); assert.equal(f.browser.loads.length, 0);
});
await test("the old source identity is checked, not merely the recycled index", () => {
  const f = fixture(); f.history.entries[0] = { ID: 90, url: "https://other.test/" };
  assert.throws(() => f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1" }));
  assert.equal(f.browser.traversals.length, 0); assert.equal(f.browser.navisNativePresentation, true);
});
function assertCloneIdentityGuard(text) {
  const f = fixture(text);
  f.history.entries[0] = { ID: 1, url: "https://a.test/", children: [{ ID: 11, url: "https://child.test/b" }] };
  assert.throws(() => f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1[10]" }));
}
await test("a cloned top-level ID cannot hide a changed child history entry", () => assertCloneIdentityGuard(source));
await test("removing the real entry-tree identity guard is rejected", () => {
  const mutant = source.replace(" || entryIdentity(entry) !== data.entryIdentity", "");
  assert.notEqual(mutant, source); assert.throws(() => assertCloneIdentityGuard(mutant));
});
await test("cancelled traversal never authorizes the subsequent new load", async () => {
  const f = fixture();
  const result = f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1", native: true });
  const rejected = assert.rejects(result, /did not reach/);
  f.history.requestedIndex = -1;
  for (const listener of [...f.listeners]) listener.onStateChange({ isTopLevel: true }, null, 16);
  await rejected; assert.equal(f.browser.loads.length, 0); assert.equal(f.listeners.size, 0);
});
await test("a new presentation invalidates an older traversal", async () => {
  const f = fixture();
  const result = f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1", native: true });
  const rejected = assert.rejects(result, /superseded/);
  f.query("session:presentation", { native: true }); await rejected;
  assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
});
await test("an old channel stop cannot cancel an in-flight real traversal", async () => {
  const f = fixture(); let settled = false;
  const result = f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1" }).then(value => { settled = true; return value; });
  for (const listener of [...f.listeners]) listener.onStateChange({ isTopLevel: true }, null, 16);
  await Promise.resolve(); assert.equal(settled, false);
  f.arrive(0); f.stop(); await result;
});
await test("timeout and owner replacement both clean up their listeners", async () => {
  for (const replaced of [false, true]) {
    const f = fixture(); const result = f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1" });
    const rejected = assert.rejects(result, replaced ? /owner changed/ : /timed out/);
    if (replaced) { f.window.closed = true; f.arrive(0); f.stop(); }
    else for (const timeout of f.timers.values()) timeout();
    await rejected; assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
  }
});
await test("a native link branches only after its real predecessor is committed", async () => {
  const f = fixture();
  const result = f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1", native: true });
  assert.throws(() => f.query("session:load", { index: 0, entryId: 1, entryIdentity: "1", uri: "https://c.test/" }));
  f.arrive(0); f.stop(); await result;
  assert.equal(f.browser.navisNativePresentation, true);
  f.query("session:load", { index: 0, entryId: 1, entryIdentity: "1", uri: "https://c.test/" });
  assert.equal(f.browser.loads[0].uri, "https://c.test/"); assert.equal(f.browser.loads[0].options.loadFlags, 0);
  assert.equal(f.browser.navisNativePresentation, false);
});
await test("a root-native link replaces only the old first backing entry", () => {
  const f = fixture(); assert.throws(() => f.query("session:load", { root: true, uri: "https://c.test/" }));
  f.history.index = 0; f.query("session:load", { root: true, uri: "https://c.test/" });
  assert.equal(f.browser.loads[0].options.loadFlags, 64);
  assert.throws(() => f.query("session:load", { root: true, uri: "https://d.test/" }));
});
await test("BFCache may replace the BrowsingContext but still waits for document STOP", async () => {
  const f = fixture(); let settled = false;
  const result = f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1" }).then(value => { settled = true; return value; });
  f.arrive(0);
  f.browser.browsingContext = { ...f.context, currentWindowGlobal: { documentTitle: "Restored A" } };
  await Promise.resolve(); assert.equal(settled, false);
  f.stop(); assert.equal((await result).current.title, "Restored A"); assert.equal(f.listeners.size, 0);
});
await test("same-document history has no STOP and completes at its own location", async () => {
  const f = fixture();
  const result = f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1" });
  f.arrive(0, true); assert.equal((await result).current.uri, "https://a.test/");
  assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
});
await test("replacing the actual product browser owner still rejects a traversal", async () => {
  const f = fixture();
  const result = f.query("session:traverse", { index: 0, entryId: 1, entryIdentity: "1" });
  const rejected = assert.rejects(result, /owner changed/);
  f.window.tab.linkedBrowser = {}; f.arrive(0); f.stop(); await rejected;
});
await test("unknown data and oversized snapshots remain rejected", () => {
  const f = fixture();
  assert.throws(() => f.query("session:traverse", { index: -1, entryId: 1, entryIdentity: "1" }));
  assert.throws(() => f.query("session:load", { root: true, uri: "navis://settings/" }));
  f.history.entries = Array.from({ length: 513 }, (_, index) => ({ ID: index + 1, url: "https://a.test/" }));
  assert.throws(() => f.query("session:history"));
});
console.log(`${count} focused Android presentation tests passed`);
