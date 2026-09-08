import assert from "node:assert/strict";
import { rankLocalSuggestions } from "../embedder/modules/NavisSuggestionPolicy.sys.mjs";

const rows = [
  {kind: "history", id: "h", title: "Rust guide", url: "https://rust.test/", lastVisit: 10},
  {kind: "bookmark", id: "b", title: "Rust reference", url: "https://rust.test/"},
  {kind: "tab", id: "t", title: "Rust open", url: "https://rust.test/"},
  {kind: "history", id: "c", title: "中文 搜索", url: "https://example.test/chinese"},
];
assert.deepEqual(rankLocalSuggestions("rust", rows), [
  {kind: "tab", id: "t", title: "Rust open", url: "https://rust.test/"},
]);
assert.equal(rankLocalSuggestions("中文", rows)[0].id, "c");
assert.equal(rankLocalSuggestions("Ｒｕｓｔ", rows)[0].id, "t");
assert.deepEqual(rankLocalSuggestions("", rows), []);
assert.deepEqual(rankLocalSuggestions("rust", rows, {maxResults: 0}), []);
assert.deepEqual(rankLocalSuggestions("中文", rows, {privateMode: true}), []);
assert.equal(rankLocalSuggestions("rust", rows, {privateMode: true})[0].kind, "tab");
assert.deepEqual(rankLocalSuggestions("script", [
  {kind: "history", id: "x", title: "script", url: "javascript:alert(1)"},
  {kind: "tab", id: "x", title: "script", url: "data:text/html,x"},
  {kind: "unknown", id: "x", title: "script", url: "https://example.test/"},
]), []);
const unsafe = {kind: "bookmark", id: "creds", title: "match", url: "https://user:secret@example.test/"};
assert.deepEqual(rankLocalSuggestions("match", [unsafe]), []);
assert.deepEqual(rankLocalSuggestions("zz", rows), []);
assert.deepEqual(rows.map(row => row.id), ["h", "b", "t", "c"]);
const order = [
  {kind: "history", id: "old", title: "Guide", url: "https://b.test/", lastVisit: 1},
  {kind: "history", id: "new", title: "Guide", url: "https://a.test/", lastVisit: 2},
];
assert.equal(rankLocalSuggestions("guide", order)[0].id, "new");
assert.deepEqual(rankLocalSuggestions("guide", [...order].reverse()), rankLocalSuggestions("guide", order));
assert.equal(rankLocalSuggestions("guide", order, {maxResults: 1}).length, 1);
assert.equal(rankLocalSuggestions("rust open", rows)[0].id, "t");
assert.equal(rankLocalSuggestions("rust missing", rows).length, 0);
const result = rankLocalSuggestions("rust", rows);
assert.ok(Object.isFrozen(result) && Object.isFrozen(result[0]));
assert.deepEqual(rankLocalSuggestions(null, rows), []);
assert.deepEqual(rankLocalSuggestions("rust", null), []);
console.log("Local suggestion ranking: focused assertions passed");
