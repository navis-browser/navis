import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const host = read("gecko/mobile/shared/chrome/navis/host.js");
const handler = host.slice(host.indexOf("function onPopupWindowClose(event) {"), host.indexOf("\nconst progressListener ="));
assert.ok(handler.startsWith("function onPopupWindowClose(event) {"));
const child = read("gecko/toolkit/actors/BrowserElementChild.sys.mjs");
const parent = read("gecko/toolkit/actors/BrowserElementParent.sys.mjs");
const token = "a".repeat(32);

function fixture(source = handler) {
  const records = [];
  const context = vm.createContext({
    shuttingDown: false,
    dispatcher: { dispatch: (name, data) => records.push([name, { ...data }]) },
    window: { name: `navis-android-session-4503599627370497:popup-${token}`, tab: { id: 4503599627370497, isPopup: true } },
    browser: null,
    JSWindowActorChild: class {}, JSWindowActorParent: class {},
  });
  vm.runInContext(`${source}\n${child.replace("export class", "class")}\n${parent.replace("export class", "class")}
    this.Child = BrowserElementChild; this.Parent = BrowserElementParent;`, context);
  class ChromeEvent {
    constructor(type, options) { Object.assign(this, options, { type, isTrusted: false }); }
    preventDefault() { this.prevented = true; }
    stopPropagation() { this.stopped = true; }
  }
  const browser = {
    isRemoteBrowser: true, documentGlobal: { CustomEvent: ChromeEvent },
    dispatchEvent(event) { event.target = this; context.onPopupWindowClose(event); },
  };
  context.browser = browser;
  const top = { parent: null, embedderElement: browser };
  const receiver = new context.Parent(); receiver.manager = { browsingContext: top };
  const sender = new context.Child(); sender.manager = { browsingContext: top };
  sender.sendAsyncMessage = name => receiver.receiveMessage({ name });
  return { context, browser, records, sender, receiver,
    event: (target = browser) => Object.assign(new ChromeEvent("DOMWindowClose", {}), { target }) };
}

test("pinned Gecko child/parent forwarding closes the exact remote popup, even with synthetic chrome events", () => {
  const f = fixture();
  f.sender.handleEvent({ type: "DOMWindowClose" });
  assert.deepEqual(f.records, [["NavisAndroid:PopupClose", { targetToken: token }]]);
  const event = f.event(); f.context.onPopupWindowClose(event);
  assert.equal(event.prevented, true); assert.equal(event.stopped, true);
});

test("iframes, different chrome embedders, ordinary/new-window tabs and stale hosts cannot close another session", () => {
  const f = fixture();
  f.sender.manager = { browsingContext: { parent: {} } };
  f.sender.handleEvent({ type: "DOMWindowClose" });
  f.receiver.manager = { browsingContext: { parent: {} } };
  f.receiver.receiveMessage({ name: "DOMWindowClose" });
  f.context.onPopupWindowClose(f.event({}));
  const popupName = f.context.window.name;
  for (const name of ["navis-android-session-4503599627370497", `navis-android-session-4503599627370497:${token}`,
    `navis-android-session-4503599627370498:popup-${token}`, `navis-android-session-4503599627370497:popup-${"A".repeat(32)}`,
    `navis-android-session-9007199254740992:popup-${token}`, `${popupName}-suffix`]) {
    f.context.window.name = name; f.context.onPopupWindowClose(f.event());
  }
  f.context.window.name = popupName; f.context.window.tab.isPopup = false;
  f.context.onPopupWindowClose(f.event()); f.context.window.tab.isPopup = true;
  f.context.shuttingDown = true; f.context.onPopupWindowClose(f.event());
  f.context.shuttingDown = false; f.context.dispatcher = null;
  f.context.onPopupWindowClose(f.event());
  assert.deepEqual(f.records, []);
});

test("registration lifetime is balanced; old missing-bridge and isTrusted mutations lose the real close request", () => {
  assert.equal(host.split('browser.addEventListener("DOMWindowClose", onPopupWindowClose);').length, 2);
  assert.equal(host.split('browser.removeEventListener("DOMWindowClose", onPopupWindowClose);').length, 2);
  for (const mutation of ["function onPopupWindowClose(event) {}", handler.replace("if (shuttingDown", "if (!event.isTrusted || shuttingDown")]) {
    const f = fixture(mutation); f.sender.handleEvent({ type: "DOMWindowClose" });
    assert.deepEqual(f.records, []);
  }
});

test("only tokenized popup browsers receive Gecko's script-close capability before frame creation", () => {
  const createTab = host.slice(host.indexOf("function createNavisNativeTab(contentBrowser) {"), host.indexOf("\nfunction startup() {"));
  for (const suffix of ["", `:${token}`, `:popup-${token}`]) {
    const attributes = new Map();
    const browser = { setAttribute: (name, value) => attributes.set(name, value) };
    const fn = Function("window", `${createTab}; return createNavisNativeTab;`)({ name: `navis-android-session-1${suffix}` });
    const tab = fn(browser);
    assert.equal(tab.isPopup, suffix.startsWith(":popup-"));
    assert.equal(attributes.get("allowscriptstoclose"), tab.isPopup ? "true" : undefined);
  }
  const startup = host.slice(host.indexOf("function startup() {"), host.indexOf("\nfunction shutdown() {"));
  assert.ok(startup.indexOf("createNavisNativeTab(browser)") < startup.indexOf("appendChild(browser)"));
  // Check the actual pinned Gecko consumers; no global preference or extra renderer is involved.
  const frameLoader = read("gecko/dom/base/nsFrameLoader.cpp");
  assert.match(frameLoader, /GetBoolAttr\(nsGkAtoms::allowscriptstoclose\)\)\s*\{\s*\(void\)browserParent->SendAllowScriptsToClose\(\)/);
  assert.match(frameLoader, /GetBoolAttr\(nsGkAtoms::allowscriptstoclose\)\)\s*\{\s*nsGlobalWindowOuter::Cast\(newWindow\)->AllowScriptsToClose\(\)/);
});
