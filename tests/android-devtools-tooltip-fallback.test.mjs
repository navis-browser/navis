import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const source = read("gecko/devtools/client/shared/widgets/tooltip/HTMLTooltip.js");
const menuSource = read("gecko/devtools/client/shared/components/menu/MenuButton.js");
const menuInitializer = menuSource.split("  initializeTooltip() {", 2)[1]
  .split("\n  async resetTooltip()", 1)[0];
const XUL = "http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul";
const HTML = "http://www.w3.org/1999/xhtml";

// Only DOM geometry and native popup event delivery are simulated. The entire
// production HTMLTooltip constructor, placement, show/hide, focus and listeners
// run unchanged, as does the MenuButton initializer that requests XUL wrappers.
class Element extends EventEmitter {
  constructor(doc, name, namespaceURI = HTML) {
    super();
    this.ownerDocument = doc;
    this.name = name;
    this.namespaceURI = namespaceURI;
    this.style = {};
    this.attributes = new Map();
    this.children = [];
    this.scrollTop = 0;
    this.rect = { left: 0, top: 0, width: 180, height: 160 };
    const classes = new Set();
    this.classList = {
      add: (...values) => values.forEach(value => classes.add(value)),
      remove: (...values) => values.forEach(value => classes.delete(value)),
      contains: value => classes.has(value),
      toggle(value, force = !classes.has(value)) {
        force ? classes.add(value) : classes.delete(value);
      },
    };
  }
  setAttribute(name, value) { this.attributes.set(name, value); }
  toggleAttribute(name, value) { if (value) this.setAttribute(name, ""); else this.attributes.delete(name); }
  appendChild(child) { child.remove(); this.children.push(child); child.parentNode = this; return child; }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this); this.parentNode = null; }
  querySelector(selector) { return this.children.find(child => selector.startsWith(".") ? child.classList.contains(selector.slice(1)) : child.name === selector) ?? null; }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  closest() { return null; }
  getBoxQuads() { return []; }
  getBoundingClientRect() { return { ...this.rect, right: this.rect.left + this.rect.width, bottom: this.rect.top + this.rect.height }; }
  addEventListener(name, listener) { this.on(name, listener); }
  removeEventListener(name, listener) { this.off(name, listener); }
  focus() { this.ownerDocument.activeElement = this; }
  openPopupAtScreen() {
    this.ownerDocument.nativeShows++;
    this.state = "open";
    queueMicrotask(() => this.emit("popupshown"));
  }
  hidePopup() { this.state = "closed"; queueMicrotask(() => this.emit("popuphidden")); }
  moveTo() { this.ownerDocument.nativeMoves++; }
}

function fixture({ OS = "Android", system = true, rootXul = false, code = source } = {}) {
  const doc = { nodePrincipal: { isSystemPrincipal: system }, nativeShows: 0, nativeMoves: 0, nativePanels: 0 };
  doc.documentElement = new Element(doc, rootXul ? "window" : "html", rootXul ? XUL : HTML);
  doc.documentElement.rect = { left: 0, top: 0, width: 360, height: 600 };
  doc.body = new Element(doc, "body");
  doc.documentElement.appendChild(doc.body);
  doc.createElementNS = (namespace, name) => new Element(doc, name, namespace);
  doc.createXULElement = name => { doc.nativePanels++; return new Element(doc, name, XUL); };
  const win = new EventEmitter();
  Object.assign(win, {
    setTimeout,
    addEventListener: (name, listener) => win.on(name, listener),
    removeEventListener: (name, listener) => win.off(name, listener),
    screen: { availLeft: 0, availTop: 0, availWidth: 1200, availHeight: 900 },
    screenX: 0, screenY: 0, outerWidth: 360, outerHeight: 600,
    mozInnerScreenX: 0, mozInnerScreenY: 0,
    getComputedStyle: () => ({ direction: "ltr", marginTop: "0", marginBottom: "0", getPropertyValue: () => "4" }),
  });
  win.top = win;
  win.parent = win;
  doc.defaultView = win;
  doc.hasFocus = () => true;
  const anchor = new Element(doc, "button");
  anchor.rect = { left: 270, top: 28, width: 32, height: 28 };
  doc.body.appendChild(anchor);
  anchor.focus();
  const scope = vm.createContext({
    module: { exports: {} },
    Services: { appinfo: { OS }, prefs: {
      getBoolPref: (key, fallback) => key === "devtools.popup.disable_autohide" ? doc.disableAutoHide ?? fallback : fallback,
    } },
    require(name) { assert.match(name, /event-emitter\.js$/); return EventEmitter; },
    ChromeUtils: { defineESModuleGetters(target) { target.focusableSelector = "button"; } },
    loader: { lazyRequireGetter() {} },
    DevToolsUtils: { getTopWindow: value => value.top },
    listenOnce: (element, name) => new Promise(resolve => element.once(name, resolve)),
  });
  vm.runInContext(code, scope);
  const Tooltip = scope.module.exports.HTMLTooltip;
  const button = { props: { toolboxDoc: doc, menuId: "tools-chevron-menu-button-panel" }, onHidden() {} };
  scope.tooltipButton = button;
  vm.runInContext(`({ initializeTooltip() {${menuInitializer} }).initializeTooltip.call(tooltipButton)`, scope);
  return { doc, win, anchor, Tooltip, tooltip: button.tooltip };
}

async function checkInDocument(f) {
  const { doc, win, tooltip, anchor } = f;
  assert.equal(tooltip.useXulWrapper, false);
  assert.equal(doc.nativePanels, 0);
  assert.equal(tooltip.container.parentNode, doc.documentElement.namespaceURI === XUL ? doc.documentElement : doc.body);
  let shown = 0, hidden = 0;
  tooltip.on("shown", () => shown++);
  tooltip.on("hidden", () => hidden++);
  await tooltip.show(anchor, { position: "bottom", y: -5 });
  assert.equal(tooltip.isVisible(), true);
  assert.equal(shown, 1);
  assert.match(tooltip.container.style.left, /^\d+(\.\d+)?px$/);
  assert.match(tooltip.container.style.top, /^\d+(\.\d+)?px$/);
  assert.equal(win.listenerCount("mouseup"), 1);
  const choice = new Element(doc, "button");
  tooltip.panel.appendChild(choice);
  tooltip.focus();
  assert.equal(doc.activeElement, choice);
  tooltip._onMouseup({ target: choice });
  assert.equal(tooltip.isVisible(), true, "selecting a menu item is not treated as an outside hit");
  await tooltip.hide();
  assert.equal(hidden, 1);
  assert.equal(tooltip.isVisible(), false);
  assert.equal(doc.activeElement, anchor);
  assert.equal(win.listenerCount("mouseup"), 0);
  await tooltip.show(anchor, { position: "bottom" });
  const outsideHidden = new Promise(resolve => tooltip.once("hidden", resolve));
  tooltip._onMouseup({ target: doc.body });
  await outsideHidden;
  assert.equal(tooltip.isVisible(), false);
  assert.equal(doc.nativeShows, 0);
  tooltip.destroy();
  assert.equal(tooltip.container.parentNode, null);
  assert.equal(win.listenerCount("click"), 0);
}

test("Android privileged toolbox MenuButton uses the shared in-document show/hide/focus lifecycle", async () => {
  await checkInDocument(fixture());
});

test("Android XUL-root host also uses the existing non-native container branch", async () => {
  await checkInDocument(fixture({ rootXul: true }));
});

test("desktop privileged menus retain native wrappers on Linux, Windows and macOS", async () => {
  for (const OS of ["Linux", "WINNT", "Darwin"]) {
    const { doc, tooltip, anchor } = fixture({ OS });
    assert.equal(tooltip.useXulWrapper, true);
    assert.equal(doc.nativePanels, 1);
    assert.equal(tooltip.container.parentNode, tooltip.xulPanelWrapper);
    await tooltip.show(anchor, { position: "bottom" });
    assert.equal(doc.nativeShows, 1);
    assert.equal(tooltip.isVisible(), true);
    await tooltip.hide();
    assert.equal(tooltip.isVisible(), false);
    tooltip.destroy();
  }
});

test("ordinary content keeps the existing non-privileged fallback on either platform", async () => {
  for (const OS of ["Android", "Linux"]) await checkInDocument(fixture({ OS, system: false }));
});

test("mutation: principal-only availability reproduces the unsupported native popup choice", async () => {
  const needle = 'Services.appinfo.OS !== "Android" &&';
  assert.equal(source.split(needle).length, 2);
  const f = fixture({ code: source.replace(needle, "") });
  assert.equal(f.tooltip.useXulWrapper, true);
  await assert.rejects(checkInDocument(f), { name: "AssertionError" });
  f.tooltip.destroy();
});

// A short RDM chrome iframe grants a temporary, larger viewport. Geometry is
// still measured by the real HTMLTooltip implementation; this wrapper records
// the order and document identity at that boundary, not a second show policy.
function responsiveFixture(options = {}) {
  const f = fixture(options);
  const { doc, win, tooltip } = f;
  const initialHeight = 48;
  doc.documentElement.rect.height = initialHeight;
  const lease = { acquires: 0, releases: 0, live: 0, events: [] };
  win.frameElement = { navisResponsivePopupHost: {
    acquire(requestedDoc) {
      assert.equal(requestedDoc, doc, "the grant belongs to the real iframe document");
      assert.equal(lease.live, 0, "one tooltip may own only one viewport grant");
      lease.acquires++;
      lease.live++;
      lease.events.push("acquire");
      doc.documentElement.rect.height = 600;
      let released = false;
      return () => {
        assert.equal(released, false, "a viewport release may not be invoked twice");
        released = true;
        lease.releases++;
        lease.live--;
        lease.events.push("release");
        doc.documentElement.rect.height = initialHeight;
      };
    },
  } };
  const update = tooltip._updateContainerBounds;
  tooltip._updateContainerBounds = function (...args) {
    lease.events.push("measure");
    assert.equal(lease.live, 1, "acquire must precede the real container measurement");
    assert.equal(doc.documentElement.rect.height, 600);
    return update.apply(this, args);
  };
  return { ...f, lease, initialHeight };
}

async function checkViewportLifecycle(code = source) {
  const { tooltip, anchor, doc, lease, initialHeight } = responsiveFixture({ code });
  try {
    // Also reuse while show is waiting for its real listener-attachment tick.
    await Promise.all([tooltip.show(anchor), tooltip.show(anchor)]);
    await tooltip.show(anchor, { position: "bottom", x: -8 });
    assert.equal(lease.acquires, 1);
    assert.equal(lease.releases, 0);
    assert.equal(lease.live, 1);
    assert.deepEqual(lease.events, ["acquire", "measure", "measure", "measure"]);
    assert.equal(tooltip.isVisible(), true);
    await tooltip.hide();
    assert.equal(lease.live, 0);
    assert.equal(lease.releases, 1);
    assert.equal(doc.documentElement.rect.height, initialHeight);
    await tooltip.hide();
    assert.equal(lease.releases, 1, "hiding an already-hidden tooltip is idempotent");
    await tooltip.show(anchor);
    assert.equal(lease.acquires, 2);
    tooltip.destroy();
    assert.equal(lease.live, 0);
    assert.equal(lease.releases, 2);
    assert.equal(tooltip.container.parentNode, null);
  } finally {
    tooltip.destroy();
  }
}

test("Android system RDM viewport is acquired before geometry and reused until hide/destroy", async () => {
  await checkViewportLifecycle();
});

test("RDM show errors release the granted viewport and permit a clean retry", async () => {
  const { tooltip, anchor, doc, lease, initialHeight } = responsiveFixture();
  const update = tooltip._updateContainerBounds;
  tooltip._updateContainerBounds = function () {
    assert.equal(lease.live, 1);
    throw new Error("fixture geometry failure");
  };
  await assert.rejects(tooltip.show(anchor), /fixture geometry failure/);
  assert.equal(tooltip.isVisible(), false);
  assert.deepEqual([lease.acquires, lease.releases, lease.live], [1, 1, 0]);
  assert.equal(doc.documentElement.rect.height, initialHeight);
  tooltip._updateContainerBounds = update;
  await tooltip.show(anchor);
  assert.deepEqual([lease.acquires, lease.releases, lease.live], [2, 1, 1]);
  await tooltip.hide();
  tooltip.destroy();
  assert.equal(lease.releases, 2);
});

test("no-autohide preserves a visible grant but destroy always releases it", async () => {
  const { tooltip, anchor, doc, lease } = responsiveFixture();
  await tooltip.show(anchor);
  doc.disableAutoHide = true;
  await tooltip.hide();
  assert.equal(tooltip.isVisible(), true);
  assert.equal(lease.live, 1);
  tooltip.destroy();
  assert.equal(lease.live, 0);
  assert.equal(lease.releases, 1);
  tooltip.destroy();
  assert.equal(lease.releases, 1);
});

test("desktop and content documents never request an Android RDM viewport grant", async () => {
  for (const options of [
    { OS: "Linux" }, { OS: "WINNT" }, { OS: "Darwin" },
    { OS: "Android", system: false }, { OS: "Linux", system: false },
  ]) {
    const { tooltip, anchor, win } = fixture(options);
    let calls = 0;
    win.frameElement = { navisResponsivePopupHost: {
      acquire() { calls++; throw new Error("wrong platform/principal requested a grant"); },
    } };
    await tooltip.show(anchor);
    await tooltip.hide();
    tooltip.destroy();
    assert.equal(calls, 0);
  }
});

test("mutations: omitting either viewport acquisition or release fails real lifecycle assertions", async () => {
  for (const [needle, replacement] of [
    ["if (owner) this._navisPopupViewportRelease = owner.acquire(this.doc);", "// Mutant: measure the short iframe without its viewport grant."],
    ["release?.();", "// Mutant: lose the release callback without restoring the viewport."],
  ]) {
    assert.equal(source.split(needle).length, 2, "mutation must target exactly one production statement");
    await assert.rejects(checkViewportLifecycle(source.replace(needle, replacement)), { name: "AssertionError" });
  }
});
