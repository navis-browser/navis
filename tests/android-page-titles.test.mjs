import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const source = await readFile(new URL(
  "../gecko/mobile/shared/chrome/navis/host.js", import.meta.url,
), "utf8");
const START = 1, STOP = 2, NETWORK = 4, SAME = 16, ERROR = 32, RESTORING = 64;

// Execute the actual tracker, title bridge, progress callbacks and registration;
// no duplicate implementation of their acceptance rules in the test.
function fixture(input = source, privateMode = false) {
  const events = [], listeners = new Map();
  const browser = {
    browsingContext: { id: 7, originAttributes: { privateBrowsingId: privateMode ? 1 : 0 } },
    addEventListener(name, listener) { listeners.set(name, listener); },
  };
  const sandbox = {
    Ci: { nsIChannel: {}, nsIWebProgressListener: {
      STATE_START: START, STATE_STOP: STOP, STATE_IS_NETWORK: NETWORK,
      LOCATION_CHANGE_SAME_DOCUMENT: SAME, LOCATION_CHANGE_ERROR_PAGE: ERROR,
      STATE_RESTORING: RESTORING,
    } },
    Cr: { NS_BINDING_ABORTED: 0x804b0002, NS_BINDING_REDIRECTED: 0x804b0003,
      NS_BINDING_RETARGETED: 0x804b0004, NS_ERROR_ABORT: 0x80004004 },
    ChromeUtils: { generateQI() {} }, shuttingDown: false, browser,
    dispatcher: { dispatch: (name, data) => events.push({ name, ...data }) },
    faviconBridge: { invalidate() {} }, reportNavigationDiagnostic() {},
    dispatchHistoryState() {}, dispatchSessionState() {},
  };
  const body = input.slice(input.indexOf("class AndroidNavigationProgress {"),
    input.indexOf("function dispatchHistoryState()"));
  const registration = input.match(/browser\.addEventListener\([^;]*, pageTitles\);/u)?.[0];
  assert.ok(registration, "production title listener must be registered");
  vm.runInNewContext(`${body}\n${registration}\nthis.progress = progressListener;`, sandbox);
  const request = uri => ({ QueryInterface: () => ({ URI: { spec: uri } }) });
  return {
    events, browser,
    titles() { return events.filter(e => e.name === "NavisAndroid:Title"); },
    document(uri, title, id = 1) {
      browser.browsingContext.currentWindowGlobal = { documentURI: { spec: uri }, documentTitle: title, innerWindowId: id };
    },
    start(uri, restoring = false) {
      sandbox.progress.onStateChange({ isTopLevel: true }, request(uri), START | NETWORK | (restoring ? RESTORING : 0), 0);
    },
    location(uri, flags = 0, top = true) {
      sandbox.progress.onLocationChange({ isTopLevel: top }, request(uri), { spec: uri }, flags);
    },
    stop(uri, status = 0) {
      sandbox.progress.onStateChange({ isTopLevel: true }, request(uri), STOP | NETWORK, status);
    },
    titleEvent(target = browser) {
      listeners.get("pagetitlechanged")?.handleEvent({ type: "pagetitlechanged", target, title: "UNTRUSTED EVENT TITLE" });
    },
    shutdown() { sandbox.shuttingDown = true; },
  };
}

function checkNativeToWeb(input = source) {
  const f = fixture(input);
  f.document("about:blank", "Navis");
  f.start("https://page.test/"); f.location("https://page.test/");
  f.titleEvent();
  assert.equal(f.titles().length, 0, "native/previous document cannot supply the web title");
  f.document("https://page.test/", "Actual page", 2); f.titleEvent();
  assert.equal(f.titles().at(-1)?.title, "Actual page");
  assert.equal(f.titles().at(-1)?.uri, "https://page.test/");
  assert.equal(f.titles().at(-1)?.generation, 1);
}

test("remote parent event delivers web title, never newtab's Navis placeholder", () => checkNativeToWeb());

test("old DOMTitleChanged registration is a killed regression mutation", () => {
  assert.throws(() => checkNativeToWeb(source.replace(
    'browser.addEventListener("pagetitlechanged", pageTitles)',
    'browser.addEventListener("DOMTitleChanged", pageTitles)',
  )), assert.AssertionError);
});

test("dynamic and empty titles update; duplicate and foreign events do not", () => {
  const f = fixture();
  f.start("https://page.test/"); f.document("https://page.test/", "First"); f.location("https://page.test/");
  f.document("https://page.test/", "Dynamic"); f.titleEvent({});
  assert.equal(f.titles().at(-1).title, "First");
  f.titleEvent(); f.titleEvent();
  assert.equal(f.titles().length, 2);
  f.document("https://page.test/", ""); f.titleEvent();
  assert.equal(f.titles().at(-1).title, "");
});

test("completion snapshots current title before successful history consumption", () => {
  const f = fixture();
  f.start("https://page.test/"); f.document("https://page.test/", "Early"); f.location("https://page.test/");
  f.document("https://page.test/", "Final title", 2); f.stop("https://page.test/");
  assert.equal(f.events.at(-2).name, "NavisAndroid:Title");
  assert.equal(f.events.at(-2).title, "Final title");
  assert.equal(f.events.at(-1).name, "NavisAndroid:LoadComplete");
});

test("BFCache/back refreshes reused WindowGlobal without a title event", () => {
  const f = fixture();
  for (const [uri, title, id, restoring] of [
    ["https://a.test/", "Page A", 1, false], ["https://b.test/", "Page B", 2, false],
    ["https://a.test/", "Page A restored", 1, true],
  ]) {
    f.start(uri, restoring); f.document(uri, title, id); f.location(uri); f.stop(uri);
    assert.equal(f.titles().at(-1).title, title);
  }
  assert.equal(f.titles().at(-1).generation, 3);
});

test("late old-document event cannot title the next navigation", () => {
  const f = fixture();
  f.start("https://old.test/"); f.document("https://old.test/", "Old"); f.location("https://old.test/"); f.stop("https://old.test/");
  f.start("https://new.test/"); f.titleEvent(); f.location("https://new.test/"); f.titleEvent();
  assert.equal(f.titles().length, 1);
  f.document("https://new.test/", "Current", 2); f.titleEvent();
  assert.equal(f.titles().at(-1).generation, 2);
  assert.equal(f.titles().at(-1).title, "Current", "only current WindowGlobal, never event payload");
});

test("same-URL reload takes final new document's title, including empty title", () => {
  const f = fixture();
  f.start("https://page.test/"); f.document("https://page.test/", "Before reload"); f.location("https://page.test/"); f.stop("https://page.test/");
  f.start("https://page.test/"); f.location("https://page.test/");
  f.document("https://page.test/", "", 2); f.stop("https://page.test/");
  assert.equal(f.titles().at(-1).title, "");
  assert.equal(f.titles().at(-1).generation, 2);
});

test("same-document location keeps current URI correlation; subframes cannot replace it", () => {
  const f = fixture();
  f.start("https://page.test/"); f.document("https://page.test/", "Page"); f.location("https://page.test/");
  f.location("https://frame.test/", 0, false);
  f.location("https://page.test/#fragment", SAME);
  assert.equal(f.titles().at(-1).uri, "https://page.test/#fragment");
  assert.equal(f.titles().at(-1).generation, 1);
});

test("overlap, error document, shutdown and unbounded URI fail closed", () => {
  const f = fixture();
  f.start("https://page.test/"); f.start("https://page.test/");
  f.document("https://page.test/", "Ambiguous"); f.location("https://page.test/"); f.titleEvent();
  assert.equal(f.titles().length, 0);
  f.stop("https://page.test/"); f.stop("https://page.test/");
  f.start("https://page.test/"); f.location("https://page.test/", ERROR); f.titleEvent();
  assert.equal(f.titles().length, 0);
  f.location("https://page.test/" + "a".repeat(65536)); f.titleEvent();
  assert.equal(f.titles().length, 0);
  f.location("https://page.test/"); const count = f.titles().length;
  f.shutdown(); f.document("https://page.test/", "After shutdown"); f.titleEvent();
  assert.equal(f.titles().length, count);
});

test("private tabs receive actual titles without a profile/history write in the host", () => {
  const f = fixture(source, true);
  f.start("https://private.test/"); f.document("https://private.test/", "Private title"); f.location("https://private.test/");
  assert.equal(f.titles().at(-1).title, "Private title");
  assert.deepEqual(f.events.map(e => e.name), ["NavisAndroid:Loading", "NavisAndroid:Location", "NavisAndroid:Title"]);
});
