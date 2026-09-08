import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { EventEmitter } from "node:events";
import { createNavisAndroidResponsiveTarget } from "../gecko/mobile/shared/modules/navis/NavisAndroidResponsive.sys.mjs";

const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
function method(source, signature) {
  const found = source.indexOf(`\n  ${signature}`);
  assert.ok(found >= 0, signature);
  const start = found + 3;
  let end = source.indexOf(") {", start) + 3;
  let depth = 1;
  while (depth) { depth += (source[end] === "{") - (source[end] === "}"); end++; }
  return source.slice(start, end);
}
const upstreamManager = read("gecko/devtools/client/responsive/manager.js");
const upstreamUI = read("gecko/devtools/client/responsive/ui.js");
const closeMethod = Function(`return ({${method(upstreamManager, "async closeIfNeeded(")}}).closeIfNeeded`)();
const resizeMethod = Function(`return ({${method(upstreamUI, "updateViewportSize(")}}).updateViewportSize`)();
const rotateMethod = Function(`return ({${method(upstreamUI, "async onRotateViewport(")}}).onRotateViewport`)();
const destroyMethod = Function(`return ({${method(upstreamUI, "async destroy(")}}).destroy`)();

class Node extends EventTarget {
  constructor(name, window) {
    super(); this.name = name; this.documentGlobal = window;
    this.children = []; this.values = new Map();
    this.classList = { add: (...items) => items.forEach(item => this.values.set(item, true)),
      remove: (...items) => items.forEach(item => this.values.delete(item)) };
    this.style = { setProperty: (name, value) => this.values.set(name, value),
      removeProperty: name => this.values.delete(name) };
  }
  append(...nodes) { for (const node of nodes) { this.children.push(node); node.parent = this; } }
  prepend(node) { this.children.unshift(node); node.parent = this; }
  remove() { this.parent.children = this.parent.children.filter(node => node !== this); }
  setAttribute(name, value) { this.values.set(name, value); }
}

function fixture({ initializeFailure = false, createTarget = createNavisAndroidResponsiveTarget } = {}) {
  const calls = [];
  const window = new EventTarget();
  window.closed = false;
  const media = new EventTarget(); media.matches = true;
  window.matchMedia = () => media;
  window.document = { documentURI: "chrome://navis-android/content/host.xhtml",
    createXULElement: name => new Node(name, window), documentElement: new Node("root", window) };
  const browser = new Node("browser", window);
  browser.fullZoom = 1.2;
  browser.browsingContext = {};
  browser.navisNativePresentation = false;
  browser.focus = () => calls.push("focus");
  browser.leaveResponsiveMode = () => { browser.browsingContext.inRDMPane = false; calls.push("leave"); };
  const tab = { browser, linkedBrowser: browser, isPopup: false, getActive: () => true };
  const manager = new EventEmitter();
  manager.activeTabs = new Map();
  manager.isActiveForTab = tab => manager.activeTabs.has(tab);
  manager.isActiveForWindow = () => manager.activeTabs.size > 0;
  manager.removeMenuCheckListenerFor = () => calls.push("remove-menu-listener");
  manager.setMenuCheckFor = async () => {};
  manager.recordTelemetryClose = () => {};
  manager.getResponsiveUIForTab = tab => manager.activeTabs.get(tab);
  manager.closeIfNeeded = closeMethod;
  let releaseActors;
  let ui;
  manager.openIfNeeded = async (owner, actualTab) => {
    assert.equal(owner, window); assert.equal(actualTab, tab);
    const frame = new Node("iframe", window);
    frame.contentWindow = new EventTarget(); frame.contentWindow.destroy = () => calls.push("frame-destroy");
    const screen = new Node("screen", window);
    const container = window.gBrowser.getBrowserContainer(browser);
    const stack = container.children[0];
    container.prepend(frame); stack.append(screen);
    ui = {
      tab, browserWindow: window, browserContainerEl: container, browserStackEl: stack,
      rdmFrame: frame, screenBox: screen, initialized: Promise.resolve(),
      resizeToolbarObserver: { unobserve() {}, disconnect() { calls.push("disconnect"); } },
      hideBrowserUI() {}, showBrowserUI() {}, reloadOnChange: () => false,
      updateScreenOrientation: async (...args) => calls.push(["orientation", ...args]),
      updateMaxTouchPointsEnabled: async value => calls.push(["touch-points", value]),
      updateNetworkThrottling: async () => calls.push("network-reset"),
      updateDPPX: async value => calls.push(["dpr", value]),
      updateUserAgent: async () => { calls.push("ua-reset"); return false; },
      updateTouchSimulation: async value => calls.push(["touch", value]),
      commands: { targetCommand: { TYPES: { FRAME: "frame" }, unwatchTargets() {}, destroy() {} },
        destroy: () => new Promise(resolve => { calls.push("actors-destroy"); releaseActors = resolve; }) },
      resourceCommand: { TYPES: { NETWORK_EVENT: "network" }, unwatchResources() {} },
      destroy: destroyMethod,
    };
    manager.activeTabs.set(tab, ui);
    browser.browsingContext.inRDMPane = true;
    if (initializeFailure) {
      ui.commands.destroy = async () => calls.push("actors-destroy");
      ui.initialized = new Promise(() => {}); // actual upstream failed-initialize contract
      throw new Error("actor initialization rejected");
    }
    manager.emit("on", { tab });
    return ui;
  };
  globalThis.Services = { obs: { removeObserver() {} }, prefs: {
    getStringPref: () => "auto", addObserver() {}, removeObserver() {} } };
  globalThis.Cu = { reportError: error => calls.push(error) };
  globalThis.InspectorUtils = window.InspectorUtils = {
    setVerticalClipping: (_, value) => calls.push(["clip", value]),
    setDynamicToolbarMaxHeight: (_, value) => calls.push(["toolbar", value]),
  };
  globalThis.ChromeUtils = { importESModule: uri => uri.endsWith("NavisAndroidResponsiveUI.sys.mjs")
    ? { bindNavisAndroidResponsiveUI: async () => () => calls.push("layout-dispose") }
    : ({ require: name => {
    if (name === "devtools/client/framework/devtools-browser") return { gDevToolsBrowser: {} };
    assert.equal(name, "devtools/client/responsive/manager"); return manager;
  } }) };
  const target = createTarget(window, browser, tab);
  return { target, window, browser, tab, manager, calls, get ui() { return ui; },
    releaseActors: () => releaseActors(), get stack() { return target.container.children.find(node => node.name === "stack"); } };
}

test("container binds the original browser before insertion, with actual tab ownership and scoped events", async () => {
  const f = fixture();
  assert.equal(f.target.container.children[0].children[0], f.browser);
  assert.equal(f.window.gBrowser.getBrowserForTab(f.tab), f.browser);
  assert.equal(f.window.gBrowser.getTabForBrowser(f.browser), f.tab);
  assert.deepEqual(f.window.gBrowser.browsers, [f.browser]);
  assert.throws(() => f.window.gBrowser.getBrowserForTab({}), /another Session/);
  assert.equal(f.tab.tagName, "tab");
  assert.equal(f.tab.documentGlobal, f.window);
  let remoteness = 0; const controller = new AbortController();
  f.tab.addEventListener("TabRemotenessChange", event => { assert.equal(event.target, f.tab); remoteness++; }, { signal: controller.signal });
  f.browser.dispatchEvent(new Event("XULFrameLoaderCreated"));
  controller.abort(); f.browser.dispatchEvent(new Event("XULFrameLoaderCreated"));
  assert.equal(remoteness, 1);
  await assert.rejects(f.target.toggle(), /live web/);
  f.target.markReady(); f.browser.navisNativePresentation = true;
  await assert.rejects(f.target.toggle(), /live web/);
  f.target.destroy();
});

test("real upstream resize/rotate methods change the target container and actor; restore waits for actors", async () => {
  const f = fixture(); f.target.markReady();
  assert.deepEqual(await f.target.toggle(), { responsive: true, zoom: 1.2 });
  const ui = f.ui; ui.emit = () => {};
  resizeMethod.call(ui, 390, 844);
  assert.equal(f.stack.values.get("--rdm-width"), "390px");
  assert.equal(f.stack.values.get("--rdm-height"), "844px");
  assert.deepEqual(f.target.zoom("zoom-in"), { zoom: 1.3 });
  resizeMethod.call(ui, 844, 390);
  assert.equal(f.stack.values.get("--rdm-zoom"), 1.3);
  await rotateMethod.call(ui, { data: { orientationType: "landscape-primary", angle: 90 } });
  assert.deepEqual(f.calls.at(-1), ["orientation", "landscape-primary", 90]);
  let finished = false;
  const closing = f.target.restore().then(() => { finished = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(finished, false, "no success before real actor release");
  assert.equal(f.browser.browsingContext.inRDMPane, false);
  assert.equal(f.stack.values.has("--rdm-width"), false);
  f.releaseActors(); await closing;
  assert.equal(f.manager.isActiveForTab(f.tab), false);
  assert.equal(f.browser.fullZoom, 1.2, "restore pre-RDM zoom");
  assert.ok(f.calls.some(item => Array.isArray(item) && item[0] === "dpr" && item[1] === null));
  assert.ok(f.calls.includes("ua-reset"));
  f.target.destroy();
});

async function assertConcurrentRestore(createTarget = createNavisAndroidResponsiveTarget) {
  const f = fixture({ createTarget }); f.target.markReady(); await f.target.toggle();
  const toolbarClose = f.manager.closeIfNeeded(f.window, f.tab);
  await new Promise(resolve => setImmediate(resolve));
  let finished = false;
  const closing = f.target.restore().then(() => { finished = true; });
  await new Promise(resolve => setImmediate(resolve));
  try { assert.equal(finished, false, "early manager return is not cleanup completion"); }
  finally { f.releaseActors(); await Promise.all([toolbarClose, closing]); f.target.destroy(); }
}

test("toolbar X concurrent with native Back waits for the real off event, including mutation", async () => {
  await assertConcurrentRestore();
  const source = read("gecko/mobile/shared/modules/navis/NavisAndroidResponsive.sys.mjs");
  const mutant = source.replace("if (responsiveManager.isActiveForTab(tab)) await completion;", "/* broken early return */");
  assert.notEqual(mutant, source);
  const module = await import(`data:text/javascript;base64,${Buffer.from(mutant).toString("base64")}`);
  await assert.rejects(assertConcurrentRestore(module.createNavisAndroidResponsiveTarget), /early manager return/);
});

test("failed original initialization releases acquired actors/UI without waiting on unresolved initialized", async () => {
  const f = fixture({ initializeFailure: true }); f.target.markReady();
  await assert.rejects(f.target.toggle(), /actor initialization rejected/);
  assert.equal(f.browser.browsingContext.inRDMPane, false);
  assert.equal(f.manager.isActiveForTab(f.tab), false);
  assert.equal(f.target.container.children.length, 1);
  assert.equal(f.stack.children.length, 1);
  assert.ok(f.calls.indexOf("actors-destroy") < f.calls.indexOf("frame-destroy"));
  await f.target.restore(); f.target.destroy();
});
