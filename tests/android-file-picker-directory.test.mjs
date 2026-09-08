/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const moduleUrl = new URL("../gecko/mobile/shared/components/geckoview/FilePickerDelegate.sys.mjs", import.meta.url);
const source = (await readFile(moduleUrl, "utf8"))
  .replace(/^import .+;\n/gmu, "")
  .replace("export class FilePickerDelegate", "class FilePickerDelegate");
const root = "/private/cache/gecko_temp/navis-upload-staging";
const owner = "11111111-2222-3333-4444-555555555555";
const selected = `${root}/${owner}/所选文件夹`;

function fixture(entries = {}) {
  const nodes = new Map(Object.entries({ [root]: { type: "directory" },
    [`${root}/${owner}`]: { type: "directory" }, [selected]: { type: "directory" }, ...entries }));
  class NativeFile {
    constructor(value) { this.path = value; }
    append(name) { this.path += `/${name}`; }
    normalize() { this.path = path.posix.normalize(this.path); }
    contains(other) { return other.path.startsWith(`${this.path}/`); }
    exists() { return nodes.has(this.path); }
    isDirectory() { return nodes.get(this.path)?.type === "directory"; }
    isSymlink() { return nodes.get(this.path)?.type === "symlink"; }
    equals(other) { return this.path === other.path; }
    clone() { return new NativeFile(this.path); }
    QueryInterface() { return this; }
    get parent() { return this.path === "/" ? null : new NativeFile(path.posix.dirname(this.path)); }
    get leafName() { return path.posix.basename(this.path); }
  }
  const created = [];
  const sandbox = {
    AppConstants: { MOZ_NAVIS_CORE: true, platform: "android" },
    GeckoViewUtils: { initLogging: () => ({ debug() {}, warn() {} }) },
    ChromeUtils: {
      defineESModuleGetters(lazy) { Object.assign(lazy, { FileUtils: { File: NativeFile,
        getDir: () => new NativeFile("/private/cache/gecko_temp") } }); },
      generateQI() { return () => {}; },
    },
    Components: { ID: value => value },
    Services: { io: {
      newURI: uri => ({ QueryInterface: () => ({ file: new NativeFile(decodeURI(uri.replace(/^file:\/\//u, ""))) }) }),
      newFileURI: file => ({ spec: `file://${file.path}` }),
    } },
    Ci: { nsIFilePicker: { modeGetFolder: 2 } }, Cc: {},
    IOUtils: {
      async remove(parent) { for (const key of nodes.keys()) if (key === parent || key.startsWith(`${parent}/`)) nodes.delete(key); },
      async getChildren(parent) { return [...nodes.keys()].filter(key => path.posix.dirname(key) === parent); },
      async stat(file) { return { size: 1, lastModified: 123, ...nodes.get(file) }; },
    },
    File: { async createFromFileName(filePath, options) {
      const file = { filePath, ...options, setMozRelativePath(value) { this.relativePath = value; } };
      created.push(file);
      return file;
    } },
  };
  vm.runInNewContext(`${source}\nglobalThis.Picker = FilePickerDelegate;`, sandbox, { filename: moduleUrl.pathname });
  const picker = new sandbox.Picker();
  picker._prompt = {};
  picker._navisDirectRuntime = true;
  picker._mode = 2;
  return { picker, created, nodes, NativeFile, resolve: () => picker._resolveMaterializedDirectory(`file://${selected}`) };
}

test("folder uses file-backed Gecko DOM files with exact root-relative names", async () => {
  const f = fixture({ [`${selected}/文 件.txt`]: { type: "regular" },
    [`${selected}/nested`]: { type: "directory" }, [`${selected}/nested/report.csv`]: { type: "regular" } });
  await f.resolve();
  assert.deepEqual(f.created.map(file => file.relativePath), ["所选文件夹/文 件.txt", "所选文件夹/nested/report.csv"]);
  assert.equal(f.created[0].filePath, `${selected}/文 件.txt`);
  assert.equal(f.created[0].lastModified, 123);
});

test("empty selected folder is a valid empty file list", async () => {
  const f = fixture();
  await f.resolve();
  assert.equal(f.created.length, 0);
  assert.equal([...f.picker.domFilesInWebKitDirectory].length, 0);
});

test("arbitrary file URI and symbolic entries are not accepted as picker authority", async () => {
  const f = fixture({ "/private/secret": { type: "regular" }, [`${selected}/link`]: { type: "symlink" } });
  assert.throws(() => f.picker._navisMaterializedFile("file:///private/secret"), /outside staging/u);
  assert.throws(() => f.picker._navisMaterializedFile(`file://${root}/../secret`), /outside staging/u);
  await assert.rejects(f.resolve(), /Symbolic upload path/u);
  assert.equal(f.created.length, 0);
});

test("folder count and file size limits fail before creating any DOM File", async () => {
  const many = fixture(Object.fromEntries(Array.from({ length: 33 }, (_, i) => [`${selected}/${i}`, { type: "regular" }])));
  await assert.rejects(many.resolve(), /storage limit/u);
  assert.equal(many.created.length, 0);
  const large = fixture({ [`${selected}/large`]: { type: "regular", size: 128 * 1024 * 1024 + 1 } });
  await assert.rejects(large.resolve(), /storage limit/u);
  assert.equal(large.created.length, 0);
});

test("DOM file enumerator yields only DOM entries, never duplicate nsIFile entries", () => {
  const f = fixture();
  const domFile = { name: "actual.txt" };
  f.picker._fileData = [{ file: new f.NativeFile(`${selected}/actual.txt`), domFileOrDir: domFile }];
  assert.deepEqual([...f.picker.domFileOrDirectoryEnumerator], [domFile]);
  assert.equal([...f.picker.files].length, 1);
  assert.notEqual([...f.picker.files][0], f.picker._fileData[0].file);
});

test("failed handoff removes only its own staging bucket", async () => {
  const other = `${root}/66666666-2222-3333-4444-555555555555/other.txt`;
  const f = fixture({ [`${selected}/readme`]: { type: "regular" }, [other]: { type: "regular" } });
  await f.resolve();
  await f.picker._discardMaterializedSelections();
  assert.equal(f.nodes.has(selected), false);
  assert.equal(f.nodes.has(other), true);
  assert.equal([...f.picker.domFilesInWebKitDirectory].length, 0);
});
