import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { EN_US_MESSAGES } from "../product/chrome/content/locales/en-US.mjs";
import { ZH_CN_MESSAGES } from "../product/chrome/content/locales/zh-CN.mjs";
import {
  NAVIS_LOCALES,
  createNavisLocalizer,
  navisMessagePlaceholders,
  normalizeNavisLocale,
} from "../product/chrome/content/localization-core.mjs";

const englishKeys = Object.keys(EN_US_MESSAGES).sort();
const chineseKeys = Object.keys(ZH_CN_MESSAGES).sort();

assert.deepEqual(NAVIS_LOCALES, ["en-US", "zh-CN"]);
assert.ok(Object.isFrozen(NAVIS_LOCALES));
assert.ok(Object.isFrozen(EN_US_MESSAGES));
assert.ok(Object.isFrozen(ZH_CN_MESSAGES));
assert.deepEqual(chineseKeys, englishKeys, "catalogue keys must have exact parity");

for (const id of englishKeys) {
  const english = EN_US_MESSAGES[id];
  const chinese = ZH_CN_MESSAGES[id];
  assert.equal(typeof english, typeof chinese, `${id} record type differs`);
  if (typeof english === "object") {
    assert.deepEqual(
      Object.keys(chinese).sort(),
      Object.keys(english).sort(),
      `${id} plural categories differ`
    );
    assert.ok(Object.hasOwn(english, "other"), `${id} lacks an other plural`);
  }
  for (const [locale, record] of [
    ["en-US", english],
    ["zh-CN", chinese],
  ]) {
    const patterns = typeof record === "string" ? [record] : Object.values(record);
    assert.ok(patterns.length > 0, `${locale} ${id} has no patterns`);
    for (const pattern of patterns) {
      assert.equal(typeof pattern, "string", `${locale} ${id} is not text`);
      assert.ok(pattern.trim(), `${locale} ${id} is empty`);
      assert.equal(/[<>]/u.test(pattern), false, `${locale} ${id} contains markup`);
    }
  }
  assert.deepEqual(
    navisMessagePlaceholders(chinese),
    navisMessagePlaceholders(english),
    `${id} placeholders differ`
  );
}

for (const [locale, messages] of [
  ["en-US", EN_US_MESSAGES],
  ["zh-CN", ZH_CN_MESSAGES],
]) {
  const localizer = createNavisLocalizer(locale);
  for (const [id, record] of Object.entries(messages)) {
    const baseVariables = Object.fromEntries(
      navisMessagePlaceholders(record).map(name => [
        name,
        name === "count" ? 1 : `${name}-value`,
      ])
    );
    const counts = typeof record === "string" ? [baseVariables.count] : [1, 2];
    for (const count of counts) {
      const variables = Object.hasOwn(baseVariables, "count")
        ? { ...baseVariables, count }
        : baseVariables;
      const rendered = localizer.text(id, variables);
      assert.ok(rendered.trim(), `${locale} ${id} formatted to empty text`);
      assert.equal(
        /\{[A-Za-z][A-Za-z0-9]*\}/u.test(rendered),
        false,
        `${locale} ${id} left an unresolved placeholder`
      );
    }
  }
}

const chromeMarkup = readFileSync(
  new URL("../product/chrome/content/main.xhtml", import.meta.url),
  "utf8",
);
for (const match of chromeMarkup.matchAll(
  /data-l10n-(?:id|aria-label|title|placeholder)="([A-Za-z][A-Za-z0-9.]+)"/gu,
)) {
  const id = match[1];
  assert.deepEqual(
    navisMessagePlaceholders(EN_US_MESSAGES[id]),
    [],
    `static chrome message ${id} requires runtime variables`,
  );
}

assert.equal(normalizeNavisLocale("zh-CN"), "zh-CN");
assert.equal(normalizeNavisLocale("fr-FR"), "en-US");

const en = createNavisLocalizer("en-US");
const zh = createNavisLocalizer("zh-CN");
assert.equal(en.text("tabs.count", { count: 1 }), "1 tab");
assert.equal(en.text("tabs.count", { count: 2 }), "2 tabs");
assert.equal(en.text("tabs.count", { count: 12345 }), "12,345 tabs");
assert.equal(zh.text("tabs.count", { count: 2 }), "2 个标签页");
assert.equal(zh.text("tabs.count", { count: 12345 }), "12,345 个标签页");
assert.equal(zh.text("prompt.authTitle"), "需要身份验证");
assert.equal(zh.text("prompt.authMessage"), "此网站要求您登录。");
assert.equal(zh.text("prompt.signIn"), "登录");
assert.equal(
  zh.text("siteInfo.dataCleared", { host: "example.com" }),
  "已清除 example.com 的 Cookie 和网站数据"
);
assert.throws(() => en.text("missing.message"), RangeError);
assert.throws(() => en.text("siteInfo.dataCleared"), TypeError);
assert.throws(
  () => en.text("siteInfo.dataCleared", { host: "example.com", extra: true }),
  TypeError
);

console.log(`Navis localization catalogue tests passed (${englishKeys.length} messages).`);
