import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const file = "mobile/shared/components/extensions/ext-search.js";
let source = fs.readFileSync(new URL(`../gecko/${file}`, import.meta.url), "utf8");
if (!source.includes("NavisSearchService.snapshot")) {
  // Exercise the source-of-record port without mutating the generated Gecko checkout.
  const patch = fs.readFileSync(new URL("../patches/gecko/0108-share-android-search-service-and-suggestions.patch", import.meta.url), "utf8");
  const section = patch.split("diff --git ").find(part => part.startsWith(`a/${file} b/${file}\n`));
  for (const hunk of section.split(/^@@ .* @@\n/mu).slice(1)) {
    const lines = hunk.trimEnd().split("\n");
    const before = lines.filter(line => line.startsWith(" ") || line.startsWith("-")).map(line => line.slice(1)).join("\n");
    const after = lines.filter(line => line.startsWith(" ") || line.startsWith("+")).map(line => line.slice(1)).join("\n");
    assert.ok(source.includes(before), "The Android extension search port must match the selected source");
    source = source.replace(before, after);
  }
}
const calls = [];
const service = {
  snapshot: {providers: [{id: "google", name: "Google"}], defaultProviderId: "google"},
  submission(query, options) {
    calls.push({query, options});
    return {url: `https://search.test/?q=${encodeURIComponent(query)}&provider=${options.providerId ?? this.snapshot.defaultProviderId}`};
  },
};
const opened = [];
const browser = {privateMode: true};
const tab = {id: 7, browser};
const bridge = {
  async update(extension, tabId, data) { opened.push({extension, tabId, ...data}); },
  async create(extension, data) { opened.push({extension, ...data}); },
};
const context = {
  incognito: false, privateBrowsingAllowed: true, unloaded: false,
  extension: {id: "search@test", hasShutdown: false, hasPermission: () => true, tabManager: {canAccessTab: () => true}},
  checkLoadURL: () => true,
};
const scope = vm.createContext({
  ChromeUtils: {importESModule(url) {
    if (url.endsWith("NavisSearchService.sys.mjs")) return {NavisSearchService: service};
    if (url.endsWith("NavisAndroidWebExtensionHost.sys.mjs")) return {NavisAndroidExtensionTabBridge: bridge};
    if (url.endsWith("PrivateBrowsingUtils.sys.mjs")) return {PrivateBrowsingUtils: {isBrowserPrivate: value => value.privateMode}};
    throw new Error(`Unexpected import ${url}`);
  }},
  ExtensionUtils: {ExtensionError: Error}, ExtensionAPI: class {},
  tabTracker: {activeTab: tab, getTab: id => id === tab.id ? tab : null},
});
vm.runInContext(source, scope, {filename: file});
const api = new scope.search().getAPI(context).search;
assert.equal((await api.get())[0].name, "Google");
const custom = {id: "custom", name: "My Search"};
service.snapshot = {providers: [custom, {id: "google", name: "Renamed Google"}], defaultProviderId: "custom"};
const catalog = await api.get();
assert.deepEqual(Array.from(catalog, provider => provider.alias), ["custom", "google"]);
assert.equal(catalog[0].isDefault, true);
assert.equal(catalog[1].name, "Renamed Google");
await api.search({query: "中文 terms", engine: "My Search", tabId: tab.id});
assert.equal(calls.at(-1).options.providerId, "custom");
assert.equal(calls.at(-1).options.privateMode, true);
assert.equal(opened.at(-1).tabId, tab.id);
await api.query({text: "default terms"});
assert.ok(opened.at(-1).url.endsWith("provider=custom"));
await assert.rejects(api.search({query: "terms", engine: "removed"}), /Unknown Navis search engine/);
service.snapshot = {providers: [{id: "google", name: "Renamed Google"}], defaultProviderId: "google"};
assert.equal((await api.get()).length, 1);
await assert.rejects(api.search({query: "terms", engine: "My Search"}), /Unknown Navis search engine/);
context.unloaded = true;
assert.throws(() => api.get(), /not allowed/);
assert.equal(opened.length, 2);
console.log("Android extension search: live custom/edited/removed catalog, named/default Core submission and private target passed");
