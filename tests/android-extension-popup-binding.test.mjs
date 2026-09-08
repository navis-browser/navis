import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";

const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const host = read("../runtime/gecko/mobile/shared/modules/navis/NavisAndroidWebExtensionHost.sys.mjs");
const start = host.indexOf("  loadPopupTarget(browser, extensionId, targetToken, popupUri) {");
const end = host.indexOf("\n}\n\nfunction directTabById", start);
assert.ok(start > 0 && end > start);
const method = host.slice(start, end);
const TOKEN = "a".repeat(32);
const URI = "moz-extension://fixture/popup.html";

function fixture(implementation = method) {
  const order = [];
  const attributes = new Map();
  const source = { id: 1 };
  const owner = { id: 4503599627370497, setPopup(value) { order.push(`popup:${value}`); } };
  const window = { name: `navis-android-session-${owner.id}:popup-${TOKEN}`, tab: owner,
    document: { documentElement: { getAttribute: () => "navigator:navis-android" } } };
  const browser = { documentGlobal: window, privateMode: false,
    setAttribute(name, value) { attributes.set(name, value); order.push("viewtype"); },
    hasAttribute: name => attributes.has(name), getAttribute: name => attributes.get(name),
    loadURI(uri) { order.push("load"); assert.equal(uri, URI); } };
  const extension = { tabManager: { canAccessTab: () => true, activeTab: source } };
  const lazy = { ExtensionParent: { GlobalManager: { getExtension: () => extension },
    apiManager: { emit(type, target) { assert.equal(type, "extension-browser-inserted");
      assert.equal(target, browser); order.push("insert"); } } },
    PrivateBrowsingUtils: { isBrowserPrivate: target => target.privateMode },
    NavisAndroidExtensionTabTopology: { selectedNativeTab: id => id === source.id } };
  const context = vm.createContext({ lazy, DIRECT_WINDOW_TYPE: "navigator:navis-android",
    installError: (message, code) => Object.assign(new Error(message), { code }),
    Services: { io: { newURI: uri => uri }, scriptSecurityManager: { getSystemPrincipal: () => ({}) } },
  });
  vm.runInContext(`globalThis.Binding = class { #popup;
    constructor(popup) { this.#popup = popup; }
    #dismissPopup() { this.#popup = null; }
    ${implementation}
  };`, context);
  const popup = { extensionId: "fixture", targetToken: TOKEN, popupUri: URI,
    nativeTab: source, privateMode: false, targetBrowser: null };
  const binding = new context.Binding(popup);
  return { order, attributes, source, owner, window, browser, extension, lazy, popup,
    run: (target = browser, token = TOKEN) => binding.loadPopupTarget(target, "fixture", token, URI) };
}

test("valid popup has one ordered view-type, context insertion and load, with stable rebind", () => {
  const state = fixture();
  assert.equal(state.browser.ownerGlobal, undefined, "do not invent the removed Node API in a fixture");
  assert.equal(state.run(), true);
  assert.deepEqual(state.order, ["popup:true", "viewtype", "insert", "load"]);
  assert.equal(state.run(), true);
  assert.equal(state.order.length, 4);
});

test("binding uses the current pinned chrome Node global and rejects a removed-API mutation", () => {
  const nodeIDL = read("../runtime/gecko/dom/webidl/Node.webidl");
  assert.match(nodeIDL, /\[ChromeOnly, Pure, BinaryName="documentGlobalForBindings"\]\s*readonly attribute WindowProxy\? documentGlobal;/);
  assert.doesNotMatch(nodeIDL, /attribute[^;]*\bownerGlobal\b/);
  assert.ok(method.includes("const ownerWindow = browser.documentGlobal;"));
  const mutated = fixture(method.replace("browser.documentGlobal", "browser.ownerGlobal"));
  assert.throws(mutated.run, error => error.code === "popup-owner-invalid");
  assert.equal(mutated.order.length, 0, "invalid globals must not get popup privileges or a load");
});

test("Navis extension-menu wakeup recognizes the same current Node host without Firefox gBrowser", async () => {
  const menu = read("../runtime/gecko/browser/components/extensions/parent/ext-menus.js");
  const block = menu.slice(menu.indexOf("    onClicked({ context, fire }) {"));
  const listener = block.slice(block.indexOf("      let listener = async"),
    block.indexOf('\n\n      extension.on("webext-menu-menuitem-click"'));
  assert.ok(listener.includes("linkedBrowser?.documentGlobal?.navisAndroidContextMenus"));
  assert.ok(!listener.includes("ownerGlobal"));
  const window = { navisAndroidContextMenus: {} }; // Navis does not own Firefox gBrowser.
  const nativeTab = { linkedBrowser: { documentGlobal: window, ownerDocument: { defaultView: window } } };
  let fired = false;
  const sandbox = vm.createContext({
    extension: { tabManager: { convert: tab => tab } },
    fire: { wakeup: async () => {}, sync: () => { fired = true; } },
    context: { withPendingBrowser(browser, call) { assert.equal(browser, nativeTab.linkedBrowser); call(); } },
    Cu: { reportError: () => { throw Error("live Navis menu source was misclassified"); } },
  });
  vm.runInContext(listener + "\nglobalThis.click = listener;", sandbox);
  await sandbox.click("menu-click", {}, nativeTab);
  assert.equal(fired, true);
});

const failures = [
  ["popup-invalid-target", state => () => state.run(null)],
  ["popup-transaction-stale", state => () => state.run(state.browser, "b".repeat(32))],
  ["popup-owner-invalid", state => { state.window.tab = null; }],
  ["popup-owner-reused", state => { state.window.tab = state.source; }],
  ["popup-owner-name", state => { state.window.name = "unrelated-private-name"; }],
  ["popup-source-inaccessible", state => { state.extension.tabManager.canAccessTab = () => false; }],
  ["popup-source-inactive", state => { state.lazy.NavisAndroidExtensionTabTopology.selectedNativeTab = () => false; }],
  ["popup-private-mismatch", state => { state.browser.privateMode = true; }],
  ["popup-target-reused", state => { state.popup.targetBrowser = {}; }],
  ["popup-viewtype", state => { state.attributes.set("webextension-view-type", "tab"); }],
  ["popup-viewtype-failed", state => { state.owner.setPopup = () => { throw Error("secret-token"); }; }],
  ["popup-insertion-failed", state => { state.lazy.ExtensionParent.apiManager.emit = () => { throw Error(URI); }; }],
  ["popup-load-failed", state => { state.browser.loadURI = () => { throw Error("private browser URI"); }; }],
];

for (const [code, mutate] of failures) {
  test(`binding rejection is distinguishable: ${code}`, () => {
    const state = fixture();
    const invoke = mutate(state) || state.run;
    assert.throws(invoke, error => {
      assert.equal(error.code, code);
      assert.doesNotMatch(error.message, /secret-token|moz-extension|unrelated-private-name/);
      return true;
    });
    assert.ok(!state.order.includes("load"));
  });
}

test("all native binding codes survive only the explicit Java and Kotlin allowlists", () => {
  const java = read("../runtime/gecko/mobile/android/geckoview/src/main/java/org/mozilla/gecko/navis/NavisAndroidPopupBindingFailure.java");
  const kotlin = read("../platform/android/src/main/java/org/navis/browser/extensions/ExtensionPopupFailureStage.kt");
  const codes = new Set(failures.map(([code]) => code));
  assert.deepEqual(new Set([...java.matchAll(/case "([a-z-]+)":/g)].map(match => match[1])), codes);
  for (const code of codes) assert.ok(kotlin.includes(`("${code}")`));
  assert.doesNotMatch(java, /getMessage\(|toString\(|getStackTrace\(|getString\("(?:message|url|token)"/);
  assert.match(read("../runtime/gecko/mobile/android/geckoview/src/main/java/org/mozilla/gecko/navis/NavisAndroidPopupSurface.java"),
    /notifyFailed\(NavisAndroidPopupBindingFailure\.code\(error\)\)/);
});
