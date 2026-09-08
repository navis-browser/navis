import assert from "node:assert/strict";
import { OmniboxSuggestions } from "../product/chrome/content/omnibox-suggestions.mjs";
const pendingLocal = [], pendingRemote = [], emitted = [];
const timers = new Map();
let nextTimer = 1;
const controller = new OmniboxSuggestions({
  local: (query, {signal}) => new Promise(resolve => pendingLocal.push({query, signal, resolve})),
  remote: (query, {signal}) => new Promise(resolve => pendingRemote.push({query, signal, resolve})),
  changed: value => emitted.push(value),
  schedule: callback => { const id = nextTimer++; timers.set(id, callback); return id; },
  unschedule: id => timers.delete(id),
});
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
controller.update("first");
await flush();
controller.update("second");
await flush();
assert.ok(pendingLocal[0].signal.aborted);
pendingLocal[0].resolve([{kind: "history", id: "old", title: "Old", url: "https://old.test/"}]);
await flush();
assert.equal(emitted.at(-1).query, "second");
assert.deepEqual(emitted.at(-1).rows, []);
pendingLocal[1].resolve([{kind: "bookmark", id: "new", title: "New", url: "https://new.test/"}]);
await flush();
assert.equal(emitted.at(-1).rows[0].id, "new");
for (const callback of timers.values()) callback();
timers.clear();
await flush();
assert.equal(pendingRemote.length, 1);
pendingRemote[0].resolve(["second", "second choice", "second choice"]);
await flush();
assert.equal(emitted.at(-1).rows.length, 2);
assert.equal(emitted.at(-1).rows[1].text, "second choice");
controller.clear();
assert.ok(pendingRemote[0].signal.aborted);
assert.deepEqual(emitted.at(-1).rows, []);
assert.equal(timers.size, 0);
controller.update("中文", {composing: true});
await flush();
assert.equal(pendingLocal.length, 2);
assert.equal(pendingRemote.length, 1);
controller.update("   ");
await flush();
assert.equal(timers.size, 0);

// Browser timers require their Window receiver. Injected arrow schedulers above
// cannot catch storing a bare setTimeout and calling it as a controller method.
const originalSetTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;
const defaultTimers = new Map(), cancelledTimers = [], defaultStates = [];
let nextDefaultTimer = 1;
try {
  globalThis.setTimeout = function(callback, delay) {
    assert.equal(this, globalThis, "default scheduling must retain the global timer receiver");
    assert.equal(delay, 250);
    const handle = nextDefaultTimer++;
    defaultTimers.set(handle, callback);
    return handle;
  };
  globalThis.clearTimeout = function(handle) {
    assert.equal(this, globalThis, "default cancellation must retain the global timer receiver");
    cancelledTimers.push(handle);
    defaultTimers.delete(handle);
  };
  const defaults = new OmniboxSuggestions({
    local: () => [], remote: query => [`${query} travel`],
    changed: state => defaultStates.push(state),
  });
  defaults.update("Nepal");
  defaults.update("尼泊尔");
  assert.deepEqual(cancelledTimers, [1]);
  assert.equal(defaultTimers.size, 1);
  const callback = defaultTimers.get(2);
  defaultTimers.delete(2);
  callback();
  await flush();
  assert.equal(defaultStates.at(-1).query, "尼泊尔");
  assert.equal(defaultStates.at(-1).rows[0].text, "尼泊尔 travel");
  defaults.clear();
  assert.deepEqual(defaultStates.at(-1).rows, []);
} finally {
  globalThis.setTimeout = originalSetTimeout;
  globalThis.clearTimeout = originalClearTimeout;
}
console.log("Omnibox suggestion cancellation, IME and merging checks passed");
