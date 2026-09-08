import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const root = new URL("../", import.meta.url);
const read = path => fs.readFileSync(new URL(path, root), "utf8");
const profileSource = read("gecko/mobile/shared/modules/navis/NavisAndroidExtensionProfile.sys.mjs");
export function fixture(source = profileSource) {
  const calls = [], listeners = new Set();
  const transport = {
    respond: async () => "[]",
    sendRequestForResult(event, args) { calls.push({ event, ...args }); return this.respond(args); },
    registerListener(listener) { listeners.add(listener); },
    unregisterListener(listener) { listeners.delete(listener); },
  };
  const realm = { ChromeUtils: { importESModule: uri => uri.includes("Messaging") ?
    { EventDispatcher: { byName: () => transport } } : { ExtensionError: Error } },
    ExtensionUtils: { ExtensionError: Error },
    ExtensionAPIPersistent: class {}, ExtensionAPI: class {}, EventManager: class { api() { return {}; } },
    Services: { io: { newURI: url => ({ host: new URL(url).hostname, spec: new URL(url).href,
      schemeIs: scheme => new URL(url).protocol === `${scheme}:` }) },
      eTLD: { getBaseDomain: uri => uri.host.split(".").slice(-2).join(".") } },
  };
  vm.createContext(realm);
  vm.runInContext(source.replace("export const NavisAndroidExtensionProfile", "this.profile"), realm);
  const permissions = new Set(["bookmarks", "topSites"]);
  const extension = { id: "sample@test", hasPermission: name => permissions.has(name), privateBrowsingAllowed: false };
  const loadAPI = (name, file) => {
    realm.ChromeUtils.importESModule = () => ({ NavisAndroidExtensionProfile: realm.profile });
    vm.runInContext(read(`gecko/mobile/shared/components/extensions/${file}`), realm);
    const api = new realm[name](); api.extension = extension;
    return api.getAPI({ extension });
  };
  return { profile: realm.profile, calls, transport, listeners, extension, permissions, loadAPI };
}
const node = (id, parentId = "root", index = 0, type = "bookmark") => ({
  id, parentId, index, type, title: id, dateAdded: index,
  ...(type === "bookmark" ? { url: `https://${id}.example/` } : {}),
});
const plain = value => JSON.parse(JSON.stringify(value));

test("permission, unsupported operation and unauthorized private context reject before transport", async () => {
  const f = fixture();
  f.permissions.delete("bookmarks");
  await assert.rejects(f.profile.request(f.extension, {}, "bookmarks:list"));
  await assert.rejects(f.profile.request(f.extension, {}, "history:getVisits"));
  await assert.rejects(f.profile.request(f.extension, { incognito: true }, "topSites:list"));
  assert.equal(f.calls.length, 0);
});
test("revocation, context close, uninstall during pending query cannot deliver data", async () => {
  for (const invalidate of [f => f.permissions.clear(), f => { f.extension.hasShutdown = true; },
    (_f, context) => { context.unloaded = true; }]) {
    const f = fixture(), context = {};
    let reply; f.transport.respond = () => new Promise(resolve => { reply = resolve; });
    const pending = f.profile.request(f.extension, context, "bookmarks:list");
    invalidate(f, context); reply('[{"id":"secret"}]');
    await assert.rejects(pending, /not allowed/);
  }
});
test("authorized private requests carry explicit identity, never an active Session ID", async () => {
  const f = fixture(); f.extension.privateBrowsingAllowed = true;
  await f.profile.request(f.extension, { incognito: true }, "topSites:list");
  assert.equal(f.calls[0].extensionId, "sample@test");
  assert.equal(f.calls[0].privateMode, true);
  assert.equal(f.calls[0].privateBrowsingAllowed, true);
  assert.equal(Object.hasOwn(f.calls[0], "sessionId"), false);
});
test("tree, search, ordered children and CRUD route to the same profile", async () => {
  const f = fixture(); const entries = [node("folder", "root", 1, "folder"), node("child", "folder"), node("first")];
  f.transport.respond = async args => args.operation === "bookmarks:list" ? JSON.stringify(entries) : "null";
  const api = f.loadAPI("bookmarks", "ext-bookmarks.js").bookmarks;
  assert.deepEqual(plain((await api.getTree())[0].children.map(n => n.id)), ["first", "folder"]);
  assert.equal((await api.getSubTree("folder"))[0].children[0].id, "child");
  assert.equal((await api.search("child"))[0].id, "child");
  await assert.rejects(api.get("missing"));
  await api.create({ title: "a", url: "https://a.test/" });
  await api.update("child", { title: "new" });
  await api.move("child", { parentId: "root", index: 1 });
  await api.removeTree("folder");
  assert.deepEqual(f.calls.slice(-4).map(c => c.operation), ["bookmarks:create", "bookmarks:update", "bookmarks:move", "bookmarks:removeTree"]);
  f.transport.respond = async () => { throw Error("disk failure"); };
  await assert.rejects(api.create({ title: "failed" }), /operation failed/);
});
test("committed delta emits truthful create/change/move/remove subtree without phantom sibling moves", () => {
  const f = fixture(); const before = [node("a"), node("b", "root", 1)];
  const after = [node("b"), { ...node("a", "root", 1), title: "edited" }, node("c", "root", 2)];
  assert.deepEqual(plain(f.profile.changes({ before, after, movedId: "a" }).map(row => row.slice(0, 2))),
    [["onChanged", "a"], ["onMoved", "a"], ["onCreated", "c"]]);
  const tree = [node("folder", "root", 0, "folder"), node("child", "folder")];
  const removed = f.profile.changes({ before: tree, after: [], movedId: null });
  assert.equal(removed.length, 1); assert.equal(removed[0][2].node.children[0].id, "child");
});
test("persistent event converts listener and suppresses revoked/uninstalled consumers", () => {
  const f = fixture(), seen = [];
  const registration = f.profile.register(f.extension, "onCreated", { async: (...args) => seen.push(args) });
  const event = { json: JSON.stringify({ before: [], after: [node("a")] }) };
  const emit = () => { for (const listener of f.listeners) listener.onEvent("changed", event); };
  emit(); assert.equal(seen.length, 1);
  registration.convert({ async: () => seen.push("converted") }); emit(); assert.equal(seen[1], "converted");
  f.permissions.clear(); emit(); assert.equal(seen.length, 2);
  registration.unregister(); assert.equal(f.listeners.size, 0);
});
test("topSites deduplicates registrable domains, honours limit and onePerDomain false", async () => {
  const f = fixture(); f.transport.respond = async () => JSON.stringify([
    { url: "https://a.example.com/", title: "first" }, { url: "https://b.example.com/", title: "second" },
    { url: "https://other.test/", title: "third" }]);
  const api = f.loadAPI("topSites", "ext-topSites.js").topSites;
  assert.equal((await api.get()).length, 2);
  assert.equal((await api.get({ onePerDomain: false })).length, 3);
  assert.equal((await api.get({ limit: 1 }))[0].title, "first");
});
test("removing the post-await guard would leak a revoked result (mutation killed)", async () => {
  const mutated = profileSource.replace(/checkAccess\(extension, permission, context\);\n    return JSON.parse/, "return JSON.parse");
  const f = fixture(mutated); let reply;
  f.transport.respond = () => new Promise(resolve => { reply = resolve; });
  const pending = f.profile.request(f.extension, {}, "bookmarks:list");
  f.permissions.clear(); reply("[]");
  assert.deepEqual(plain(await pending), []); // The production rejection test above would fail.
});
