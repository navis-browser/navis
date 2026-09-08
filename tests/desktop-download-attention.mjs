import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("../embedder/modules/DesktopDownloadPreferences.sys.mjs", import.meta.url), "utf8");
const definition = source.slice(source.indexOf("export class DesktopDownloadAttention {"),
  source.indexOf("export function getDesktopDownloadLifecycle()"));
const { Attention } = vm.runInNewContext(definition.replace("export class", "class") +
  "\n({ Attention: DesktopDownloadAttention });");
const copy = value => JSON.parse(JSON.stringify(value));
const keyFor = item => createHash("sha256").update(JSON.stringify([
  item.source.url, item.target.path, item.startTime.getTime(),
])).digest("hex");
const download = (name, privateMode = false, succeeded = false) => ({
  source: { url: "https://example.test/" + name, isPrivate: privateMode },
  target: { path: "/downloads/" + name }, startTime: new Date(1700000000000), succeeded,
});
function storage(initial = []) {
  let saved = copy(initial), issued = 0, keyCalls = 0;
  const writes = [];
  return {
    create: () => new Attention({
      read: () => copy(saved),
      write: entries => { saved = copy(entries); writes.push(copy(entries)); },
      keyFor: item => { keyCalls++; return keyFor(item); },
      token: () => "completion-" + ++issued,
    }),
    writes, get saved() { return copy(saved); }, get issued() { return issued; },
    get keyCalls() { return keyCalls; },
  };
}

// Restored completed history is not a new completion; restored pending work is.
{
  const disk = storage(), attention = disk.create();
  const old = download("old.txt", false, true);
  attention.observe(old, { existing: true });
  attention.observe(old);
  assert.equal(attention.tokenFor(old), null);
  assert.equal(disk.issued, 0);
  assert.equal(disk.writes.length, 0);
  const pending = download("pending.txt");
  attention.observe(pending, { existing: true });
  assert.equal(attention.tokenFor(pending), null);
  pending.succeeded = true;
  attention.observe(pending);
  const token = attention.tokenFor(pending);
  assert.equal(token, "completion-1");
  assert.deepEqual(disk.saved, [[keyFor(pending), token]]);
  assert.match(disk.saved[0][0], /^[0-9a-f]{64}$/);
  assert.equal(JSON.stringify(disk.saved).includes("pending.txt"), false);
}

// The two window views receive the same Downloads.ALL object and must coalesce.
{
  const disk = storage(), attention = disk.create(), item = download("windows.txt");
  attention.observe(item);
  attention.observe(item, { existing: true });
  item.succeeded = true;
  attention.observe(item);
  const token = attention.tokenFor(item);
  attention.observe(item);
  attention.observe(item, { existing: true });
  assert.equal(attention.tokenFor(item), token);
  assert.equal(disk.issued, 1);
  assert.equal(disk.writes.length, 1);
  assert.equal(attention.acknowledge(item, "wrong-token"), false);
  assert.equal(disk.writes.length, 1);
  assert.equal(attention.acknowledge(item, token), true);
  assert.equal(attention.tokenFor(item), null);
  assert.deepEqual(disk.saved, []);
  assert.equal(attention.acknowledge(item, token), false);
  attention.observe(item);
  assert.equal(disk.issued, 1);
  assert.equal(disk.writes.length, 2);
}

// A late acknowledgement from the first result cannot clear a retry result.
{
  const disk = storage(), attention = disk.create(), item = download("retry.txt");
  attention.observe(item);
  item.succeeded = true; attention.observe(item);
  const oldToken = attention.tokenFor(item), oldKey = keyFor(item);
  item.succeeded = false; attention.observe(item);
  assert.equal(attention.tokenFor(item), null);
  assert.deepEqual(disk.saved, []);
  assert.equal(attention.acknowledge(item, oldToken), false);
  item.startTime = new Date(item.startTime.getTime() + 1000);
  item.succeeded = true; attention.observe(item);
  const newToken = attention.tokenFor(item);
  assert.notEqual(newToken, oldToken);
  assert.notEqual(keyFor(item), oldKey);
  assert.deepEqual(disk.saved, [[keyFor(item), newToken]]);
  const writes = disk.writes.length;
  assert.equal(attention.acknowledge(item, oldToken), false);
  assert.equal(attention.tokenFor(item), newToken);
  assert.equal(disk.writes.length, writes);
  assert.equal(attention.acknowledge(item, newToken), true);
  assert.deepEqual(disk.saved, []);
}

// Only unseen normal completions survive process restarts, with the same token.
{
  const disk = storage(), item = download("restart.txt");
  let attention = disk.create();
  attention.observe(item); item.succeeded = true; attention.observe(item);
  const token = attention.tokenFor(item);
  const restored = download("restart.txt", false, true);
  attention = disk.create();
  attention.observe(restored, { existing: true });
  assert.equal(attention.tokenFor(restored), token);
  assert.equal(disk.issued, 1);
  assert.equal(disk.writes.length, 1);
  assert.equal(attention.acknowledge(restored, token), true);
  attention = disk.create();
  const seen = download("restart.txt", false, true);
  attention.observe(seen, { existing: true }); attention.observe(seen);
  assert.equal(attention.tokenFor(seen), null);
  assert.equal(disk.issued, 1);
  assert.equal(disk.writes.length, 2);
}

// Private work never hashes its identity or writes to normal persisted storage.
{
  const normal = download("unread-normal.txt", false, true);
  const disk = storage([[keyFor(normal), "normal-token"]]);
  const attention = disk.create(), item = download("private-secret.txt", true);
  attention.observe(item); item.succeeded = true; attention.observe(item);
  const first = attention.tokenFor(item);
  assert.ok(first);
  attention.observe(item, { existing: true });
  assert.equal(attention.tokenFor(item), first);
  assert.equal(attention.acknowledge(item, first), true);
  item.succeeded = false; attention.observe(item);
  item.succeeded = true; attention.observe(item);
  assert.notEqual(attention.tokenFor(item), first);
  attention.forget(item);
  assert.equal(attention.tokenFor(item), null);
  assert.deepEqual(disk.saved, [[keyFor(normal), "normal-token"]]);
  assert.equal(disk.writes.length, 0);
  assert.equal(disk.keyCalls, 0);
  const restored = download("private-secret.txt", true, true);
  const fresh = disk.create(); fresh.observe(restored, { existing: true });
  assert.equal(fresh.tokenFor(restored), null);
}

// Removing one record clears only its unread state, including after restarting.
{
  const disk = storage(), attention = disk.create();
  const first = download("remove.txt"), second = download("retain.txt");
  for (const item of [first, second]) {
    attention.observe(item); item.succeeded = true; attention.observe(item);
  }
  const firstToken = attention.tokenFor(first), secondToken = attention.tokenFor(second);
  attention.forget(first);
  assert.equal(attention.tokenFor(first), null);
  assert.equal(attention.acknowledge(first, firstToken), false);
  assert.deepEqual(disk.saved, [[keyFor(second), secondToken]]);
  const writes = disk.writes.length;
  attention.forget(first); attention.forget(download("unknown.txt"));
  assert.equal(disk.writes.length, writes);
  const restarted = disk.create();
  const old = download("remove.txt", false, true);
  restarted.observe(old, { existing: true });
  assert.equal(restarted.tokenFor(old), null);
  const retained = download("retain.txt", false, true);
  restarted.observe(retained, { existing: true });
  assert.equal(restarted.tokenFor(retained), secondToken);
}

// Malformed pref entries must not become completion tokens.
{
  const item = download("valid.txt", false, true);
  const disk = storage([null, [], ["/raw/private/path", "token"], ["b".repeat(64), ""],
    ["c".repeat(64), "x".repeat(65)], [keyFor(item), "valid-token"]]);
  const attention = disk.create(); attention.observe(item, { existing: true });
  assert.equal(attention.tokenFor(item), "valid-token");
  attention.acknowledge(item, "valid-token");
  assert.deepEqual(disk.saved, []);
  const invalid = storage({ unexpected: "object" });
  invalid.create().observe(download("old.txt", false, true), { existing: true });
  assert.equal(invalid.writes.length, 0);
}

console.log("Desktop download attention: seven real-class lifecycle groups passed");
