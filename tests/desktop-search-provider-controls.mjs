import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
import { renderNavisInternalPage } from "../../platform/gecko-chrome/chrome/content/internal-pages.mjs";
import { getNavisInternalPage } from "../../runtime/embedder/modules/DesktopInternalPages.sys.mjs";
import { createNavisLocalizer } from "../../platform/gecko-chrome/chrome/content/localization-core.mjs";
import { NAVIS_BUILTIN_SEARCH_PROVIDERS, mutateSearchProviders, readDefaultSearchProvider } from "../../runtime/embedder/modules/NavisSettingsPolicy.sys.mjs";

const diagnostics = { application: [], engine: [], graphics: [], media: [], network: [], capabilities: [], system: [] };
const source = await readFile(new URL("../../platform/gecko-chrome/chrome/content/internal-pages.mjs", import.meta.url), "utf8");
for (const locale of ["en-US", "zh-CN"]) {
  const html = renderNavisInternalPage({ page: getNavisInternalPage("settings", "search"), pages: [], diagnostics, locale, nonce: "focused" });
  assert.match(html, /<header class="settings-provider-header">[\s\S]*?id="provider-add"[^>]*aria-label="[^"]+"[\s\S]*?<\/header>\s*<p class="settings-provider-description">/);
  assert.match(html, /<select id="search-provider"/);
  const script = [...html.matchAll(/<script nonce="focused">([\s\S]*?)<\/script>/g)]
    .map(match => match[1]).find(value => value.includes("const renderProviders ="));
  new vm.Script(script);
  const handlers = script.slice(script.indexOf("  const providerIconPaths ="), script.indexOf('  for (const choice of document.querySelectorAll'));
  assert.ok(!handlers.includes('"settings.moveUp"') && !handlers.includes('"settings.moveDown"'));
  const commands = [], edits = [], documentEvents = new Map(), windowEvents = new Map();
  let document;
  class Element {
    constructor(tag) {
      this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {};
      this.listeners = new Map(); this.disabled = false;
      this.classList = { add: value => { this.className += " " + value; } };
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, listener) { this.listeners.set(name, listener); }
    contains(target) { return this === target || this.children.some(child => child.contains(target)); }
    querySelector(selector) { return selector === ":focus" && this.contains(document.activeElement) ? document.activeElement : null; }
    focus() { if (!this.disabled) document.activeElement = this; }
    getBoundingClientRect() { return { top: 100, height: 80 }; }
    fire(name, data = {}) {
      const event = { prevented: false, preventDefault() { this.prevented = true; }, ...data };
      this.listeners.get(name)?.(event);
      return event;
    }
  }
  document = { activeElement: null, createElement: tag => new Element(tag),
    createElementNS: (_namespace, tag) => new Element(tag),
    addEventListener: (name, handler) => documentEvents.set(name, handler) };
  const providerList = new Element("div");
  const context = vm.createContext({ document, providerList, busy: false, pageShown: true, settingsLoaded: true, pendingWrites: [],
    window: { NavisL10n: createNavisLocalizer(locale), addEventListener: (name, handler) => windowEvents.set(name, handler) },
    editProvider: provider => edits.push(provider),
    dispatchCommand: (command, value) => { commands.push({ command, value: { ...value } }); context.busy = true; },
  });
  vm.runInContext(handlers + "\nglobalThis.render = renderProviders;", context);
  const focusHandler = script.slice(script.indexOf('  window.addEventListener("focus", () => {'),
    script.indexOf('  window.addEventListener(\n    "pageshow"'));
  vm.runInContext(focusHandler, context);
  const custom = { id: "custom", name: '<Example "search">', template: "https://example.com/search?q={searchTerms}", builtIn: false };
  let providers = [...NAVIS_BUILTIN_SEARCH_PROVIDERS, custom];
  const values = new Map([["navis.search.providers", JSON.stringify(providers)], ["navis.search.defaultProvider", "bing"]]);
  const prefs = { getStringPref: (key, fallback) => values.get(key) ?? fallback,
    setStringPref: (key, value) => values.set(key, value) };
  const render = () => { context.busy = false; context.render(providers); };
  const rows = () => providerList.children;
  const handle = index => rows()[index].children[0];
  const actions = index => rows()[index].children[2].children;
  const transfer = () => ({ setData() {}, effectAllowed: "", dropEffect: "" });
  const start = index => handle(index).fire("dragstart", { dataTransfer: transfer() });
  const drop = (index, after) => rows()[index].fire("drop", { clientY: after ? 170 : 110 });
  const applyMove = () => {
    const last = commands.at(-1);
    assert.equal(last.command, "settings:search-move");
    providers = [...mutateSearchProviders(prefs, "move", last.value, () => "unused")];
    assert.equal(readDefaultSearchProvider(prefs), "bing", "Sorting must not change the default provider");
    render();
  };
  render();
  for (let index = 0; index < 4; index++) assert.equal(actions(index).length, 1, "Built-in providers expose edit only");
  assert.equal(actions(4).length, 2);
  assert.equal(rows()[4].children[1].children[0].textContent, custom.name, "Names remain text, not injected markup");
  assert.ok(handle(4).attributes["aria-label"].includes(custom.name));
  assert.equal(handle(4).attributes["aria-keyshortcuts"], "ArrowUp ArrowDown");
  actions(4)[0].fire("click"); assert.equal(edits[0], custom);
  actions(4)[1].fire("click"); assert.deepEqual(commands.pop(), { command: "settings:search-remove", value: { id: "custom" } });
  render();
  handle(0).fire("keydown", { key: "ArrowUp" }); assert.equal(commands.length, 0);
  handle(1).focus(); handle(1).fire("keydown", { key: "ArrowUp" });
  assert.deepEqual(commands.at(-1).value, { id: "bing", position: 0 }); applyMove();
  assert.equal(document.activeElement.dataset.providerId, "bing", "Restore keyboard focus after the persisted move renders");
  start(0); rows()[4].fire("dragover", { clientY: 170, dataTransfer: transfer() });
  assert.equal(rows()[4].dataset.drop, "after"); drop(4, true);
  assert.deepEqual(commands.at(-1).value, { id: "bing", position: 4 }); applyMove();
  assert.equal(providers[4].id, "bing");
  start(4); drop(0, false); assert.deepEqual(commands.at(-1).value, { id: "bing", position: 0 }); applyMove();
  // Press/focus precedes native dragstart. Native DnD then cancels pointer events;
  // neither that cancellation, a window blur nor an unchanged read may detach it.
  const held = handle(0), beforeDrag = commands.length;
  held.fire("pointerdown", { pointerId: 7, button: 0 });
  windowEvents.get("focus")();
  assert.equal(commands.length, beforeDrag, "Pressing the handle must not start a competing focus refresh");
  start(0);
  windowEvents.get("pointercancel")({ pointerId: 7 });
  windowEvents.get("blur")?.();
  context.render(JSON.parse(JSON.stringify(providers)));
  assert.equal(handle(0), held, "An unchanged settings response must preserve the actual drag source node");
  windowEvents.get("focus")();
  assert.equal(commands.length, beforeDrag, "The native drag focus cycle must not start a settings refresh");
  assert.equal(rows()[2].fire("dragover", { clientY: 170 }).prevented, true);
  drop(2, true);
  assert.deepEqual(commands.at(-1).value, { id: "bing", position: 2 }); applyMove();
  for (const end of ["pointerup", "pointercancel"]) {
    handle(0).fire("pointerdown", { pointerId: 8, button: 0 });
    windowEvents.get(end)({ pointerId: 8 });
    windowEvents.get("focus")();
    assert.equal(commands.pop().command, "settings:get", "A non-drag release restores ordinary focus refresh");
    context.busy = false;
  }
  const count = commands.length;
  start(0); drop(0, false); assert.equal(commands.length, count, "Dropping at the same position is a no-op");
  drop(2, true); assert.equal(commands.length, count, "External drops cannot issue moves");
  for (const finish of [() => handle(0).fire("dragend"), () => documentEvents.get("keydown")({ key: "Escape", preventDefault() {} }), () => windowEvents.get("pagehide")()]) {
    start(0); rows()[1].fire("dragover", { clientY: 110 }); finish();
    assert.ok(rows().every(row => !row.dataset.drop && !row.dataset.dragging));
    drop(2, false); assert.equal(commands.length, count);
  }
  start(0); context.busy = true; drop(2, true);
  handle(0).fire("keydown", { key: "ArrowDown" }); actions(0)[0].fire("click");
  assert.equal(commands.length, count, "Busy requests reject drag and keyboard writes");
  assert.equal(edits.length, 1);
  assert.equal(start(0).prevented, true);
  context.busy = false; context.pendingWrites.push({});
  assert.equal(start(0).prevented, true); context.pendingWrites.length = 0;
  context.settingsLoaded = false; assert.equal(start(0).prevented, true);
}
assert.match(source, /grid-template-columns: 40px minmax\(0, 1fr\) 80px/);
assert.match(source, /\.settings-provider-copy small \{[^}]*overflow-wrap: anywhere/);
console.log("Desktop search provider controls: real handlers passed in en-US and zh-CN");
