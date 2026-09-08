import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";

const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const host = read("../runtime/gecko/mobile/shared/modules/navis/NavisAndroidWebExtensionHost.sys.mjs");
const desktop = read("../runtime/embedder/modules/DesktopExtensionManager.sys.mjs");
const gecko = read("../runtime/gecko/toolkit/components/extensions/Extension.sys.mjs");
const manifest = JSON.parse(execFileSync("unzip", ["-p",
  new URL("../../platform/gecko-chrome/builtin/ublock-origin/uBlock0@raymondhill.net.xpi", import.meta.url).pathname,
  "manifest.json"], { encoding: "utf8", maxBuffer: 1024 * 1024 }));
const PRIVATE_PERMISSION = "internal:privateBrowsingAllowed";
const allowedExpression = host.match(/privateBrowsingAllowed:\s*([\s\S]+?),\n\s*privateBrowsingAvailable:/)?.[1];
const availableExpression = host.match(/privateBrowsingAvailable:\s*([\s\S]+?),\n\s*pinnedToToolbar:/)?.[1];
assert.ok(allowedExpression && availableExpression, "test actual inventory fields");
const privilegeMethod = gecko.slice(gecko.indexOf("  static getIsPrivileged("), gecko.indexOf("  get builtinMessages()"));
const permissionStart = gecko.indexOf("  _setupStartupPermissions() {");
const permissionEnd = gecko.indexOf("    // Allow other extensions to access static themes", permissionStart);
assert.ok(permissionStart > 0 && permissionEnd > permissionStart);
const startupPolicy = gecko.slice(permissionStart, permissionEnd) + "\n  }";

function startup({ builtIn = true, notAllowed = false, granted = [] } = {}) {
  const persisted = new Set(granted);
  const context = vm.createContext({
    PRIVATE_ALLOWED_PERMISSION: PRIVATE_PERMISSION,
    lazy: {
      AddonManager: { SIGNEDSTATE_PRIVILEGED: 3, SIGNEDSTATE_SYSTEM: 4 },
      AddonSettings: { EXPERIMENTS_ENABLED: false },
      ExtensionPermissions: {
        add(id, value) { for (const permission of value.permissions) persisted.add(permission); },
        remove(id, value) { for (const permission of value.permissions) persisted.delete(permission); },
      },
    },
  });
  // Real pinned Gecko classification and startup policy; persistence is the
  // only seam replaced. The uBO fixture itself is the shipped immutable XPI.
  vm.runInContext(`globalThis.Policy = class { ${privilegeMethod} ${startupPolicy} };`, context);
  const extension = new context.Policy();
  const fixture = notAllowed ? { ...manifest, incognito: "not_allowed" } : manifest;
  Object.assign(extension, { id: fixture.browser_specific_settings.gecko.id,
    permissions: new Set(granted), manifest: fixture, temporarilyInstalled: false,
    isPrivileged: context.Policy.getIsPrivileged({ signedState: 2, builtIn, temporarilyInstalled: false }) });
  extension._setupStartupPermissions();
  return { persisted, fixture };
}

function project({ builtIn = true, foreign = false, temporary = false,
  notAllowed = false, granted = [] } = {}) {
  const context = { PRIVATE_PERMISSION, builtIn, foreign, temporary,
    addon: { incognito: notAllowed ? "not_allowed" : manifest.incognito },
    granted: { permissions: [...granted] } };
  return {
    allowed: vm.runInNewContext(allowedExpression, context),
    available: vm.runInNewContext(availableExpression, context),
  };
}

test("actual shipped uBO receives Gecko built-in access and Android shows checked but fixed", () => {
  assert.equal(manifest.browser_specific_settings.gecko.id, "uBlock0@raymondhill.net");
  assert.notEqual(manifest.incognito, "not_allowed");
  const state = startup();
  assert.ok(state.persisted.has(PRIVATE_PERMISSION));
  assert.deepEqual(project({ granted: state.persisted }), { allowed: true, available: false });
  const desktopExpression = desktop.match(/const privateBrowsingAllowed = ([\s\S]+?);/)?.[1];
  assert.ok(desktopExpression);
  assert.equal(vm.runInNewContext(desktopExpression, {
    PRIVATE_PERMISSION, granted: { permissions: [...state.persisted] },
  }), true, "same real permission value as desktop");
});

test("not_allowed overrides built-in privilege and stale stored grants", () => {
  const state = startup({ notAllowed: true, granted: [PRIVATE_PERMISSION] });
  assert.equal(state.persisted.has(PRIVATE_PERMISSION), false);
  assert.deepEqual(project({ notAllowed: true, granted: [PRIVATE_PERMISSION] }),
    { allowed: false, available: false });
});

test("user-installed signed extensions remain denied until explicitly granted", () => {
  const state = startup({ builtIn: false });
  assert.equal(state.persisted.has(PRIVATE_PERMISSION), false);
  assert.deepEqual(project({ builtIn: false, granted: state.persisted }),
    { allowed: false, available: true });
  const consented = startup({ builtIn: false, granted: [PRIVATE_PERMISSION] });
  assert.deepEqual(project({ builtIn: false, granted: consented.persisted }),
    { allowed: true, available: true });
});

test("projection never invents grants merely because the extension is built-in", () => {
  assert.deepEqual(project(), { allowed: false, available: false });
  for (const denied of [{ foreign: true }, { temporary: true }]) {
    assert.deepEqual(project({ builtIn: false, granted: [PRIVATE_PERMISSION], ...denied }),
      { allowed: false, available: false });
  }
  assert.doesNotMatch(allowedExpression, /ExtensionPermissions\.(?:add|remove)|builtIn\s*\|\|/);
});

test("UI explains fixed built-in policy without presenting the user-install default as its policy", () => {
  const ui = read("../platform/android/src/main/java/org/navis/browser/ui/ExtensionManagerSurface.kt");
  assert.match(ui, /extension\.extensionClass == ExtensionClass\.APPLICATION_BUILT_IN\) \{\s*R\.string\.run_in_private_tabs_builtin_summary/);
  assert.match(ui, /checked = extension\.allowedInPrivateBrowsing/);
  assert.match(ui, /enabled = !busy && extension\.privateBrowsingAvailable/);
  for (const locale of ["values", "values-zh-rCN"]) {
    assert.match(read(`../platform/android/src/main/res/${locale}/extension_private_strings.xml`),
      /name="run_in_private_tabs_builtin_summary"/);
  }
});
