// Real Core method under boundary stubs: never launches an application/file.
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../embedder/modules/DesktopEngine.sys.mjs", import.meta.url), "utf8");
const method = source.slice(source.indexOf("  async openDownload("), source.indexOf("  async cancelDownload("));
assert.ok(method.startsWith("  async openDownload("));
const projection = source.slice(source.indexOf("function projectDownload("), source.indexOf("function userContextIdFrom("));
const { Runtime, projectDownload } = vm.runInNewContext(`
  ${projection}
  class Runtime {
    #closed = false;
    #windows = new Map();
    #downloadsById = new Map();
    #openingDownloads = new Set();
    register(window) { this.#windows.set(window, {}); }
    set(id, download) { this.#downloadsById.set(id, {download, state: projectDownload(id, download)}); }
    remove(id) { this.#downloadsById.delete(id); }
    close() { this.#closed = true; }
    ${method}
  }
  ({Runtime, projectDownload});
`, { isPrivateWindow: window => window.private, targetFileName: () => "fixture.txt" });

function fixture(privateMode = false) {
  const runtime = new Runtime();
  const window = { private: privateMode, closed: false,
    document: { visibilityState: "visible", hasFocus: () => true } };
  runtime.register(window);
  const launches = [];
  const download = { succeeded: true, stopped: true, hasBlockedData: false,
    target: { exists: true }, source: { isPrivate: privateMode },
    refresh: async () => {}, launch: async options => launches.push(structuredClone(options)) };
  runtime.set("download-1", download);
  return { runtime, window, download, launches, open: () => runtime.openDownload("download-1", { window }) };
}

for (const privateMode of [false, true]) {
  const f = fixture(privateMode);
  assert.equal(await f.open(), true);
  assert.equal(await f.open(), true, "Opening is repeatable, not permanently disabled");
  assert.deepEqual(f.launches, [{ useSystemDefault: true }, { useSystemDefault: true }]);
  assert.equal(projectDownload("x", f.download).canOpen, true);
  f.download.target.exists = false;
  assert.equal(projectDownload("x", f.download).canOpen, false);
}
for (const block of [
  f => { f.download.succeeded = false; },
  f => { f.window.private = !f.window.private; },
  f => { f.window.document.visibilityState = "hidden"; },
  f => { f.window.document.hasFocus = () => false; },
  f => { f.window.closed = true; },
  f => f.runtime.close(),
  f => f.runtime.remove("download-1"),
]) {
  const f = fixture(); block(f);
  assert.equal(await f.open(), false);
  assert.equal(f.launches.length, 0);
}
{
  const f = fixture();
  assert.equal(await f.runtime.openDownload("download-1"), false);
  assert.equal(await f.runtime.openDownload("download-1", { window: { ...f.window } }), false);
  assert.equal(f.launches.length, 0);
}
for (const race of [
  f => { f.download.target.exists = false; },
  f => { f.download.succeeded = false; },
  f => { f.window.closed = true; },
  f => { f.window.document.hasFocus = () => false; },
  f => f.runtime.remove("download-1"),
  f => f.runtime.set("download-1", { ...f.download }),
]) {
  const f = fixture();
  f.download.refresh = async () => race(f);
  assert.equal(await f.open(), false, "Check ownership and existence after asynchronous filesystem refresh");
  assert.equal(f.launches.length, 0);
}
{
  const f = fixture();
  // Gecko refresh replaces the projected entry through onDownloadChanged.
  f.download.refresh = async () => f.runtime.set("download-1", f.download);
  assert.equal(await f.open(), true, "A new projection of the same Download remains eligible");
  let release;
  f.download.launch = () => new Promise(resolve => { release = resolve; });
  const first = f.open();
  await Promise.resolve();
  assert.equal(await f.open(), false, "A pending launch cannot be duplicated");
  release();
  assert.equal(await first, true);
  f.download.launch = async () => { throw new Error("No application available"); };
  await assert.rejects(f.open(), /No application available/);
  f.download.launch = async () => {};
  assert.equal(await f.open(), true, "Failure releases the in-flight lock");
}
console.log("PASS desktop download open: owner/privacy/focus, disk refresh races, default-system Gecko delegation, repeat/duplicate/failure handling; no real files opened");
