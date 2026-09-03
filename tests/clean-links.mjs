import assert from "node:assert/strict";

const preferences = new Map();
let stripCalls = 0;
let navigationListRegistrations = 0;
let copyListRegistrations = 0;

function testURI(spec) {
  const parsed = new URL(spec);
  return {
    displaySpec: parsed.href,
    schemeIs(scheme) {
      return parsed.protocol === `${scheme}:`;
    },
  };
}

globalThis.Services = {
  prefs: {
    getBoolPref(name, fallback) {
      return preferences.has(name) ? preferences.get(name) : fallback;
    },
    setBoolPref(name, value) {
      preferences.set(name, value);
    },
  },
  io: {
    newURI: testURI,
    createExposableURI(uri) {
      return uri;
    },
  },
};
globalThis.ChromeUtils = {
  generateQI() {
    return () => {};
  },
};
globalThis.Ci = {
  nsIURLQueryStringStripper: Symbol("stripper"),
  nsIURLQueryStrippingListService: Symbol("list-service"),
};
globalThis.Cc = {
  "@mozilla.org/url-query-string-stripper;1": {
    getService() {
      return {
        stripForCopyOrShare(uri) {
          stripCalls++;
          const parsed = new URL(uri.displaySpec);
          if (!parsed.searchParams.has("utm_source")) {
            return null;
          }
          parsed.searchParams.delete("utm_source");
          return testURI(parsed.href);
        },
      };
    },
  },
  "@mozilla.org/query-stripping-list-service;1": {
    getService() {
      return {
        registerAndRunObserver(observer) {
          navigationListRegistrations++;
          observer.onQueryStrippingListUpdate("utm_source", "");
        },
        registerAndRunObserverStripOnShare(observer) {
          copyListRegistrations++;
          observer.onStripOnShareUpdate([]);
        },
        unregisterObserver() {},
        unregisterStripOnShareObserver() {},
      };
    },
  },
};

const {
  DESKTOP_CLEAN_LINKS_PREF,
  getDesktopCleanLinksEnabled,
  prepareDesktopCleanLinks,
  projectDesktopCopiedLink,
  setDesktopCleanLinksEnabled,
  synchronizeDesktopCleanLinkPreferences,
} = await import("../embedder/modules/DesktopCleanLinks.sys.mjs");

assert.equal(getDesktopCleanLinksEnabled(), false);
assert.equal(synchronizeDesktopCleanLinkPreferences(), false);
for (const name of [
  "privacy.query_stripping.enabled",
  "privacy.query_stripping.enabled.pbmode",
  "privacy.query_stripping.redirect",
  "privacy.query_stripping.strip_on_share.enabled",
]) {
  assert.equal(preferences.get(name), false);
}

assert.throws(() => setDesktopCleanLinksEnabled("yes"), TypeError);
assert.equal(setDesktopCleanLinksEnabled(true), true);
assert.equal(await prepareDesktopCleanLinks(), true);
assert.equal(await prepareDesktopCleanLinks(), true);
assert.equal(navigationListRegistrations, 1);
assert.equal(copyListRegistrations, 1);
assert.equal(preferences.get(DESKTOP_CLEAN_LINKS_PREF), true);
for (const name of [
  "privacy.query_stripping.enabled",
  "privacy.query_stripping.enabled.pbmode",
  "privacy.query_stripping.redirect",
  "privacy.query_stripping.strip_on_share.enabled",
]) {
  assert.equal(preferences.get(name), true);
}

assert.equal(
  projectDesktopCopiedLink(
    "https://example.com/path?utm_source=navis-test&kept=yes",
  ),
  "https://example.com/path?kept=yes",
);
assert.equal(
  projectDesktopCopiedLink("https://example.com/path?kept=yes"),
  "https://example.com/path?kept=yes",
);
assert.equal(
  projectDesktopCopiedLink("navis://settings/"),
  "navis://settings/",
);
assert.equal(stripCalls, 2);

setDesktopCleanLinksEnabled(false);
assert.equal(
  projectDesktopCopiedLink("https://example.com/?utm_source=unchanged"),
  "https://example.com/?utm_source=unchanged",
);
assert.equal(stripCalls, 2);

console.log("Navis clean-links pure module tests passed.");
