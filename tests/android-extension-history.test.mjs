import test from "node:test";
import assert from "node:assert/strict";
import { fixture } from "./android-extension-profile.test.mjs";
const plain = value => JSON.parse(JSON.stringify(value));
function historyFixture() {
  const f = fixture();
  f.permissions.add("history");
  const api = f.loadAPI("history", "ext-history.js").history;
  return { ...f, api };
}

test("history requires its own permission and authorized private access", async () => {
  const f = historyFixture();
  f.permissions.delete("history");
  await assert.rejects(f.api.search({ text: "" }), /not allowed/);
  assert.equal(f.calls.length, 0);
  f.permissions.add("history");
  await assert.rejects(f.profile.request(f.extension, { incognito: true }, "history:search"), /not allowed/);
  assert.equal(f.calls.length, 0);
});

test("search converts schema dates, bounds results and preserves literal search", async () => {
  const f = historyFixture();
  f.transport.respond = async () => '[{"id":"https://example.com/","visitCount":3}]';
  const before = Date.now();
  assert.equal((await f.api.search({ text: "" }))[0].visitCount, 3);
  let args = JSON.parse(f.calls.at(-1).arguments);
  assert.ok(args.startTime >= before - 86400000 && args.startTime <= Date.now() - 86400000);
  assert.equal(args.endTime, Number.MAX_SAFE_INTEGER); assert.equal(args.maxResults, 100);
  await f.api.search({ text: "100%_literal", startTime: "2026-01-01T00:00:00Z", endTime: "2026-01-02T00:00:00Z", maxResults: 999 });
  args = JSON.parse(f.calls.at(-1).arguments);
  assert.equal(args.startTime, Date.parse("2026-01-01T00:00:00Z"));
  assert.equal(args.maxResults, 500); assert.equal(args.text, "100%_literal");
});

test("getVisits returns actual native IDs and no synthetic legacy rows", async () => {
  const f = historyFixture();
  assert.deepEqual(plain(await f.api.getVisits({ url: "https://OLD.test" })), []);
  assert.equal(JSON.parse(f.calls[0].arguments).url, "https://old.test/");
  f.transport.respond = async () => JSON.stringify([{ id: "https://a.test/", visitId: "17", visitTime: 100, transition: "typed", referringVisitId: "0" }]);
  assert.equal((await f.api.getVisits({ url: "https://a.test/" }))[0].visitId, "17");
  assert.equal(f.calls.at(-1).operation, "history:visits");
});

test("add/delete operations route canonical URLs, real transition/time and exact range", async () => {
  const f = historyFixture(); f.transport.respond = async () => "null";
  assert.equal(await f.api.addUrl({ url: "https://EXAMPLE.com", title: "Title", transition: "typed", visitTime: 1234 }), undefined);
  assert.deepEqual(JSON.parse(f.calls[0].arguments), { url: "https://example.com/", title: "Title", transition: "typed", visitTime: 1234 });
  await f.api.deleteUrl({ url: "https://example.com/" });
  await f.api.deleteRange({ startTime: 10, endTime: 20 });
  assert.deepEqual(JSON.parse(f.calls.at(-1).arguments), { startTime: 10, endTime: 20 });
  await f.api.deleteAll();
  assert.deepEqual(f.calls.map(call => call.operation), ["history:add", "history:deleteUrl", "history:deleteRange", "history:deleteAll"]);
  await assert.rejects(f.api.addUrl({ url: "javascript:alert(1)" }), /HTTP/);
  await assert.rejects(f.api.deleteRange({ startTime: 20, endTime: 10 }), /range/);
  await assert.rejects(f.api.addUrl({ url: "https://example.com/", visitTime: "nonsense" }), /timestamp/);
  assert.equal(f.calls.length, 4);
  f.transport.respond = async () => { throw Error("disk full"); };
  await assert.rejects(f.api.deleteAll(), /failed/);
});

test("history events preserve allHistory and URLs, convert fire, and stop after revoke/uninstall", () => {
  const f = historyFixture(), seen = [];
  const emit = detail => { for (const listener of f.listeners) listener.onEvent("history", { json: JSON.stringify({ event: "onVisitRemoved", detail }) }); };
  const registration = f.profile.registerHistory(f.extension, "onVisitRemoved", { async: detail => seen.push(plain(detail)) });
  emit({ allHistory: false, urls: ["https://a/"] });
  assert.deepEqual(seen[0], { allHistory: false, urls: ["https://a/"] });
  registration.convert({ async: detail => seen.push(["new", plain(detail)]) });
  emit({ allHistory: true, urls: [] });
  assert.deepEqual(seen[1], ["new", { allHistory: true, urls: [] }]);
  f.permissions.delete("history"); emit({ allHistory: true, urls: [] });
  f.permissions.add("history"); f.extension.hasShutdown = true; emit({ allHistory: true, urls: [] });
  assert.equal(seen.length, 2);
  registration.unregister(); assert.equal(f.listeners.size, 0);
});

test("pending history query cannot deliver after permission revocation", async () => {
  const f = historyFixture(); let reply;
  f.transport.respond = () => new Promise(resolve => { reply = resolve; });
  const query = f.api.getVisits({ url: "https://a/" });
  f.permissions.delete("history"); reply('[{"visitId":"secret"}]');
  await assert.rejects(query, /not allowed/);
});
