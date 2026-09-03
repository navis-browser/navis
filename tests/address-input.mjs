import assert from "node:assert/strict";

import {
  DESKTOP_SEARCH_PROVIDERS,
  resolveDesktopAddressInput,
} from "../embedder/modules/DesktopAddressInput.sys.mjs";

assert.deepEqual(
  DESKTOP_SEARCH_PROVIDERS.map((provider) => provider.id),
  ["google", "baidu", "bing", "duckduckgo"],
);
assert.ok(Object.isFrozen(DESKTOP_SEARCH_PROVIDERS));
assert.ok(DESKTOP_SEARCH_PROVIDERS.every(Object.isFrozen));

const cases = [
  ["https://example.com/path", "google", "url", "https://example.com/path"],
  ["localhost:8080/path", "google", "url", "http://localhost:8080/path"],
  ["app.localhost", "google", "url", "http://app.localhost"],
  ["127.9.8.7:3000", "google", "url", "http://127.9.8.7:3000"],
  ["192.0.2.1", "google", "url", "https://192.0.2.1"],
  ["[::1]:8000", "google", "url", "http://[::1]:8000"],
  ["::1", "google", "url", "http://[::1]"],
  ["[0:0:0:0:0:0:0:1]:8000", "google", "url", "http://[0:0:0:0:0:0:0:1]:8000"],
  ["2001:db8::1", "google", "url", "https://[2001:db8::1]"],
  ["example.com/a", "google", "url", "https://example.com/a"],
  ["localhost.evil", "google", "url", "https://localhost.evil"],
  ["navis://settings/", "google", "url", "navis://settings/"],
  [
    "Navis browser",
    "google",
    "search",
    "https://www.google.com/search?q=Navis%20browser",
  ],
  [
    "小红书",
    "baidu",
    "search",
    "https://www.baidu.com/s?wd=%E5%B0%8F%E7%BA%A2%E4%B9%A6",
  ],
  ["a&b", "bing", "search", "https://www.bing.com/search?q=a%26b"],
  ["privacy", "unknown", "search", "https://www.google.com/search?q=privacy"],
  [
    "999.1.1.1",
    "google",
    "search",
    "https://www.google.com/search?q=999.1.1.1",
  ],
];

for (const [input, provider, kind, url] of cases) {
  const resolved = resolveDesktopAddressInput(input, provider);
  assert.equal(resolved.kind, kind, input);
  assert.equal(resolved.url, url, input);
  assert.ok(Object.isFrozen(resolved), input);
}

assert.throws(() => resolveDesktopAddressInput(null, "google"), TypeError);
assert.throws(() => resolveDesktopAddressInput("", "google"), TypeError);
assert.throws(
  () => resolveDesktopAddressInput("x".repeat(8193), "google"),
  TypeError,
);
for (const address of [
  "javascript:alert(1)",
  "data:text/html,unsafe",
  "file:///etc/passwd",
  "chrome://global/content/",
  "resource://gre/modules/",
]) {
  assert.throws(
    () => resolveDesktopAddressInput(address, "google"),
    /scheme is not available/u,
    address,
  );
}

for (const address of [
  "http://[navis-invalid",
  "https://example.com:70000",
  "http://",
  "navis://[invalid",
]) {
  assert.throws(
    () => resolveDesktopAddressInput(address, "google"),
    /address is malformed/u,
    address,
  );
}

console.log("Navis address-input pure module tests passed.");
