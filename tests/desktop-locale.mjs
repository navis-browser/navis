import assert from "node:assert/strict";

import {
  DESKTOP_AVAILABLE_LOCALES,
  DESKTOP_LOCALE_SETTINGS,
  isDesktopLocaleSetting,
  resolveDesktopLocale,
} from "../embedder/modules/DesktopLocale.sys.mjs";

assert.deepEqual(DESKTOP_AVAILABLE_LOCALES, ["en-US", "zh-CN"]);
assert.deepEqual(DESKTOP_LOCALE_SETTINGS, ["system", "en-US", "zh-CN"]);
assert.ok(Object.isFrozen(DESKTOP_AVAILABLE_LOCALES));
assert.ok(Object.isFrozen(DESKTOP_LOCALE_SETTINGS));

for (const setting of DESKTOP_LOCALE_SETTINGS) {
  assert.equal(isDesktopLocaleSetting(setting), true);
}
for (const setting of ["", "en", "zh", "zh-TW", "fr", null, 1]) {
  assert.equal(isDesktopLocaleSetting(setting), false);
}

for (const locale of ["zh", "zh-CN", "zh-Hans", "zh-HK", "zh-Hant-TW"]) {
  assert.equal(resolveDesktopLocale("system", locale), "zh-CN");
}
for (const locale of ["en", "en-US", "fr-FR", "invalid_locale", ""]) {
  assert.equal(resolveDesktopLocale("system", locale), "en-US");
}
assert.equal(resolveDesktopLocale("en-US", "zh-CN"), "en-US");
assert.equal(resolveDesktopLocale("zh-CN", "en-US"), "zh-CN");
assert.equal(resolveDesktopLocale("unsupported", "zh-CN"), "en-US");

console.log("Navis desktop-locale pure module tests passed.");
