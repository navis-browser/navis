import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createNavisAndroidDevTools } from "../gecko/mobile/shared/modules/navis/NavisAndroidDevTools.sys.mjs";

function fixture({ targetPrivate = false, hostPrivate = false, targetClosed = false,
  createHost = createNavisAndroidDevTools } = {}) {
  const calls = [];
  const selections = [];
  const targetWindow = {
    tab: { id: 7, isPopup: false },
    browser: { browsingContext: {}, browserId: 103, privateMode: targetPrivate },
    closed: targetClosed,
  };
  targetWindow.browser.documentGlobal = targetWindow;
  targetWindow.browser.fullZoom = 1;
  targetWindow.navisResponsiveTarget = {
    tab: targetWindow.tab,
    async restore() {},
    async toggle() { return { responsive: true, zoom: 1 }; },
    zoom() { return { zoom: 1 }; },
  };
  const frame = {
    style: {},
    setAttribute() {},
    addEventListener(type, listener) { this.loaded = listener; },
    loadURI(uri) { queueMicrotask(() => this.loaded({ target: { documentURI: uri } })); },
    remove() { calls.push("frame:remove"); },
  };
  const window = {
    privateMode: hostPrivate,
    setTimeout,
    clearTimeout,
    document: {
      createXULElement() { return frame; },
      documentElement: { appendChild() {} },
    },
  };
  globalThis.Services = {
    wm: { getEnumerator: () => [targetWindow] },
    io: { newURI: value => value },
    scriptSecurityManager: { getSystemPrincipal: () => ({}) },
  };
  globalThis.Cu = { reportError(error) { throw error; } };
  const toolbox = { async destroy() { calls.push("toolbox:destroy"); } };
  const commands = { async destroy() { calls.push("commands:destroy"); } };
  const modules = {
    "devtools/server/devtools-server": { DevToolsServer: {
      init() {}, registerAllActors() {}, setRootActor() {},
      connectPipe() { calls.push("connectPipe"); return {}; },
    } },
    "devtools/client/devtools-client": { DevToolsClient: class {
      async connect() { calls.push("client:connect"); }
      async close() { calls.push("client:close"); }
    } },
    "devtools/shared/commands/commands-factory": { CommandsFactory: {
      async forRemoteTab(browserId) {
        assert.equal(browserId, 103);
        calls.push("target:103");
        return commands;
      },
    } },
    "devtools/client/framework/devtools": { gDevTools: {
      async showToolbox(_, options) {
        assert.equal(options.hostType, "page");
        assert.equal(options.hostOptions.customIframe, frame);
        selections.push(options.toolId);
        calls.push(`tool:${options.toolId}`);
        return toolbox;
      },
    } },
    "devtools/client/framework/toolbox": { Toolbox: { HostType: { PAGE: "page" } } },
    "resource://gre/modules/dbg-browser-actors.js": { createRootActor() {} },
  };
  globalThis.ChromeUtils = { importESModule(uri) {
    if (uri.endsWith("NavisAndroidLocales.sys.mjs")) return {
      async ensureNavisAndroidLocales() {},
    };
    if (uri.endsWith("PrivateBrowsingUtils.sys.mjs")) return { PrivateBrowsingUtils: {
      isBrowserPrivate: browser => browser.privateMode,
      isWindowPrivate: owner => owner.privateMode,
    } };
    return { require(name) {
      assert.ok(Object.hasOwn(modules, name), `Unexpected dependency: ${name}`);
      return modules[name];
    } };
  } };
  return { host: createHost(window), calls, selections, targetWindow };
}

test("opens the full toolbox over a local pipe and releases it once", async () => {
  const { host, calls } = fixture();
  await host.open(7, "webconsole");
  assert.deepEqual(calls, ["connectPipe", "client:connect", "target:103", "tool:webconsole"]);
  const first = host.close();
  assert.equal(host.close(), first);
  await first;
  assert.deepEqual(calls.slice(-2), ["toolbox:destroy", "frame:remove"]);
});

test("rejects a private target in a normal tool host before opening a client", async () => {
  const { host, calls } = fixture({ targetPrivate: true });
  await assert.rejects(host.open(7, "inspector"), /privacy context/);
  assert.deepEqual(calls, []);
});

test("private tools can inspect a private session", async () => {
  const { host, calls } = fixture({ targetPrivate: true, hostPrivate: true });
  await host.open(7, "netmonitor");
  assert.ok(calls.includes("tool:netmonitor"));
  await host.close();
});

async function assertDefaultSelection(createHost = createNavisAndroidDevTools) {
  const { host, selections } = fixture({ createHost });
  try {
    await host.open(7, "default");
    assert.deepEqual(selections, [undefined], "ordinary opening must defer to Gecko LAST_TOOL");
  } finally { await host.close(); }
}

test("ordinary opening leaves actual toolbox options unset so Gecko restores LAST_TOOL", async () => {
  await assertDefaultSelection();
  const source = readFileSync(new URL("../gecko/devtools/client/framework/toolbox.js", import.meta.url), "utf8");
  assert.match(source, /if \(!selectedTool\)\s*\{\s*selectedTool = Services\.prefs\.getCharPref\(this\._prefs\.LAST_TOOL\)/);
  assert.match(source, /Services\.prefs\.setCharPref\(this\._prefs\.LAST_TOOL, id\)/);
});

test("Inspect keeps an explicit inspector selection", async () => {
  const { host, selections } = fixture();
  await host.open(7, "inspector");
  assert.deepEqual(selections, ["inspector"]);
  await host.close();
});

test("selection test rejects the old hard-coded Inspector and leaked transport sentinel", async () => {
  const source = readFileSync(new URL("../gecko/mobile/shared/modules/navis/NavisAndroidDevTools.sys.mjs", import.meta.url), "utf8");
  for (const replacement of ['toolId: "inspector",', 'toolId: tool,']) {
    const mutant = source.replace('toolId: tool === "default" ? undefined : tool,', replacement);
    assert.notEqual(mutant, source);
    const module = await import(`data:text/javascript;base64,${Buffer.from(mutant).toString("base64")}`);
    await assert.rejects(assertDefaultSelection(module.createNavisAndroidDevTools), /ordinary opening/);
  }
});

test("ordinary menu and context Inspect routes are distinct through the nullable Kotlin chain", () => {
  const root = new URL("../android/src/main/java/org/navis/browser/", import.meta.url);
  const read = path => readFileSync(new URL(path, root), "utf8");
  const app = read("ui/NavisBrowserApp.kt");
  assert.match(app, /onOpenDevTools = \{ openDevTools\(\) \}/);
  assert.match(app, /onInspect = \{ openDevTools\("inspector"\) \}/);
  assert.match(app, /devToolsInitialTool = initialTool/);
  assert.match(app, /DeveloperToolsSurface\(runtime, toolsTarget, devToolsInitialTool\)/);
  assert.match(read("ui/DeveloperToolsSurface.kt"), /runtime\.createDevToolsHost\(target, initialTool\)/);
  assert.match(read("engine/AndroidBrowserRuntime.kt"), /target\.mode == SessionMode\.PRIVATE, initialTool\)/);
  assert.match(read("engine/AndroidDevToolsHost.kt"), /session\.openDevTools\(privateMode, targetSessionId, initialTool\)/);
  assert.match(read("engine/runtime/EngineRuntimePort.kt"), /tool: String\? = null/);
  assert.match(read("engine/runtime/DirectEngineRuntimeAdapter.kt"), /peer\.openDevToolsWindow\(privateMode, targetSessionId, tool \?: "default"\)/);
});

test("rejects missing or closed targets and unknown tool IDs", async () => {
  const { host, calls } = fixture({ targetClosed: true });
  await assert.rejects(host.open(7, "inspector"), /tab has closed/);
  await assert.rejects(host.open(8, "inspector"), /tab has closed/);
  await assert.rejects(host.open(7, "process"), /Invalid developer tools/);
  assert.deepEqual(calls, []);
});

test("viewport calls are fixed, private to this tool host and precede close acknowledgment", async () => {
  const { host, calls, targetWindow } = fixture();
  await host.open(7, "webconsole");
  let restore;
  targetWindow.navisResponsiveTarget.restore = () => new Promise(resolve => { restore = resolve; });
  assert.deepEqual(await host.query("devtools:viewport", { command: "responsive-toggle" }), { responsive: true, zoom: 1 });
  await assert.rejects(host.query("devtools:viewport", { command: "evaluate-script" }), /Unknown viewport/);
  await assert.rejects(host.query("site:clear", {}), /unavailable/);
  await assert.rejects(host.query("devtools:viewport", { command: "zoom-in", target: 9 }), /Invalid/);
  targetWindow.browser.fullZoom = 1.7;
  let finished = false;
  const pending = host.query("devtools:close", {}).then(reply => { finished = true; return reply; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(finished, false);
  assert.equal(calls.includes("toolbox:destroy"), false);
  restore();
  assert.deepEqual(await pending, { restored: true });
  assert.equal(targetWindow.browser.fullZoom, 1);
  assert.deepEqual(calls.slice(-2), ["toolbox:destroy", "frame:remove"]);
});
