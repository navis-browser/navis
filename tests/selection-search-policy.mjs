import assert from "node:assert/strict";
import {classifyAddressInput, projectSelectionSearch} from "../embedder/modules/DesktopAddressInput.sys.mjs";
const provider = {id: "custom", name: "My engine"};
const selected = text => ({text, password: false, hasSelection: true});
assert.deepEqual(projectSelectionSearch(selected(" hello "), provider), {
  text: "hello", providerId: "custom", providerName: "My engine", excerpt: "hello",
});
assert.equal(projectSelectionSearch({...selected("secret"), password: true}, provider), null);
assert.equal(projectSelectionSearch({...selected("hello"), hasSelection: false}, provider), null);
assert.equal(projectSelectionSearch(selected(" "), provider), null);
assert.equal(projectSelectionSearch(selected("x".repeat(8193)), provider), null);
const long = projectSelectionSearch(selected("字".repeat(100)), provider);
assert.equal(long.text.length, 100);
assert.equal(long.excerpt.length, 41);
assert.equal(projectSelectionSearch(selected("https://example.com"), provider).text, "https://example.com");
assert.equal(classifyAddressInput("https://example.com").kind, "url");
assert.deepEqual(classifyAddressInput("你好 world"), {kind: "search", query: "你好 world"});
console.log("Selection search policy: focused assertions passed");
