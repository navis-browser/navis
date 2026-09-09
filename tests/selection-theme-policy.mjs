// SPDX-License-Identifier: MPL-2.0

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const chrome = read("../platform/gecko-chrome/chrome/content/design-system.css");
const main = read("../platform/gecko-chrome/chrome/content/main.css");
const internal = read("../platform/gecko-chrome/chrome/content/internal-pages.mjs");
const native = read("../platform/android/src/main/java/org/navis/browser/ui/NavisTheme.kt");
const paint = read("../runtime/gecko/layout/generic/nsTextPaintStyle.cpp");
const look = read("../runtime/gecko/widget/nsXPLookAndFeel.cpp");
const defaults = read("../runtime/gecko/modules/libpref/init/all.js");

assert.match(chrome, /::selection\s*\{\s*background-color: var\(--navis-color-selection\);\s*color: var\(--navis-color-on-selection\);/,
  "Browser text selection must share the theme tokens across editable and ordinary text");
assert.doesNotMatch(main, /\.omnibox-input::selection/,
  "The omnibox must consume the shared selection rule");
assert.match(chrome, /--navis-color-selection: color-mix\(in srgb, var\(--navis-color-accent\) 20%, var\(--navis-color-surface\)\)/);
assert.match(chrome, /@media \(forced-colors: active\)[\s\S]*--navis-color-selection: Highlight;[\s\S]*--navis-color-on-selection: HighlightText;/);
assert.match(internal, /--navis-color-selection: color-mix\(in srgb, var\(--accent\) 20%, var\(--surface\)\)/);
assert.match(internal, /::selection \{ background-color: var\(--navis-color-selection\); color: var\(--navis-color-on-selection\); \}/);
assert.match(internal, /@media \(forced-colors: active\) \{ :root \{ --navis-color-selection: Highlight; --navis-color-on-selection: HighlightText; \}/);
assert.match(native, /LocalTextSelectionColors provides TextSelectionColors\(\s*handleColor = colors.primary,\s*backgroundColor = colors.primary.copy\(alpha = \.20f\)/);

// The accent can be any RGB value. Check the full channel extremes and a grid
// against each owned surface's inherited text, including private chrome.
const rgb = hex => hex.match(/[0-9a-f]{2}/gi).map(value => parseInt(value, 16));
const luminance = color => color.map(value => {
  const n = value / 255;
  return n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4;
}).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) /
  (Math.min(luminance(a), luminance(b)) + .05);
for (const [surface, foreground] of [
  ["ffffff", "1f1f1f"], ["292c30", "e3e3e3"], ["31283d", "f1eafb"],
  ["292a2d", "e8eaed"], ["f8fafd", "1f1f1f"], ["37393f", "e3e3e3"],
]) {
  for (const r of [0, 51, 102, 153, 204, 255]) for (const g of [0, 51, 102, 153, 204, 255]) for (const b of [0, 51, 102, 153, 204, 255]) {
    const background = rgb(surface).map((value, index) => value * .80 + [r, g, b][index] * .20);
    assert(contrast(background, rgb(foreground)) >= 4.5, `${surface}/${foreground} selection remains readable for ${r},${g},${b}`);
  }
}

const selection = paint.slice(paint.indexOf("bool nsTextPaintStyle::InitSelectionColorsAndShadow()"), paint.indexOf("void nsTextPaintStyle::InitTargetTextPseudoStyle()"));
const authored = selection.indexOf("HasAuthorSpecifiedTextColor()");
const normal = selection.indexOf("case nsISelectionController::SELECTION_ON:");
const hook = selection.indexOf("GetNavisSelectionColors(");
assert(authored >= 0 && normal > authored && hook > normal,
  "Only the active native fallback may use Navis colors, after author foreground/background decisions");
assert(selection.slice(authored, normal).includes("return true;"));
assert(selection.slice(hook).includes("EnsureSufficientContrast"));
assert.match(paint, /!prefs\.mUseDocumentColors \|\| prefs\.mUseAccessibilityTheme/);
assert.match(paint, /ShouldUseStandins\(\*doc, LookAndFeel::ColorID::Highlight\)\s*==\s*LookAndFeel::UseStandins::Yes/);
assert.match(paint, /widget_non_native_theme_always_high_contrast\(\)/);
assert.match(paint, /#ifdef MOZ_NAVIS_CORE[\s\S]*GetNavisSelectionColors/);
assert.match(look, /#ifdef MOZ_NAVIS_CORE\s*\{"navis.appearance.accent"_ns, widget::ThemeChangeKind::Style\},\s*#endif/);
assert.match(defaults, /#ifdef MOZ_NAVIS_CORE\s*pref\("navis.appearance.accent", "#0b57d0"\);\s*#endif/,
  "A fixed default keeps the bounded appearance value available in content processes");
assert.doesNotMatch(paint, /Preferences::Set/);
console.log("PASS selection theme policy: shared chrome/internal/native tokens, 1296 contrast cases, author/disabled/attention/forced-color/stand-in boundaries, live accent invalidation and fixed content-pref default; native execution pending");
