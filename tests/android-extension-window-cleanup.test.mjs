import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../gecko/mobile/shared/modules/navis/NavisAndroidWebExtensionHost.sys.mjs", import.meta.url), "utf8");
function method(marker) {
  const start = source.indexOf(marker);
  assert.ok(start > 0);
  let end = source.indexOf("{", start) + 1, depth = 1;
  while (depth) { depth += (source[end] === "{") - (source[end] === "}"); end++; }
  return source.slice(start, end);
}
const dispatch = source.split('case "NavisAndroid:Extensions:ActiveTabChanged":')[1]
  .split('case "NavisAndroid:Extensions:TabClosed":')[0];
const methods = [method("#onTabClosed(tabId, nativeTab = null) {"),
  method("#dismissPopup() {"), method("#revokeAllGrants() {")].join("\n");
function fixture(body = dispatch) {
  return Function("installError", `return class {
    #popup; #activeNativeTab; #activeGrants = new Map();
    revoked = [];
    constructor(popupSource = 2) {
      const a = { id: 1 }, b = { id: 2 };
      this.#activeGrants.set("a", a); this.#activeGrants.set("b", b);
      this.#popup = { sourceTabId: popupSource, extensionId: popupSource === 1 ? "a" : "b" };
      this.#activeNativeTab = b;
    }
    async #ensureInitialized() {}
    #revokeGrant(id) { if (this.#activeGrants.delete(id)) this.revoked.push(id); }
    get state() { return { popup: this.#popup?.sourceTabId, active: this.#activeNativeTab?.id, grants: [...this.#activeGrants.keys()] }; }
    async dispatch(data) { ${body} }
    ${methods}
  }`)((message, code) => Object.assign(new Error(message), { code }));
}

test("actual selected-tab cleanup preserves another window's popup and grants", async () => {
  const Host = fixture(), host = new Host();
  await host.dispatch({ tabId: 1 });
  assert.deepEqual(host.state, { popup: 2, active: 2, grants: ["b"] });
  assert.deepEqual(host.revoked, ["a"]);
  const own = new Host(1);
  await own.dispatch({ tabId: 1 });
  assert.deepEqual(own.state, { popup: undefined, active: 2, grants: ["b"] });
});

test("legacy global cleanup remains explicit; malformed identity never broadens its scope", async () => {
  const Host = fixture();
  for (const tabId of [null, 0, -1, 1.5, "1", Number.MAX_SAFE_INTEGER + 1]) {
    const host = new Host();
    await assert.rejects(host.dispatch({ tabId }), error => error.code === "invalid-tab");
    assert.deepEqual(host.state.grants, ["a", "b"]);
  }
  const host = new Host();
  await host.dispatch(null);
  assert.equal(host.state.popup, undefined);
  assert.deepEqual(host.state.grants, []);
});

test("old global invalidation mutation is caught by the window boundary", async () => {
  const old = dispatch.replace("this.#onTabClosed(data.tabId);", "this.#dismissPopup(); this.#revokeAllGrants();");
  assert.notEqual(old, dispatch);
  const host = new (fixture(old))();
  await host.dispatch({ tabId: 1 });
  assert.notEqual(host.state.popup, 2);
  assert.deepEqual(host.state.grants, []);
});

test("actual action snapshot tags the exact tab used for private context data", () => {
  const snapshot = method("function addonActionSnapshot(addon) {");
  const a = { id: 1 }, b = { id: 2 };
  const extension = { manifest: {}, tabManager: { activeTab: a } };
  const accessible = new Set([a, b]);
  const fn = Function("lazy", "addonHasAction", "actionForExtension", "activeAccessibleTab", "boundedString", "androidArgb", "iconSnapshot",
    `${snapshot}; return addonActionSnapshot;`)(
    { ExtensionParent: { GlobalManager: { getExtension: () => extension } } },
    addon => addon.hasAction,
    () => ({ kind: "BROWSER", action: { getContextData: tab => ({ title: `tab-${tab.id}`, badgeText: `${tab.id}` }) } }),
    () => accessible.has(extension.tabManager.activeTab) ? extension.tabManager.activeTab : null,
    (value, limit) => String(value || "").slice(0, limit), value => value ?? null, () => ({}));
  const addon = { id: "extension", name: "Extension", isActive: true, hasAction: true };
  const first = fn(addon);
  extension.tabManager.activeTab = b;
  const second = fn(addon);
  assert.equal(first.actionSourceTabId, 1);
  assert.equal(first.actionTitle, "tab-1");
  assert.equal(second.actionSourceTabId, 2);
  assert.equal(second.actionBadgeText, "2");
  accessible.delete(b);
  assert.equal(fn(addon).actionSourceTabId, 0);
  assert.equal(fn(addon).actionBadgeText, "");
  assert.equal(fn({ ...addon, hasAction: false }).actionSourceTabId, 0);
});
