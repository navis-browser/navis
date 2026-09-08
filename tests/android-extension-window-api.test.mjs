import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const read = path => fs.readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const apiSource = read("gecko/mobile/shared/components/extensions/ext-windows.js");
const hostSource = read("gecko/mobile/shared/modules/navis/NavisAndroidWebExtensionHost.sys.mjs");
const bridgeSource = hostSource.slice(hostSource.indexOf("export const NavisAndroidExtensionWindowBridge"),
  hostSource.indexOf("export const NavisAndroidWebExtensionHost ="));
const plain = value => JSON.parse(JSON.stringify(value));
function fixture(source = bridgeSource) {
  const windows = new Map(), listeners = new Set(), calls = [];
  let focusedWindowId = 1, lastFocusedWindowId = 1;
  let focusOrder = [1];
  let nextToken = 1;
  const add = (windowId, privateMode = false) => {
    const item = { windowId, privateMode, state: "normal", native: { id: windowId, private: privateMode } }; windows.set(windowId, item); return item;
  };
  add(1); add(2, true); add(3);
  const topology = { ready: true, window: id => windows.get(id),
    get focusedWindowId() { return focusedWindowId; }, get lastFocusedWindowId() { return lastFocusedWindowId; },
    lastFocusedWindow(includePrivate) { return focusOrder.map(id => windows.get(id)).find(item => item && (includePrivate || !item.privateMode)); },
    representativeWindow(includePrivate, id) { const item = windows.get(id); return item && (!item.privateMode || includePrivate) ? item.native : null; },
    windows(includePrivate) { return [...windows.values()].filter(item => includePrivate || !item.privateMode); },
    onWindowEvent(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
  const extension = { id: "window@test", name: "Window tester", privateBrowsingAllowed: false, hasShutdown: false,
    canAccessWindow: window => !!window && (!window.private || extension.privateBrowsingAllowed) };
  const dto = native => ({ id: native.id, type: "normal", incognito: native.private, focused: native.id === focusedWindowId,
    state: windows.get(native.id)?.state });
  extension.windowManager = { convert: native => dto(native), getAll: () => [...windows.values()]
    .filter(item => extension.canAccessWindow(item.native)).map(item => ({ type: "normal", convert: () => dto(item.native) })) };
  let liveExtension = extension;
  const transport = { respond: async request => {
    assert.equal(realm.bridge.authorize(request.authorizationToken, request.operation === "CREATE" ? null : request.windowId), true);
    if (request.operation === "REMOVE") {
      windows.delete(request.windowId); return { windowId: request.windowId, removed: true };
    }
    let id = request.windowId;
    if (request.operation === "CREATE") {
      id = 10; add(id, request.privateMode); focusedWindowId = id;
      assert.equal(realm.bridge.authorize(request.authorizationToken, id), true);
    }
    for (const name of ["left", "top", "width", "height"]) if (request[name] != null) windows.get(id)[name] = request[name];
    if (request.state != null) windows.get(id).state = request.state;
    if (request.operation === "FOCUS" || request.focused === true) focusedWindowId = id;
    if ((request.focused === false || request.state === "minimized") && focusedWindowId === id) focusedWindowId = id === 1 ? 3 : 1;
    return { windowId: id, removed: false, attention: request.drawAttention === true };
  }, sendRequestForResult(event, request) { assert.equal(event, "NavisAndroid:Extensions:WindowCommand"); calls.push(request); return this.respond(request); } };
  const realm = { ExtensionError: Error, ExtensionAPIPersistent: class {}, EventManager: class { api() { return {}; } },
    lazy: { NavisAndroidExtensionTabTopology: topology, ExtensionParent: { GlobalManager: { getExtension: () => liveExtension } },
      EventDispatcher: { byName: () => transport } },
    Services: { uuid: { generateUUID: () => ({ toString: () => (nextToken++).toString(16).padStart(32, "0") }) } },
    installError: message => Error(message), DISPATCHER_NAME: "navis-android-extensions", DIRECT_WINDOW_COMMAND: "NavisAndroid:Extensions:WindowCommand",
    windowTracker: { getId: native => native.id, getWindow: id => windows.get(id)?.native,
      getCurrentWindow: () => windows.get(1)?.native },
    ChromeUtils: { importESModule(uri) { return uri.includes("TabTopology") ? { NavisAndroidExtensionTabTopology: topology } :
      { NavisAndroidExtensionWindowBridge: realm.bridge }; } },
  };
  vm.createContext(realm); vm.runInContext(source.replace("export const NavisAndroidExtensionWindowBridge", "this.bridge"), realm);
  vm.runInContext(apiSource, realm);
  const owner = new realm.windows(); owner.extension = extension;
  const context = { extension, uri: { resolve: value => new URL(value, "https://extension.test/").href },
    canAccessWindow: extension.canAccessWindow,
    checkLoadURL: uri => /^https?:/.test(uri), unloaded: false };
  return { api: owner.getAPI(context).windows, owner, bridge: realm.bridge, windows, calls, transport, extension, context, topology,
    add, focus: id => { focusedWindowId = id; lastFocusedWindowId = id; focusOrder = [id, ...focusOrder.filter(old => old !== id)]; }, replaceExtension: value => { liveExtension = value; },
    emit: event => { for (const listener of listeners) listener(event); }, listeners };
}

test("create, actual focus and beforeunload-aware close execute captured product IDs", async () => {
  const f = fixture();
  assert.equal((await f.api.create({ url: ["https://a.test/", "page"] })).id, 10);
  assert.deepEqual(plain(f.calls[0]), { extensionId: "window@test", operation: "CREATE", sourceWindowId: 1,
    privateMode: false, urls: ["https://a.test/", "https://extension.test/page"], focused: true,
    authorizationToken: "00000000000000000000000000000001", extensionName: "Window tester", userActivation: false });
  assert.equal((await f.api.update(3, { focused: true })).focused, true);
  assert.equal(f.calls[1].windowId, 3); assert.equal(f.calls[1].sourceWindowId, 1);
  await f.api.remove(3); assert.equal(f.windows.has(3), false);
  const failed = fixture(); failed.transport.respond = async () => ({ windowId: 3, removed: false });
  await assert.rejects(failed.api.remove(3), /cancelled/); assert.equal(failed.windows.has(3), true);
});
test("invalid task geometry, maximization without user input, and forbidden URLs reject before mutation", async () => {
  const f = fixture();
  for (const options of [{ width: 32769 }, { left: 1000001 }, { width: 400, state: "fullscreen" },
    { state: "maximized" }, { state: "minimized", focused: true }, { type: "popup" },
    { tabId: 10 }, { cookieStoreId: "firefox-container-1" }, { titlePreface: "hi" }, { url: "chrome://global/content/" },
    { url: [] }, { url: Array(129).fill("https://a.test/") }]) await assert.rejects(f.api.create(options));
  for (const options of [{ focused: "false" }, { drawAttention: "true" }, { width: 32769 }, { state: "locked-fullscreen" }]) await assert.rejects(f.api.update(1, options));
  assert.equal(f.calls.length, 0);
});
test("UPDATE geometry uses the same task, implicit normal state and actual partial bounds without an invented gesture restriction", async () => {
  const f = fixture(); const original = f.windows.get(1);
  const result = await f.api.update(1, { width: 20, top: -10, focused: false });
  assert.equal(result.id, 1); assert.equal(result.focused, false);
  assert.equal(f.windows.get(1), original);
  assert.equal(f.calls[0].operation, "UPDATE"); assert.equal(f.calls[0].state, "normal");
  assert.equal(f.calls[0].width, 100); assert.equal(f.calls[0].top, -10);
  assert.equal(f.calls[0].userActivation, false); assert.equal(f.calls[0].left, undefined);
  const g = fixture(); g.transport.respond = async request => {
    g.windows.get(request.windowId).width = 599;
    return { windowId: request.windowId, removed: false };
  };
  await assert.rejects(g.api.update(1, { width: 600 }), /requested window bounds/);
  assert.equal(g.windows.size, 3, "an ignored update never creates a replacement task");
  const h = fixture(); h.windows.get(1).state = "maximized";
  await assert.rejects(h.api.update(1, { width: 600 }), /user input handler/);
  assert.equal(h.calls.length, 0, "actual Task restore still needs its API user-input qualification");
  const moved = fixture(); moved.transport.respond = async request => {
    moved.windows.get(request.windowId).width = request.width; moved.focus(request.windowId);
    return { windowId: request.windowId, removed: false };
  };
  await assert.rejects(moved.api.update(3, { width: 600 }), /requested state/);
});
test("task-mode user activation comes only from the synchronous privileged Gecko call context", async () => {
  const f = fixture();
  await assert.rejects(f.api.update(1, { state: "maximized", userActivation: true }), /user input handler/);
  await assert.rejects(f.bridge.command("window@test", "UPDATE", 1,
    { windowId: 1, state: "maximized", userActivation: true }, f.context), /user input handler/);
  assert.equal(f.calls.length, 0, "extension options cannot manufacture an input capability");
  f.context.callContextData = { isHandlingUserInput: true };
  const pending = f.api.update(1, { state: "maximized" });
  f.context.callContextData = null; // ExtensionParent clears call metadata before native async completion.
  assert.equal(f.calls[0].userActivation, true);
  assert.equal((await pending).state, "maximized");
  assert.equal(f.bridge.authorize(f.calls[0].authorizationToken, 1), false, "completed token cannot be replayed");
  await assert.rejects(f.api.update(1, { state: "normal" }), /user input handler/);
  f.context.callContextData = { isHandlingUserInput: true };
  const restore = await f.api.update(1, { state: "normal" });
  assert.equal(restore.state, "normal");
  f.context.callContextData = null;
  await f.api.update(1, { focused: true });
  assert.equal(f.calls[2].userActivation, false, "later calls do not inherit previous input context");
});
test("creation geometry is bounded, normalized once and checked against actual native bounds", async () => {
  const f = fixture();
  await f.api.create({ left: -20, top: 0, width: 20, height: 300, state: "normal" });
  assert.equal(f.calls[0].width, 100); assert.equal(f.calls[0].height, 300);
  assert.equal(f.calls[0].left, -20); assert.equal(f.calls[0].top, 0);
  const bad = fixture(); bad.transport.respond = async request => {
    bad.add(10); bad.focus(10); bad.windows.get(10).width = 1000;
    return { windowId: 10, removed: false };
  };
  await assert.rejects(bad.api.create({ width: 500 }), /requested window bounds/);
});
test("background create/update, all implemented states and attention use typed UPDATE with real result validation", async () => {
  const f = fixture();
  assert.equal((await f.api.create({ focused: false })).focused, false);
  assert.equal((await f.api.update(1, { focused: false })).focused, false);
  assert.equal(f.calls.at(-1).operation, "UPDATE");
  assert.equal((await f.api.update(1, { state: "fullscreen" })).state, "fullscreen");
  assert.equal((await f.api.update(1, { state: "normal" })).state, "normal");
  assert.equal((await f.api.update(1, { state: "minimized" })).state, "minimized");
  assert.equal((await f.api.update(1, { drawAttention: true })).id, 1);
  const bad = fixture();
  bad.transport.respond = async () => ({ windowId: 1, removed: false });
  await assert.rejects(bad.api.update(1, { drawAttention: true }), /requested state/);
  await assert.rejects(bad.api.update(1, { state: "fullscreen" }), /requested state/);
  await assert.rejects(bad.api.update(1, { focused: false }), /requested state/);
});
test("live native authorization rejects stale context, changed extension, revoked private access and wrong targets", async () => {
  for (const revoke of [f => { f.context.unloaded = true; }, f => f.replaceExtension({}),
    f => { f.extension.privateBrowsingAllowed = false; }]) {
    const f = fixture(); f.extension.privateBrowsingAllowed = true;
    let release;
    f.transport.respond = request => new Promise(resolve => { release = () => resolve({ windowId: 2, removed: false }); });
    const promise = f.api.update(2, { focused: true });
    const token = f.calls[0].authorizationToken;
    assert.equal(f.bridge.authorize(token, 2), true);
    assert.equal(f.bridge.authorize(token, 1), false, "cannot turn the authorization into another target");
    revoke(f); assert.equal(f.bridge.authorize(token, 2), false);
    release(); await assert.rejects(promise);
    assert.equal(f.bridge.authorize(token, 2), false, "finished capability is revoked");
  }
  const f = fixture(); let release;
  f.transport.respond = () => new Promise(resolve => { release = resolve; });
  const promise = f.api.create({ focused: false }); const token = f.calls[0].authorizationToken;
  assert.equal(f.bridge.authorize(token, 1), false, "a create cannot capture a pre-existing window");
  f.add(20); assert.equal(f.bridge.authorize(token, 20), true);
  f.add(21); assert.equal(f.bridge.authorize(token, 21), false, "created identity is single-assignment");
  release({ windowId: 20, removed: false }); await promise;
  assert.equal(f.bridge.authorize(token, 20), false);
});
test("source, target and private mode are checked before dispatch and after permissions change", async () => {
  const f = fixture(); await assert.rejects(f.api.create({ incognito: true })); await assert.rejects(f.api.update(2, { focused: true }));
  await assert.rejects(f.bridge.command("window@test", "CREATE", 99, { privateMode: false, urls: [] }));
  await assert.rejects(f.bridge.command("window@test", "REMOVE", 1, { windowId: 2 })); assert.equal(f.calls.length, 0);
  f.extension.privateBrowsingAllowed = true; const created = await f.api.create({ incognito: true }); assert.equal(created.incognito, true);
  let reply; f.transport.respond = () => new Promise(resolve => { reply = resolve; });
  const pending = f.api.update(10, { focused: true }); f.extension.privateBrowsingAllowed = false;
  f.focus(10); reply({ windowId: 10, removed: false }); await assert.rejects(pending, /requested state/);
});
test("a successful-looking stale response, wrong focus, live removed target or replaced extension is rejected", async () => {
  for (const respond of [async () => ({ windowId: 3, removed: false }), async () => ({ windowId: 99, removed: false })]) {
    const f = fixture(); f.transport.respond = respond; await assert.rejects(f.api.update(3, { focused: true }));
  }
  const f = fixture(); f.transport.respond = async () => ({ windowId: 3, removed: true }); await assert.rejects(f.api.remove(3));
  const g = fixture(); g.transport.respond = async () => { g.replaceExtension({}); return { windowId: 3, removed: false }; };
  await assert.rejects(g.api.update(3, { focused: true }), /extension changed/);
});
test("reads use product windows, private filtering and real last-focused identity", async () => {
  const f = fixture(); assert.deepEqual(plain((await f.api.getAll()).map(item => item.id)), [1, 3]);
  assert.deepEqual(plain(await f.api.getAll({ windowTypes: ["popup"] })), []);
  await assert.rejects(f.api.get(2)); assert.equal((await f.api.getCurrent()).id, 1);
  f.focus(3); assert.equal((await f.api.getLastFocused()).id, 3);
  f.focus(2); assert.equal((await f.api.getLastFocused()).id, 3, "last real normal focus survives later private focus");
  f.extension.privateBrowsingAllowed = true; assert.equal((await f.api.getLastFocused()).id, 2);
  f.windows.delete(2); assert.equal((await f.api.getLastFocused()).id, 3, "removed windows cannot remain last focused");
});
test("persistent lifecycle events filter private, retain removed identity and deduplicate invisible focus", () => {
  const f = fixture(), seen = [];
  const registrations = Object.entries(f.owner.PERSISTENT_EVENTS).map(([name, register]) =>
    register({ fire: { async: value => seen.push([name, plain(value)]) } }));
  const previous = { windows: [...f.windows.values()] }, next = { windows: [...f.windows.values()] };
  f.emit({ type: "CREATED", windowId: 1, previous, next });
  f.emit({ type: "CREATED", windowId: 2, previous, next });
  f.emit({ type: "REMOVED", windowId: 3, previous, next });
  f.emit({ type: "FOCUSED", windowId: 2, previous, next });
  f.emit({ type: "FOCUSED", windowId: -1, previous, next });
  assert.deepEqual(seen.map(([name, value]) => [name, typeof value === "object" ? value.id : value]),
    [["onCreated", 1], ["onRemoved", 3], ["onFocusChanged", -1]]);
  registrations[0].convert({ async: () => seen.push(["converted", 1]) });
  f.emit({ type: "CREATED", windowId: 1, previous, next }); assert.equal(seen.at(-1)[0], "converted");
  registrations.forEach(item => item.unregister()); assert.equal(f.listeners.size, 0);
});
test("mutation removing post-command focus validation fails the real response contract", async () => {
  const f = fixture(bridgeSource.replace("topology.focusedWindowId !== id", "false"));
  f.transport.respond = async () => ({ windowId: 3, removed: false });
  const escaped = await f.api.update(3, { focused: true });
  assert.notEqual(escaped.focused, true, "mutated response violates the production focus-success assertion");
});
