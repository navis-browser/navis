import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";

const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const moduleSource = read("../runtime/gecko/mobile/shared/modules/navis/NavisAndroidStartup.sys.mjs");
const topologySource = read("../runtime/gecko/mobile/shared/modules/navis/NavisAndroidExtensionTabTopology.sys.mjs");
const parentSource = read("../runtime/gecko/toolkit/components/extensions/ExtensionParent.sys.mjs");
const backgroundSource = read("../runtime/gecko/toolkit/components/extensions/parent/ext-backgroundPage.js");
const resetStart = parentSource.indexOf("ExtensionParent._resetStartupPromises = () => {");
const resetEnd = parentSource.indexOf("ExtensionParent._resetStartupPromises();", resetStart);
assert.ok(resetStart >= 0 && resetEnd > resetStart, "exercise the pinned observer implementation");
const resetSource = parentSource.slice(resetStart, resetEnd) + "ExtensionParent._resetStartupPromises();";
const manifestStart = backgroundSource.indexOf("  async onManifestEntry() {");
const manifestEnd = backgroundSource.indexOf("\n  onShutdown(isAppShutdown)", manifestStart);
assert.ok(manifestStart >= 0 && manifestEnd > manifestStart, "exercise the pinned APP_STARTUP branch");
const manifestSource = backgroundSource.slice(manifestStart, manifestEnd);
const primeStart = backgroundSource.indexOf('    extension.once("start-background-script", async () => {');
const primeEnd = backgroundSource.indexOf("\n  onBgInstanceShutdown(", primeStart);
assert.ok(primeStart >= 0 && primeEnd > primeStart, "exercise the pinned primed-request wakeup");
const primedEvents = backgroundSource.slice(primeStart, primeEnd).replace(/\n  }\s*$/, "");
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness(source = moduleSource) {
  const events = [];
  const tasks = [];
  const observers = new Map();
  const ExtensionParent = {};
  let initializedObservers = false;
  const context = vm.createContext({
    ExtensionParent,
    Services: {
      tm: { dispatchToMainThread: fn => tasks.push(fn) },
      obs: { notifyObservers(subject, topic) {
        events.push(topic);
        for (const resolve of observers.get(topic) ?? []) resolve(subject);
      } },
    },
    promiseObserved(topic) {
      return new Promise(resolve => {
        observers.set(topic, [...(observers.get(topic) ?? []), resolve]);
      });
    },
    ChromeUtils: { importESModule(path) {
      if (path.endsWith("/ExtensionParent.sys.mjs")) {
        if (!initializedObservers) {
          initializedObservers = true;
          vm.runInContext(resetSource, context);
        }
        return { ExtensionParent };
      }
      assert.ok(path.endsWith("/NavisAndroidExtensionTabTopology.sys.mjs"));
      return { NavisAndroidExtensionTabTopology: context.topologyUnderTest };
    } },
    PrivateBrowsingUtils: { permanentPrivateBrowsing: false },
    EventManager: { clearPrimedListeners() {}, primeListeners() {} },
  });
  vm.runInContext(topologySource.replaceAll("export const ", "const ") +
    "\nglobalThis.topologyUnderTest = NavisAndroidExtensionTabTopology;", context);
  vm.runInContext(source.replace("export const NavisAndroidStartup", "const NavisAndroidStartup") +
    "\nglobalThis.startupUnderTest = NavisAndroidStartup;", context);
  const topology = context.topologyUnderTest;
  const startup = context.startupUnderTest;
  const windows = [];
  function window(id, type = "navigator:navis-android", popup = false) {
    const host = { closed: false, document: { documentElement: { getAttribute: () => type } } };
    host.browser = { isConnected: true, documentGlobal: host };
    host.tab = { id, browser: host.browser, isPopup: popup };
    if (type === "navigator:navis-android" && !popup) {
      windows.push(host);
      topology.registerNativeTab(host.tab);
    }
    return host;
  }
  function publish() {
    topology.applySnapshot({ revision: topology.revision + 1, windowId: 1,
      activeTabId: windows[0].tab.id, tabs: windows.map((host, index) => ({
        tabId: host.tab.id, index, privateMode: false,
      })), events: [] });
  }
  async function tick() {
    assert.ok(tasks.length, "expected a separate main-thread startup stage");
    tasks.shift()();
    await settle();
  }
  async function background({ persistent = true, startupReason = "APP_STARTUP" } = {}) {
    const extension = new EventEmitter();
    Object.assign(extension, { startupReason, persistentBackground: persistent,
      persistentListeners: new Map([["webRequest", new Map()], ["runtime", new Map()]]),
      hasShutdown: false });
    let builds = 0;
    context.BackgroundBuilder = class {
      constructor(owner) {
        this.extension = owner;
        this.backgroundContextOwner = { canBePrimed: true, bgInstance: null };
      }
      primeBackground() {
        context.builder = this;
        // Invoke the real event-registration tail of Gecko's primeBackground,
        // stubbing only page construction (no process/browser in a Node test).
        vm.runInContext(`(function () { const extension = this.extension; ${primedEvents} }).call(builder);`, context);
      }
      async build() {
        if (this.backgroundContextOwner.bgInstance) return;
        builds++;
        this.backgroundContextOwner.bgInstance = {};
        extension.emit("background-script-started");
      }
    };
    context.extension = extension;
    vm.runInContext(`globalThis.backgroundAPI = { extension, ${manifestSource} };`, context);
    await context.backgroundAPI.onManifestEntry();
    return { extension, builds: () => builds };
  }
  return { startup, window, publish, tick, events, tasks, background, ExtensionParent };
}

test("cold persistent background remains primed until product startup then starts once", async () => {
  const state = harness();
  const host = state.window(1);
  const background = await state.background();
  assert.equal(background.builds(), 0, "APP_STARTUP is not ADDON_ENABLE");
  assert.throws(() => state.startup.completeProductInitialization(host), /not ready/);
  state.startup.markChromeReady(host);
  assert.throws(() => state.startup.completeProductInitialization(host), /not ready/);
  state.publish();
  const completion = state.startup.completeProductInitialization(host);
  assert.equal(state.startup.completeProductInitialization(host), completion);
  await state.tick();
  assert.deepEqual(state.events, ["browser-delayed-startup-finished"]);
  assert.equal(background.builds(), 0, "persistent startup awaits stage two");
  await state.tick();
  await completion;
  assert.equal(background.builds(), 1);
  assert.deepEqual(state.events, ["browser-delayed-startup-finished", "extensions-late-startup"]);
  await state.startup.completeProductInitialization(host);
  assert.equal(state.events.length, 2, "repeated initialization must not repeat lifecycle signals");
});

test("cold primed webRequest releases after chrome readiness, without requiring a Gecko surface", async () => {
  const state = harness();
  const host = state.window(1);
  const background = await state.background({ persistent: false });
  let requestResumed = false;
  background.extension.once("background-script-started", () => { requestResumed = true; });
  background.extension.emit("background-script-event");
  await settle();
  assert.equal(requestResumed, false);
  state.startup.markChromeReady(host);
  state.publish();
  const completion = state.startup.completeProductInitialization(host);
  await state.tick();
  assert.equal(requestResumed, true, "real primed-event handler no longer waits forever");
  assert.equal(background.builds(), 1);
  await state.tick();
  await completion;
  assert.equal(background.builds(), 1);
});

test("startup never runs before every restored product host is ready", async () => {
  const state = harness();
  const first = state.window(1);
  const restored = state.window(2);
  state.startup.markChromeReady(first);
  state.publish();
  assert.throws(() => state.startup.completeProductInitialization(first), /not ready/);
  assert.equal(state.tasks.length, 0);
  state.startup.markChromeReady(restored);
  const completion = state.startup.completeProductInitialization(first);
  await state.tick();
  await state.tick();
  await completion;
});

test("popup and toolbox hosts cannot declare product startup complete", () => {
  const state = harness();
  const main = state.window(1);
  state.startup.markChromeReady(main);
  state.publish();
  for (const invalid of [state.window(2, "navigator:navis-devtools"),
    state.window(3, "navigator:navis-android", true)]) {
    state.startup.markChromeReady(invalid);
    assert.throws(() => state.startup.completeProductInitialization(invalid), /not ready/);
  }
  assert.equal(state.events.length, 0);
});

test("closed host before dispatch does not publish a false ready notification", async () => {
  const state = harness();
  const host = state.window(1);
  state.startup.markChromeReady(host);
  state.publish();
  const completion = state.startup.completeProductInitialization(host);
  const rejected = assert.rejects(completion, /not ready/);
  host.closed = true;
  await state.tick();
  await rejected;
  assert.equal(state.events.length, 0);
});

test("Gecko ADDON_ENABLE bypass explains why toggling hid the cold-start defect", async () => {
  const state = harness();
  const enabled = await state.background({ startupReason: "ADDON_ENABLE" });
  assert.equal(enabled.builds(), 1);
  assert.equal(state.events.length, 0);
});

test("observer setup is eager, including extensions loaded after the startup signals", async () => {
  const state = harness();
  assert.ok(state.ExtensionParent.browserStartupPromise);
  const host = state.window(1);
  state.startup.markChromeReady(host);
  state.publish();
  const completion = state.startup.completeProductInitialization(host);
  await state.tick();
  await state.tick();
  await completion;
  const late = await state.background();
  await settle();
  assert.equal(late.builds(), 1);
});

test("mutation: removing either startup signal reproduces its blocked background path", async () => {
  for (const topic of ["browser-delayed-startup-finished", "extensions-late-startup"]) {
    const needle = `Services.obs.notifyObservers(window, "${topic}");`;
    assert.ok(moduleSource.includes(needle));
    const state = harness(moduleSource.replace(needle, "/* removed notification */"));
    const host = state.window(1);
    const persistent = topic === "extensions-late-startup";
    const background = await state.background({ persistent });
    if (!persistent) background.extension.emit("background-script-event");
    state.startup.markChromeReady(host);
    state.publish();
    const completion = state.startup.completeProductInitialization(host);
    await state.tick();
    await state.tick();
    await completion;
    assert.throws(() => assert.equal(background.builds(), 1),
      { name: "AssertionError" }, `${topic} deletion must fail the cold-start guarantee`);
  }
});

test("the actual host handshake finishes product policy before extension startup", () => {
  const host = read("../runtime/gecko/mobile/shared/chrome/navis/host.js");
  assert.match(host, /const result = await product\.query\([\s\S]*?if \(data\.operation === "product:initialize"\) \{\s*await NavisAndroidStartup\.completeProductInitialization\(window\);/);
  assert.match(host, /NavisAndroidStartup\.markChromeReady\(window\);\s*dispatcher\.dispatch\("NavisAndroid:HostReady"/);
  const runtime = read("../platform/android/src/main/java/org/navis/browser/engine/AndroidBrowserRuntime.kt");
  assert.match(runtime, /awaitInitialExtensionHost\(\)[\s\S]*?publishExtensionTopology\(\)[\s\S]*?product\.initialize\(\)[\s\S]*?initializeExtensions\(/);
  assert.match(read("../runtime/gecko/mobile/shared/modules/navis/moz.build"), /"NavisAndroidStartup.sys.mjs"/);
  assert.doesNotMatch(moduleSource, /GeckoView.*import|ModuleManager\.|MozAfterPaint|\.start\(\)|extensions\.lateStartup/);
});
