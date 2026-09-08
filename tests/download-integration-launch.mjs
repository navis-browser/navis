// Execute the upstream launch algorithm at its OS boundary; never open a file/app.
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../gecko/toolkit/components/downloads/DownloadIntegration.sys.mjs", import.meta.url), "utf8");
const method = source.slice(source.indexOf("  async launchDownload("), source.indexOf("  /**\n   * Asks for confirmation for launching"));
assert.ok(method.startsWith("  async launchDownload("));

function fixture({ platform = "linux", executable = false, policies, consent = true,
  path = "/owned/fixture.txt", mimeAvailable = true, mimeFailure = false, directFailure = false } = {}) {
  const calls = [];
  const mimeInfo = { type: "text/plain", preferredAction: 4, useSystemDefault: 2 };
  const file = { path, leafName: path.split("/").at(-1), isExecutable: () => executable };
  const download = { target: { path }, source: { url: "https://example.invalid/fixture", isPrivate: true },
    contentType: "text/plain", launchWhenSucceeded: true, handleInternally: true };
  const integration = vm.runInNewContext(`({ ${method} })`, {
    AppConstants: { platform }, Services: { policies, scriptSecurityManager: { getSystemPrincipal: () => "system" } },
    Ci: { nsIMIMEInfo: { useSystemDefault: 2 } },
    lazy: {
      FileUtils: { File: function () { return file; } },
      gMIMEService: { getFromTypeAndExtension: () => {
        if (!mimeAvailable) throw new Error("unknown MIME");
        return mimeInfo;
      } },
      DownloadUIHelper: { loadFileIn: () => { throw new Error("Must not enter Firefox's browser UI"); } },
      NetUtil: { newURI: () => "file:///owned/fixture.txt" },
      gExternalProtocolService: { loadURI: (...args) => calls.push(["external", ...args]) },
    },
  });
  integration.confirmLaunchExecutable = async () => { calls.push(["prompt"]); return consent; };
  integration.launchFile = (_file, mime) => {
    calls.push([mime ? "mime" : "file"]);
    if (mime ? mimeFailure : directFailure) throw new Error("no handler");
  };
  integration.showContainingDirectory = path => calls.push(["directory", path]);
  return { calls, mimeInfo, download, launch: () => integration.launchDownload(download, { useSystemDefault: true }) };
}

for (const platform of ["linux", "win"]) {
  for (const path of ["/owned/fixture.txt", "/owned/fixture.exe"]) {
    const f = fixture({ platform, path });
    await f.launch();
    assert.deepEqual(f.calls, [["mime"]], "Missing enterprise policies must not break any file type");
    assert.equal(f.mimeInfo.preferredAction, 2);
    assert.equal(f.download.launchWhenSucceeded, false);
  }
}
for (const consent of [false, true]) {
  const f = fixture({ executable: true, path: "/owned/fixture.sh", consent });
  await f.launch();
  assert.deepEqual(f.calls, consent ? [["prompt"], ["mime"]] : [["prompt"]], "Absent policies grant no executable exemption");
}
{
  const f = fixture({ platform: "win", executable: true, path: "/owned/fixture.EXE" });
  await f.launch();
  assert.deepEqual(f.calls, [["mime"]], "Preserve upstream Windows EXE security-zone prompt ownership");
}
for (const exempt of [false, true]) {
  const policies = { isExemptExecutableExtension(url, ext) {
    assert.equal(url, "https://example.invalid/fixture"); assert.equal(ext, "sh"); return exempt;
  } };
  const f = fixture({ policies, executable: true, path: "/owned/fixture.sh" });
  await f.launch();
  assert.deepEqual(f.calls, exempt ? [["mime"]] : [["prompt"], ["mime"]]);
}
{
  const f = fixture({ platform: "win", path: "/owned/fixture" });
  await f.launch();
  assert.deepEqual(f.calls, [["directory", "/owned/fixture"]], "Preserve extensionless Windows launch protection");
}
for (const mimeAvailable of [false, true]) {
  const f = fixture({ mimeAvailable, mimeFailure: true });
  await f.launch();
  assert.deepEqual(f.calls, mimeAvailable ? [["mime"], ["file"]] : [["file"]]);
}
{
  const f = fixture({ mimeFailure: true, directFailure: true });
  await f.launch();
  assert.deepEqual(f.calls, [["mime"], ["file"], ["external", "file:///owned/fixture.txt", "system"]]);
}
console.log("PASS real DownloadIntegration launch algorithm: absent/present policies, consent, Windows guards, system handlers and fallback; no files opened");
