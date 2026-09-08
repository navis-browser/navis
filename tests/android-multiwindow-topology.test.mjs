import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";

const source = readFileSync(new URL("../gecko/mobile/shared/modules/navis/NavisAndroidExtensionTabTopology.sys.mjs", import.meta.url), "utf8");
function fixture() {
  const events = [];
  const context = vm.createContext({ Services: { obs: { notifyObservers(value) { events.push(value.wrappedJSObject); } } } });
  vm.runInContext(source.replaceAll("export const ", "const ") + "\nglobalThis.topology = NavisAndroidExtensionTabTopology;", context);
  const topology = context.topology;
  const windows = [{ windowId: 1, activeTabId: 10, focused: true, width: 720, height: 1000 },
    { windowId: 2, activeTabId: 20, privateMode: true, width: 900, height: 500 }];
  const tabs = [{ tabId: 10, index: 0, windowId: 1, privateMode: false },
    { tabId: 11, index: 1, windowId: 1, privateMode: false },
    { tabId: 20, index: 0, windowId: 2, privateMode: true }];
  function apply(extra = {}) { topology.applySnapshot({ revision: topology.revision + 1, windowId: 1,
    activeTabId: 10, focusedWindowId: 1, lastFocusedWindowId: 1, windows, tabs, ...extra }); }
  function native(id) {
    const host = { document: { documentElement: { getAttribute: () => "navigator:navis-android" } } };
    const tab = { id, browser: { documentGlobal: host } }; host.tab = tab;
    topology.registerNativeTab(tab); return tab;
  }
  return { topology, events, windows, tabs, apply, native };
}

test("each real window owns its own tab order, selection and private filtering", () => {
  const f = fixture(); f.apply(); const a = f.native(10); const b = f.native(20);
  assert.equal(f.topology.selectedNativeTab(10), true);
  assert.equal(f.topology.selectedNativeTab(20), true);
  assert.equal(f.topology.visibleIndex(20), 0);
  assert.equal(f.topology.visibleIndex(11), 1);
  assert.equal(f.topology.productCreateIndex(-1, true, 2), 1);
  assert.equal(f.topology.productMoveIndex(11, 0), 0);
  assert.equal(f.topology.activeNativeTab(true, 2), b);
  assert.equal(f.topology.representativeWindow(true, 1), a.browser.documentGlobal);
  assert.equal(f.topology.representativeWindow(false, 2), null);
  assert.deepEqual(Array.from(f.topology.windows(false), w => w.windowId), [1]);
  assert.equal(f.topology.activeNativeTab(false, 2), null);
});

test("focus switching does not replace other windows or create fake tab activation", () => {
  const f = fixture(); f.apply(); f.native(10); f.native(20);
  const observed = []; f.topology.onWindowEvent(event => observed.push(event));
  f.apply({ windowId: 2, activeTabId: 20, focusedWindowId: 2, lastFocusedWindowId: 2,
    windows: f.windows.map(w => ({ ...w, focused: w.windowId === 2 })) });
  assert.equal(f.topology.nativeTabs().length, 2);
  assert.equal(f.events.length, 0);
  assert.deepEqual(observed.map(e => [e.type, e.windowId]), [["FOCUSED", 2]]);
  assert.equal(f.topology.selectedNativeTab(10), true);
});

test("created events wait for their own native host, removed events retire only their window", () => {
  const f = fixture(); const observed = []; const removed = [];
  f.topology.onWindowEvent(event => observed.push(event)); f.topology.onWindowRemoved(id => removed.push(id));
  f.apply(); assert.ok(!observed.some(e => e.type === "CREATED"));
  f.native(20); assert.deepEqual(observed.filter(e => e.type === "CREATED").map(e => e.windowId), [2]);
  f.native(10); f.apply({ windows: [f.windows[0]], tabs: f.tabs.slice(0, 2),
    events: [{ type: "REMOVED", tabId: 20, isWindowClosing: true }] });
  assert.deepEqual(removed, [2]);
  const projection = f.topology.projectEvent(f.events.at(-1));
  assert.equal(projection.windowId, 2); assert.equal(projection.isWindowClosing, true);
  assert.equal(f.topology.projectEvent(f.events.at(-1), false), null);
  assert.equal(f.topology.nativeTab(10).id, 10);
});

test("cross-window activation, duplicated focus and global indexes are rejected without mutation", () => {
  const f = fixture(); f.apply();
  assert.throws(() => f.apply({ windows: [{ ...f.windows[0], activeTabId: 20 }, f.windows[1]] }));
  assert.throws(() => f.apply({ windows: f.windows.map(w => ({ ...w, focused: true })) }));
  assert.throws(() => f.apply({ tabs: f.tabs.map(t => t.tabId === 20 ? { ...t, index: 2 } : t) }));
  assert.equal(f.topology.revision, 1);
});

test("closing the last real window publishes an empty topology, not a fake replacement", () => {
  const f = fixture(); f.apply();
  f.apply({ windowId: 0, activeTabId: null, windows: [], tabs: [], focusedWindowId: null, lastFocusedWindowId: null,
    events: f.tabs.map(t => ({ type: "REMOVED", tabId: t.tabId, isWindowClosing: true })) });
  assert.equal(f.topology.windowId, 0); assert.equal(f.topology.windows().length, 0);
  assert.equal(f.topology.activeNativeTab(), null);
});
