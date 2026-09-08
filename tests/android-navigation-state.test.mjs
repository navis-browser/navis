import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const source = await readFile(new URL(
  "../gecko/mobile/shared/chrome/navis/host.js", import.meta.url,
), "utf8");
const tracker = source.slice(source.indexOf("class AndroidNavigationProgress {"),
  source.indexOf("const progressListener ="));
const progress = source.slice(source.indexOf("const progressListener ="),
  source.indexOf("function dispatchHistoryState()"));
const START = 1, STOP = 2, NETWORK = 4, SAME = 16, ERROR = 32;
function fixture() {
  const events = [];
  const sandbox = {
    Ci: { nsIChannel: {}, nsIWebProgressListener: {
      STATE_START: START, STATE_STOP: STOP, STATE_IS_NETWORK: NETWORK,
      LOCATION_CHANGE_SAME_DOCUMENT: SAME, LOCATION_CHANGE_ERROR_PAGE: ERROR,
    } },
    Cr: { NS_BINDING_ABORTED: 0x804b0002, NS_BINDING_REDIRECTED: 0x804b0003,
      NS_BINDING_RETARGETED: 0x804b0004, NS_ERROR_ABORT: 0x80004004 },
    ChromeUtils: { generateQI() {} },
    shuttingDown: false,
    browser: { browsingContext: { id: 7 } },
    dispatcher: { dispatch: (name, data) => events.push({ name, ...data }) },
    faviconBridge: { invalidate() {} },
    reportNavigationDiagnostic() {}, dispatchHistoryState() {}, dispatchSessionState() {},
  };
  vm.runInNewContext(`${tracker}\n${progress}\nthis.progress = progressListener;`, sandbox);
  const remote = (uri = "https://example.com/") => ({
    get status() { throw Error("NS_ERROR_NOT_IMPLEMENTED"); },
    QueryInterface: () => ({ URI: { spec: uri } }),
  });
  return {
    events,
    start(uri) { sandbox.progress.onStateChange({ isTopLevel: true }, remote(uri), START | NETWORK, 0); },
    stop(status, uri) { sandbox.progress.onStateChange({ isTopLevel: true }, remote(uri), STOP | NETWORK, status); },
    location(flags, uri = "https://example.com/") {
      sandbox.progress.onLocationChange({ isTopLevel: true }, remote(uri), { spec: uri }, flags);
    },
  };
}

test("real remote wrappers need no status getter or object identity", () => {
  const f = fixture();
  f.start();
  assert.equal(f.events.length, 1);
  f.stop(0x804b001e);
  assert.equal(f.events.length, 2);
  assert.equal(f.events[1].name, "NavisAndroid:LoadComplete");
  assert.equal(f.events[1].status, 0x804b001e | 0);
  assert.equal(f.events[1].statusKnown, true);
  assert.equal(f.events[1].generation, 1);
  assert.equal(f.events[1].cancelled, false);
  assert.equal(f.events.filter(e => e.loading === false).length, 0);
});

test("redirects retain a single NETWORK pair; sequential links get new generations", () => {
  const f = fixture();
  f.start("https://first.example/");
  f.location(0, "https://redirected.example/");
  f.stop(0, "https://redirected.example/");
  f.start("https://next.example/");
  f.stop(0, "https://next.example/");
  assert.deepEqual(f.events.filter(e => e.name.endsWith("LoadComplete")).map(e => e.generation), [1, 2]);
});

test("stop/replacement/retarget statuses are not transport failures", () => {
  for (const status of [0x804b0002, 0x804b0003, 0x804b0004, 0x80004004]) {
    const f = fixture(); f.start(); f.stop(status);
    assert.equal(f.events[1].cancelled, true);
  }
});

test("overlapping loads report uncorrelated completion and recover on next pair", () => {
  const f = fixture();
  f.start(); f.start(); f.stop(0); f.stop(0x804b001e);
  assert.deepEqual(f.events.filter(e => e.name.endsWith("LoadComplete")).map(e => e.generation), [0, 0]);
  f.start(); f.stop(0);
  assert.equal(f.events.at(-1).generation, 3);
});

test("error-page identity is a Gecko location flag, never URL inference", () => {
  const f = fixture();
  f.start(); f.location(ERROR); f.stop(0);
  assert.equal(f.events.at(-1).errorPage, true);
  f.location(SAME, "https://example.com/#fragment");
  assert.equal(f.events.at(-1).errorPage, true);
  f.start(); f.location(0); f.stop(0);
  assert.equal(f.events.at(-1).errorPage, false);
});

test("unknown or orphan stop reports facts without invented success", () => {
  const f = fixture(); f.stop(undefined);
  assert.equal(f.events[0].generation, 0);
  assert.equal(f.events[0].statusKnown, false);
  assert.equal(f.events[0].cancelled, false);
});

test("URI projection is bounded; error diagnostics remain separate", () => {
  const f = fixture(); f.start(`https://example.com/${"a".repeat(70000)}`);
  assert.equal(f.events[0].uri.length, 65536);
  assert.doesNotMatch(source, /request\??\.status/);
  assert.match(source, /reportNavigationDiagnostic\("stop", \{ request, status \}\)/);
});
