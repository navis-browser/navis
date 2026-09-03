import assert from "node:assert/strict";

import {
  getNavisInternalPage,
  getNavisInternalPages,
  isNavisInternalErrorId,
  resolveNavisInternalPageURI,
} from "../embedder/modules/DesktopInternalPages.sys.mjs";
import { renderNavisInternalPage } from "../product/chrome/content/internal-pages.mjs";
import { createNavisLocalizer } from "../product/chrome/content/localization-core.mjs";

function uri({
  scheme = "navis",
  host = "urls",
  path = "/",
  port = -1,
  userPass = false,
  query = false,
  canonical = `navis://${host}${path}`,
} = {}) {
  return {
    asciiHost: host,
    filePath: path,
    hasQuery: query,
    hasUserPass: userPass,
    port,
    specIgnoringRef: canonical,
    schemeIs(candidate) {
      return candidate === scheme;
    },
  };
}

const pages = getNavisInternalPages();
assert.deepEqual(
  pages.map((page) => page.id),
  [
    "newtab",
    "history",
    "bookmarks",
    "passwords",
    "downloads",
    "extensions",
    "urls",
    "support",
    "settings",
  ],
);
assert.ok(Object.isFrozen(pages));
assert.ok(pages.every(Object.isFrozen));
assert.equal(getNavisInternalPage("support")?.url, "navis://support/");
assert.equal(getNavisInternalPage("newtab")?.url, "navis://newtab/");
assert.equal(getNavisInternalPage("history")?.url, "navis://history/");
assert.equal(getNavisInternalPage("bookmarks")?.url, "navis://bookmarks/");
assert.equal(getNavisInternalPage("passwords")?.url, "navis://passwords/");
assert.equal(getNavisInternalPage("downloads")?.url, "navis://downloads/");
assert.equal(getNavisInternalPage("extensions")?.url, "navis://extensions/");
assert.equal(getNavisInternalPage("settings")?.url, "navis://settings/");
assert.equal(
  getNavisInternalPage("settings", "help")?.url,
  "navis://settings/help",
);
assert.equal(getNavisInternalPage("missing"), null);

assert.equal(resolveNavisInternalPageURI(uri())?.id, "urls");
assert.equal(
  resolveNavisInternalPageURI(
    uri({ host: "support", canonical: "navis://support/" }),
  )?.id,
  "support",
);
assert.equal(
  resolveNavisInternalPageURI(uri({ host: "settings", path: "/help" }))?.key,
  "settings/help",
);
for (const malformed of [
  uri({ scheme: "https" }),
  uri({ host: "missing", canonical: "navis://missing/" }),
  uri({ path: "/extra" }),
  uri({ host: "settings", path: "/missing" }),
  uri({ host: "settings", path: "/help/" }),
  uri({
    host: "settings",
    path: "/help",
    canonical: "navis://settings/%68elp",
  }),
  uri({ port: 443, canonical: "navis://urls:443/" }),
  uri({ userPass: true, canonical: "navis://user@urls/" }),
  uri({ query: true, canonical: "navis://urls/?query" }),
  uri({ canonical: "navis://urls.evil/" }),
  null,
]) {
  assert.equal(resolveNavisInternalPageURI(malformed), null);
}

for (const id of ["certerror", "framecrashed", "httpsonlyerror", "neterror"]) {
  assert.equal(isNavisInternalErrorId(id), true);
}
assert.equal(isNavisInternalErrorId("support"), false);

const diagnosticInjection = `<img src=x onerror=alert(1)> & "quoted"`;
const support = renderNavisInternalPage({
  page: getNavisInternalPage("support"),
  pages,
  diagnostics: {
    application: [
      ["diagnostics.name", diagnosticInjection],
      [
        "diagnostics.sessionUptime",
        { messageId: "common.loading", dynamic: true },
      ],
    ],
    engine: [
      ["diagnostics.geckoVersion", "Gecko"],
      [
        "diagnostics.remoteProcesses",
        { messageId: "common.loading", dynamic: true },
      ],
    ],
    graphics: [
      [
        "diagnostics.compositor",
        { messageId: "common.loading", dynamic: true },
      ],
      [
        "diagnostics.gpuProcess",
        { messageId: "common.loading", dynamic: true },
      ],
    ],
    media: [["diagnostics.audioBackend", "PipeWire"]],
    network: [["diagnostics.proxyMode", "System proxy"]],
    capabilities: [["diagnostics.webAuthn", "Included"]],
    system: [["diagnostics.operatingSystem", "Linux"]],
  },
  nonce: "test-nonce",
});
assert.match(support, /default-src 'none'/u);
assert.match(support, /script-src 'nonce-test-nonce'/u);
assert.match(support, /style-src 'nonce-test-nonce'/u);
assert.match(support, /<html lang="en-US" dir="ltr" data-page-key="support">/u);
assert.match(support, /NavisDiagnosticsCommand/u);
assert.match(support, /diagnostics:get/u);
assert.match(support, /data-diagnostic-label="diagnostics\.compositor"/u);
assert.match(support, /data-diagnostic-dynamic/u);
assert.doesNotMatch(support, /<img src=x/u);
assert.match(
  support,
  /&lt;img src=x onerror=alert\(1\)&gt; &amp; &quot;quoted&quot;/u,
);
assert.match(support, /This page is generated locally/u);
assert.match(support, /Compositor/u);
assert.match(support, /Loading…/u);
assert.match(support, /GPU process/u);
assert.match(support, /Copy diagnostic information/u);
assert.match(support, /Security and capabilities/u);
assert.match(support, /state\.diagnostics/u);
assert.match(support, /<script nonce="test-nonce">/u);
assert.equal(support.match(/<script nonce="test-nonce">/gu)?.length, 3);
assert.match(support, /window\.NavisL10n/u);
assert.match(support, /material-ripple/u);
assert.match(support, /diagnosticsState/u);
assert.doesNotMatch(support, /<script[^>]+src=/u);
for (const absentProductSurface of [
  /update channel/iu,
  /check for updates/iu,
  /software updater/iu,
  /account sync/iu,
  /safe browsing/iu,
  /web push/iu,
  /widevine/iu,
  /telemetry/iu,
  /crash submission/iu,
  /enterprise policy/iu,
  /user-installable extensions/iu,
]) {
  assert.doesNotMatch(support, absentProductSurface);
}

const urls = renderNavisInternalPage({
  page: getNavisInternalPage("urls"),
  pages,
  diagnostics: {},
  nonce: "urls-nonce",
});
for (const page of pages) {
  assert.match(urls, new RegExp(page.url.replaceAll("/", "\\/"), "u"));
}
assert.match(urls, /navis:\/\/settings\//u);
assert.doesNotMatch(urls, /navis:\/\/settings\/help/u);
assert.match(urls, /script-src 'nonce-urls-nonce'/u);
assert.equal(urls.match(/<script nonce="urls-nonce">/gu)?.length, 1);
assert.match(urls, /material-ripple/u);
assert.doesNotMatch(urls, /<script[^>]+src=/u);

const settings = renderNavisInternalPage({
  page: getNavisInternalPage("settings"),
  pages,
  diagnostics: {
    application: [
      ["diagnostics.name", "Navis"],
      ["diagnostics.version", "1.0"],
      ["diagnostics.buildId", "test-build"],
    ],
    engine: [["diagnostics.geckoVersion", "153.1"]],
    system: [["diagnostics.operatingSystem", "Linux"]],
  },
  nonce: "settings-nonce",
});
assert.match(settings, /navis:\/\/settings\/help/u);
assert.match(settings, /id="settings-search"/u);
assert.doesNotMatch(settings, /Content blocker/u);
assert.doesNotMatch(settings, /blocker-toggle/u);
assert.match(settings, /id="search-provider"/u);
assert.match(settings, /id="display-language"/u);
assert.match(settings, /settings:set-locale/u);
assert.match(settings, /id="display-language-relaunch"/u);
assert.match(settings, /settings:relaunch-locale/u);
assert.match(settings, /id="process-isolation"/u);
assert.match(settings, /value="full"/u);
assert.match(settings, /value="selective"/u);
assert.match(settings, /value="shared"/u);
assert.match(settings, /Full site isolation/u);
assert.match(settings, /Selective site isolation/u);
assert.match(settings, /Shared web processes/u);
assert.match(settings, /Every mode keeps multiprocess browsing/u);
assert.match(settings, /id="process-isolation-relaunch"/u);
assert.match(settings, /settings:set-process-isolation/u);
assert.match(settings, /settings:relaunch-process-isolation/u);
assert.match(settings, /id="clean-links-toggle"/u);
assert.match(settings, /settings:set-clean-links/u);
assert.match(settings, /Clean links automatically/u);
assert.match(settings, /parameters after \? in a link/u);
assert.match(settings, /one bundled, versioned policy/u);
assert.match(settings, /policy works offline/u);
assert.match(settings, /copy or open links/u);
assert.match(settings, /does not remove every URL parameter/u);
assert.match(
  settings,
  /https:\/\/firefox-source-docs\.mozilla\.org\/toolkit\/components\/antitracking\/anti-tracking\/query-stripping\/index\.html/u,
);
assert.match(settings, /target="_blank" rel="noopener noreferrer"/u);
assert.match(settings, /How query-parameter stripping works \(Mozilla\)/u);
assert.match(settings, /navis:\/\/history\//u);
assert.match(settings, /navis:\/\/bookmarks\//u);
assert.match(settings, /navis:\/\/passwords\//u);
assert.match(settings, /navis:\/\/downloads\//u);
assert.match(settings, /navis:\/\/extensions\//u);
assert.match(settings, /Clear cookies and site data/u);
assert.match(settings, /script-src 'nonce-settings-nonce'/u);
assert.match(settings, /<script nonce="settings-nonce">/u);
assert.doesNotMatch(settings, /NavisDiagnosticsCommand/u);

const help = renderNavisInternalPage({
  page: getNavisInternalPage("settings", "help"),
  pages,
  diagnostics: {
    application: [
      ["diagnostics.name", "Navis"],
      ["diagnostics.version", "1.0"],
      ["diagnostics.buildId", "test-build"],
    ],
    engine: [["diagnostics.geckoVersion", "153.1"]],
    system: [["diagnostics.operatingSystem", "Linux"]],
  },
  nonce: "help-nonce",
});
assert.match(help, /<h1>About Navis<\/h1>/u);
assert.match(help, /navis:\/\/support\//u);
assert.match(help, /navis:\/\/urls\//u);
assert.match(help, /data-page-key="settings\/help"/u);
assert.match(help, /NavisDiagnosticsCommand/u);
assert.doesNotMatch(help, /NavisSettingsCommand/u);
assert.doesNotMatch(help, /Process model/u);

for (const [id, marker] of [
  ["newtab", 'id="newtab-search"'],
  ["history", 'id="management-search"'],
  ["bookmarks", 'id="bookmark-add-form"'],
  ["passwords", "Clear saved passwords"],
  ["downloads", 'id="management-list"'],
  ["extensions", 'id="extension-list"'],
]) {
  const rendered = renderNavisInternalPage({
    page: getNavisInternalPage(id),
    pages,
    diagnostics: { application: [], engine: [], system: [] },
    nonce: `${id}-nonce`,
  });
  assert.match(rendered, new RegExp(marker, "u"));
  assert.match(rendered, /NavisManagementCommand/u);
  assert.match(rendered, /NavisManagementState/u);
  if (id !== "newtab") {
    assert.match(rendered, /status\.setAttribute\("aria-label", message\)/u);
  }
  assert.match(rendered, new RegExp(`data-page-key="${id}"`, "u"));
  assert.match(rendered, new RegExp(`script-src 'nonce-${id}-nonce'`, "u"));
}

const extensions = renderNavisInternalPage({
  page: getNavisInternalPage("extensions"),
  pages,
  diagnostics: { application: [], engine: [], system: [] },
  nonce: "extensions-nonce",
});
assert.match(extensions, /Built-in extensions/u);
assert.match(extensions, /Your extensions/u);
assert.match(extensions, /Install from file/u);
assert.match(extensions, /id="extension-review"/u);
assert.match(extensions, /extensions:confirm-install/u);
assert.match(extensions, /extensions:set-enabled/u);
assert.match(extensions, /canChangeEnabled/u);
assert.match(extensions, /extensions:uninstall/u);
assert.match(extensions, /quietRefreshPending/u);
assert.match(extensions, /preserveStatus/u);
assert.match(extensions, /quiet: true/u);
assert.match(extensions, /script-src 'nonce-extensions-nonce'/u);

const chineseExtensions = renderNavisInternalPage({
  page: getNavisInternalPage("extensions"),
  pages,
  diagnostics: { application: [], engine: [], system: [] },
  locale: "zh-CN",
  nonce: "zh-extensions-nonce",
});
assert.match(chineseExtensions, /内置扩展/u);
assert.match(chineseExtensions, /你的扩展/u);
assert.match(chineseExtensions, /从文件安装/u);

for (const label of [
  "Click again to clear browsing history",
  "Click again to clear saved passwords",
]) {
  assert.match(
    renderNavisInternalPage({
      page: getNavisInternalPage(
        label.includes("history") ? "history" : "passwords",
      ),
      pages,
      diagnostics: { application: [], engine: [], system: [] },
      nonce: "confirmation-nonce",
    }),
    new RegExp(label, "u"),
  );
}

const chineseSettings = renderNavisInternalPage({
  page: getNavisInternalPage("settings"),
  pages,
  diagnostics: { application: [], engine: [], system: [] },
  locale: "zh-CN",
  nonce: "zh-settings-nonce",
});
assert.match(chineseSettings, /<html lang="zh-CN" dir="ltr"/u);
assert.match(chineseSettings, /隐私和安全/u);
assert.match(chineseSettings, /搜索引擎/u);
assert.match(chineseSettings, /Cookie 和网站数据/u);
assert.match(chineseSettings, /优先使用系统语言/u);
assert.match(chineseSettings, /完整站点隔离/u);
assert.match(chineseSettings, /选择性站点隔离/u);
assert.match(chineseSettings, /共享网页进程/u);
assert.match(chineseSettings, /所有模式都会保持多进程/u);
assert.match(chineseSettings, /自动清理链接/u);
assert.match(chineseSettings, /用来跨站识别访问者或统计流量来源/u);
assert.match(chineseSettings, /一份内置且版本化的策略/u);
assert.match(chineseSettings, /该策略可离线使用/u);
assert.match(chineseSettings, /不能替代内容拦截/u);
assert.match(chineseSettings, /查看查询参数清理原理（Mozilla）/u);
assert.doesNotMatch(chineseSettings, />Privacy and security</u);

const chineseHistory = renderNavisInternalPage({
  page: getNavisInternalPage("history"),
  pages,
  diagnostics: { application: [], engine: [], system: [] },
  locale: "zh-CN",
  nonce: "zh-history-nonce",
});
assert.match(chineseHistory, /浏览和管理本地浏览历史记录/u);
assert.match(chineseHistory, /清除浏览历史记录/u);

const englishLocalizer = createNavisLocalizer("en-US");
const chineseLocalizer = createNavisLocalizer("zh-CN");
for (const page of [...pages, getNavisInternalPage("settings", "help")]) {
  const rendered = renderNavisInternalPage({
    page,
    pages,
    diagnostics: { application: [], engine: [], system: [] },
    locale: "zh-CN",
    nonce: `zh-${page.key.replaceAll("/", "-")}-nonce`,
  });
  const chineseTitle = chineseLocalizer.text(page.titleId);
  const chineseDescription = chineseLocalizer.text(page.descriptionId);
  const englishTitle = englishLocalizer.text(page.titleId);
  const englishDescription = englishLocalizer.text(page.descriptionId);
  assert.match(rendered, /<html lang="zh-CN" dir="ltr"/u);
  assert.ok(
    rendered.includes(chineseTitle),
    `${page.key} lacks its Chinese title`,
  );
  assert.ok(
    rendered.includes(chineseDescription),
    `${page.key} lacks its Chinese description`,
  );
  if (englishTitle !== chineseTitle) {
    assert.equal(
      rendered.includes(`>${englishTitle}<`),
      false,
      `${page.key} exposed its English title in Chinese mode`,
    );
  }
  if (englishDescription !== chineseDescription) {
    assert.equal(
      rendered.includes(englishDescription),
      false,
      `${page.key} exposed its English description in Chinese mode`,
    );
  }
}

console.log("Navis internal-page pure module tests passed.");
