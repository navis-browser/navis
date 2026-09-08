import assert from "node:assert/strict";
import fs from "node:fs";
import { classifyAddressInput } from "../embedder/modules/DesktopAddressInput.sys.mjs";
import { rankLocalSuggestions } from "../embedder/modules/NavisSuggestionPolicy.sys.mjs";

const source = fs.readFileSync(new URL("../embedder/modules/NavisAndroidSearch.sys.mjs", import.meta.url), "utf8")
  .replace(/^import .*;\n/gmu, "").replaceAll("export async function", "async function");
const submissions = [];
const network = [];
const service = {
  snapshot: { providers: [], defaultProviderId: "custom", remoteSuggestionsEnabled: false },
  async init(legacy) { this.legacy = legacy; return this.snapshot; },
  submission(query, options) { submissions.push({query, options}); return {url: "https://custom.test/search"}; },
  suggestions(query, options) { return new Promise(resolve => network.push({query, options, resolve})); },
  async setDefault(id) { this.snapshot = {...this.snapshot, defaultProviderId: id}; },
  async setRemoteSuggestionsEnabled(enabled) { this.snapshot = {...this.snapshot, remoteSuggestionsEnabled: enabled}; },
};
const api = new Function("NavisSearchService", "classifyAddressInput", "rankLocalSuggestions", "PrivateBrowsingUtils",
  source + "\nreturn {initializeAndroidSearch, queryAndroidSearch};")(
  service, classifyAddressInput, rankLocalSuggestions, {isBrowserPrivate: browser => browser.privateMode},
);
const browser = {privateMode: false, browsingContext: {currentWindowGlobal: {}}};
const window = Object.assign(new EventTarget(), {tab: {linkedBrowser: browser}, closed: false});
const query = (operation, data = {}) => api.queryAndroidSearch(window, `search:${operation}`, data);
const legacy = {providers: [{id: "custom"}], defaultProviderId: "custom", remoteSuggestionsEnabled: false};
assert.equal(await api.initializeAndroidSearch(legacy), service.snapshot);
assert.equal(service.legacy, legacy);
assert.equal((await query("default", {id: "changed"})).defaultProviderId, "changed");
assert.deepEqual(await query("resolve", {query: "localhost:8000", forceSearch: false}), {url: "http://localhost:8000"});
assert.equal(submissions.length, 0);
await query("resolve", {query: "search terms", forceSearch: false});
assert.equal(submissions.at(-1).query, "search terms");
browser.privateMode = true;
await query("resolve", {query: "https://selected.test/", forceSearch: true});
assert.equal(submissions.at(-1).query, "https://selected.test/");
assert.equal(submissions.at(-1).options.privateMode, true);
await assert.rejects(query("resolve", {query: "secret", forceSearch: true, privateMode: false}), /Invalid search arguments/);
const local = await query("local-rank", {query: "example", candidates: [
  {kind: "history", id: "h", title: "Example visit", url: "https://history.test/"},
  {kind: "bookmark", id: "b", title: "Example bookmark", url: "https://bookmark.test/"},
]});
assert.deepEqual(local.suggestions.map(row => row.id), ["b"]);
const first = query("suggest", {query: "first", requestId: "first"});
assert.equal(network[0].options.privateMode, true);
const second = query("suggest", {query: "second", requestId: "second"});
assert.equal(network[0].options.signal.aborted, true);
await query("cancel", {requestId: "first"});
assert.equal(network[1].options.signal.aborted, false);
network[0].resolve(["old"]);
assert.deepEqual(await first, {suggestions: []});
await query("cancel", {requestId: "second"});
assert.equal(network[1].options.signal.aborted, true);
network[1].resolve(["cancelled"]);
assert.deepEqual(await second, {suggestions: []});
const navigation = query("suggest", {query: "navigation", requestId: "navigation"});
browser.browsingContext.currentWindowGlobal = {};
network[2].resolve(["stale document"]);
assert.deepEqual(await navigation, {suggestions: []});
const unload = query("suggest", {query: "unload", requestId: "unload"});
window.dispatchEvent(new Event("unload"));
assert.equal(network[3].options.signal.aborted, true);
network[3].resolve([]);
assert.deepEqual(await unload, {suggestions: []});
await assert.rejects(query("suggest", {query: "terms", requestId: "invalid token"}), /identity/);
await assert.rejects(query("local-rank", {query: "terms", candidates: Array(501).fill({})}), /candidates/);
window.closed = true;
await assert.rejects(query("get"), /live host/);
console.log("Android search bridge: migration projection, Core submission, authenticated privacy, local filtering, cancellation and stale-document guards passed");
