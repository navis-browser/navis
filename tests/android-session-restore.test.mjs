import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const host = await readFile(new URL("../gecko/mobile/shared/chrome/navis/host.js", import.meta.url), "utf8");
const restoreSource = host.slice(host.indexOf("function beginSessionRestore("), host.indexOf("function createRemoteBrowser("));
const collectSource = host.slice(host.indexOf("function collectSessionState("), host.indexOf("function dispatchSessionState("));
const generations = host.slice(host.indexOf("let sessionRestoreGeneration ="), host.indexOf("let navigationDiagnosticPending ="));
const commandGuard = host.slice(host.indexOf("  onEvent(event, data, callback) {") + "  onEvent(event, data, callback) {".length,
  host.indexOf("    switch (event) {"));
const loadCase = host.slice(host.indexOf('case "NavisAndroid:Load":'), host.indexOf('case "NavisAndroid:LoadExtensionPopup":'));
const stopCase = host.slice(host.indexOf('case "NavisAndroid:Stop":'), host.indexOf('case "NavisAndroid:Back":'));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture() {
  const caps = deferred(), calls = [], loadedEntries = [], directLoads = [];
  const history = { entries: [], index: -1 };
  const context = { sessionHistory: history };
  const restoreData = { nativeRestoreData: true };
  const sandbox = {
    MAX_SESSION_STATE_CHARS: 1024 * 1024,
    MAX_SESSION_HISTORY_ENTRIES: 512,
    window: { closed: false }, shuttingDown: false,
    browser: { browsingContext: context,
      stop: () => calls.push("stop"), loadURI: uri => directLoads.push(uri) },
    SessionHistory: { restoreFromParent(target, serialized) {
      calls.push("history");
      target.entries = serialized.entries;
      target.index = serialized.index - 1;
    }, collectFromParent(uri, allow, target) { return { entries: target.entries, index: target.index + 1 }; } },
    SessionStoreUtils: {
      restoreDocShellState(received, url, disallow) {
        assert.equal(received, context);
        calls.push(["caps", url, disallow]);
        return caps.promise.then(() => { context.currentURI = { spec: url }; });
      },
      constructSessionStoreRestoreData: () => restoreData,
      initializeRestore(received, data) {
        assert.equal(received, context);
        assert.equal(data, restoreData);
        // Model the pinned C++ ReloadCurrentEntry, not a URL-only navigation.
        loadedEntries.push(received.sessionHistory.entries[received.sessionHistory.index]);
        calls.push("initializeRestore");
        return Promise.resolve();
      },
    },
    Services: { io: { newURI: uri => uri }, scriptSecurityManager: { getSystemPrincipal: () => "system" } },
    NavisAndroidPresentation: { cancelTraversal() {} },
    reportNavigationDiagnostic() {},
  };
  vm.runInNewContext(`${generations}\n${collectSource}\n${restoreSource}\nthis.restore = restoreSessionState;
    this.begin = beginSessionRestore; this.readResult = readSessionRestoreResult;
    this.command = function(event, data) { ${commandGuard} switch(event) { ${loadCase} ${stopCase} } };`, sandbox);
  return { sandbox, caps, calls, loadedEntries, directLoads, history };
}

const entries = [
  { url: "https://example.com/first" },
  { url: "https://example.com/selected", cacheKey: 42, triggeringPrincipal_base64: "principal" },
  { url: "https://example.com/forward" },
];
const encoded = (items = entries, index = 2) => JSON.stringify({ version: 1, history: { entries: items, index, disallow: "Plugins" } });

test("canonical restore waits for docshell caps then loads selected history entry with metadata intact", async () => {
  const { sandbox, caps, calls, loadedEntries, directLoads, history } = fixture();
  const restored = sandbox.restore(encoded());
  assert.equal(history.entries.length, 3);
  assert.equal(history.index, 1);
  assert.deepEqual(loadedEntries, []);
  assert.deepEqual(calls, ["stop", "history", ["caps", entries[1].url, "Plugins"]]);
  caps.resolve();
  await restored;
  assert.equal(loadedEntries.length, 1);
  assert.equal(JSON.stringify(loadedEntries[0]), JSON.stringify(entries[1]));
  assert.equal(history.index, 1);
  assert.deepEqual(directLoads, [], "Restoration must not replace history with loadURI");
});

test("new user navigation and stop cancel a pending restore", async () => {
  for (const event of ["NavisAndroid:Load", "NavisAndroid:Stop"]) {
    const { sandbox, caps, loadedEntries, directLoads } = fixture();
    const pending = sandbox.restore(encoded());
    sandbox.command(event, { uri: "https://example.org/new" });
    caps.resolve();
    await assert.rejects(pending, /lost its owner/);
    assert.deepEqual(loadedEntries, [], event);
    assert.deepEqual(directLoads, event.endsWith(":Load") ? ["https://example.org/new"] : []);
  }
});

test("closed windows and replaced browsing contexts never resume stale restoration", async () => {
  for (const terminate of [s => { s.window.closed = true; }, s => { s.shuttingDown = true; },
    s => { s.browser.browsingContext = {}; }, s => { s.browser = { browsingContext: s.browser.browsingContext }; }]) {
    const { sandbox, caps, loadedEntries } = fixture();
    const pending = sandbox.restore(encoded());
    terminate(sandbox);
    caps.resolve();
    await assert.rejects(pending, /lost its owner/);
    assert.deepEqual(loadedEntries, []);
  }
});

test("a newer restore replaces the old pending restore, including an empty history", async () => {
  for (const newer of [encoded([entries[2]], 1), encoded([], 0)]) {
    const { sandbox, caps, loadedEntries } = fixture();
    const first = sandbox.restore(encoded());
    const second = sandbox.restore(newer);
    caps.resolve();
    const outcome = await Promise.allSettled([first, second]);
    assert.equal(outcome[0].status, "rejected"); assert.equal(outcome[1].status, "fulfilled");
    assert.equal(loadedEntries.length, JSON.parse(newer).history.entries.length);
    if (loadedEntries.length) assert.equal(loadedEntries[0].url, entries[2].url);
  }
});

test("invalid history rejects before changing the live browser", () => {
  for (const value of ["", "not JSON", encoded(entries, 0), encoded(entries, 4),
    encoded([{ url: "" }], 1), encoded(Array(513).fill(entries[0]), 1)]) {
    const { sandbox, calls } = fixture();
    assert.throws(() => sandbox.restore(value));
    assert.deepEqual(calls, []);
  }
});

test("docshell restore rejection propagates without loading a fallback GET", async () => {
  const { sandbox, caps, loadedEntries, directLoads } = fixture();
  const pending = sandbox.restore(encoded());
  const failure = new Error("caps failure");
  caps.reject(failure);
  await assert.rejects(pending, value => value === failure);
  assert.deepEqual(loadedEntries, []);
  assert.deepEqual(directLoads, []);
});

test("owner acknowledgement waits for actual restoration, then exposes real installed history", async () => {
  const { sandbox, caps, loadedEntries } = fixture();
  await assert.rejects(sandbox.readResult(), /No restore transaction/);
  sandbox.begin(encoded());
  let delivered = false;
  const reply = sandbox.readResult().then(value => { delivered = true; return value; });
  await Promise.resolve(); await Promise.resolve();
  assert.equal(delivered, false); assert.equal(loadedEntries.length, 0);
  caps.resolve();
  const value = await reply;
  assert.equal(value.restored, true); assert.equal(value.uri, entries[1].url);
  assert.equal(JSON.parse(value.state).history.index, 2);
  assert.equal(JSON.parse(value.state).history.entries[1].cacheKey, 42);
  assert.equal(loadedEntries.length, 1);
});

test("failed, synchronously invalid, stopped and superseded transactions never acknowledge success", async () => {
  {
    const { sandbox, caps } = fixture(); sandbox.begin(encoded());
    const result = sandbox.readResult(); caps.reject(new Error("restore failure"));
    await assert.rejects(result, /restore failure/);
  }
  {
    const { sandbox } = fixture(); sandbox.begin("invalid JSON");
    await assert.rejects(sandbox.readResult());
  }
  for (const change of [s => s.command("NavisAndroid:Stop", {}), s => s.begin(encoded([entries[0]], 1)),
    s => { s.window.closed = true; }]) {
    const { sandbox, caps } = fixture(); sandbox.begin(encoded());
    const pending = sandbox.readResult(); change(sandbox); caps.resolve();
    await assert.rejects(pending);
  }
});

test("a navigation after successful restoration invalidates its old acknowledgement", async () => {
  const { sandbox, caps } = fixture(); sandbox.begin(encoded()); caps.resolve();
  assert.equal((await sandbox.readResult()).restored, true);
  sandbox.command("NavisAndroid:Load", { uri: "https://new.test/" });
  await assert.rejects(sandbox.readResult(), /superseded/);
});

test("pinned Gecko API reloads current entry and serializer retains entry index and cache key", async () => {
  const cpp = await readFile(new URL("../gecko/toolkit/components/sessionstore/SessionStoreUtils.cpp", import.meta.url), "utf8");
  const initializer = cpp.slice(cpp.indexOf("SessionStoreUtils::InitializeRestore("), cpp.indexOf("void SessionStoreUtils::RestoreDocShellState("));
  assert.match(initializer, /aContext\.SetRestoreData\(data, aError\)/);
  assert.match(initializer, /shistory->ReloadCurrentEntry\(\)/);
  const history = await readFile(new URL("../gecko/toolkit/modules/sessionstore/SessionHistory.sys.mjs", import.meta.url), "utf8");
  assert.match(history, /history\.index = index/);
  assert.match(history, /shEntry\.cacheKey = entry\.cacheKey/);
  assert.match(history, /shEntry\.setLoadTypeAsHistory\(\)/);
  assert.match(history, /shEntry\.triggeringPrincipal =/);
});
