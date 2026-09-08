import assert from "node:assert/strict";
import vm from "node:vm";
import { renderNavisInternalPage } from "../../platform/gecko-chrome/chrome/content/internal-pages.mjs";
import { getNavisInternalPage } from "../../runtime/embedder/modules/DesktopInternalPages.sys.mjs";
import { NAVIS_DEFAULT_ACCENT, readAppearanceSettings, setAppearanceSetting } from "../../runtime/embedder/modules/NavisSettingsPolicy.sys.mjs";

const diagnostics = { application: [], engine: [], graphics: [], media: [], network: [], capabilities: [], system: [] };
const html = renderNavisInternalPage({ page: getNavisInternalPage("settings", "appearance"), pages: [], diagnostics, locale: "en-US", nonce: "accent-test" });
const source = [...html.matchAll(/<script nonce="accent-test">([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).find(script => script.includes("const showRoute ="));
assert.match(html, /<button id="appearance-accent-reset"[^>]*type="button"[^>]*>Restore default<\/button>/);

// These are event/control stubs, not a browser, document parser or UI test.
class Control {
  value = "";
  disabled = true;
  hidden = false;
  textContent = "";
  dataset = {};
  attributes = new Map();
  listeners = new Map();
  elements = [];
  addEventListener(type, handler) { this.listeners.set(type, handler); }
  fire(type, details = {}) { this.listeners.get(type)?.({ target: this, ...details }); }
  setAttribute(key, value) { this.attributes.set(key, value); }
  getAttribute(key) { return this.attributes.get(key); }
  removeAttribute(key) { this.attributes.delete(key); }
  toggleAttribute(key, enabled) { if (enabled) this.attributes.set(key, ""); else this.attributes.delete(key); }
  querySelectorAll() { return []; }
}

function fixture(initialAccent = NAVIS_DEFAULT_ACCENT) {
  const values = new Map([["navis.appearance.accent", initialAccent]]);
  const prefs = {
    getStringPref: (key, fallback) => values.get(key) ?? fallback,
    getBoolPref: (key, fallback) => values.get(key) ?? fallback,
    setStringPref: (key, value) => values.set(key, value),
    setBoolPref: (key, value) => values.set(key, value),
    setIntPref: (key, value) => values.set(key, value),
    clearUserPref() { throw new Error("Accent reset must persist its value, not clear the preference"); },
  };
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, new Control());
    return nodes.get(id);
  };
  const routes = ["search", "privacy", "appearance", "downloads", "help"];
  const links = routes.map(route => Object.assign(new Control(), { href: `navis://settings/${route}`, textContent: route, dataset: { settingsRoute: route } }));
  const sections = routes.map(route => Object.assign(new Control(), { dataset: { settingsSection: route } }));
  const window = new Control();
  window.NavisL10n = { text: key => key };
  const requests = [];
  const replies = [];
  const timers = new Map();
  let nextTimer = 0;
  const document = {
    listeners: new Map(),
    addEventListener(type, handler) { this.listeners.set(type, handler); },
    documentElement: { dataset: {}, style: { setProperty() {} } },
    getElementById: node,
    querySelectorAll: selector => selector === "[data-settings-route]" ? links
      : selector === "[data-settings-section]" ? sections : [],
    querySelector: () => null,
    dispatchEvent(event) {
      const request = structuredClone(event.detail);
      requests.push(request);
      if (request.command === "settings:set-appearance") {
        setAppearanceSetting(prefs, request.value.key, request.value.value);
      } else {
        assert.equal(request.command, "settings:get");
      }
      replies.push({ pageKey: "settings", outcome: request.command === "settings:get" ? "ready" : "appearance-updated", appearance: readAppearanceSettings(prefs) });
    },
  };
  vm.runInNewContext(source, {
    document, window, location: { pathname: "/appearance" },
    setTimeout: callback => { timers.set(++nextTimer, callback); return nextTimer; },
    clearTimeout: id => timers.delete(id),
    CustomEvent: class { constructor(type, options) { Object.assign(this, { type }, options); } },
  });
  const reply = () => {
    assert(replies.length, "No pending request to answer");
    window.fire("NavisSettingsState", { detail: replies.shift() });
  };
  window.fire("pageshow");
  assert(node("appearance-accent").disabled);
  reply();
  assert.equal(node("appearance-accent").disabled, false);
  return {
    node, window, requests, replies, reply, values,
    timeout() {
      assert.equal(timers.size, 1);
      const [id, callback] = timers.entries().next().value;
      timers.delete(id);
      callback();
    },
    colour(value, event = "input") {
      node("appearance-accent").value = value;
      node("appearance-accent").fire(event);
    },
    saved: () => values.get("navis.appearance.accent"),
    writes: () => requests.filter(request => request.command === "settings:set-appearance"),
  };
}

// Focus starts a background read before the native picker commits change.
{
  const f = fixture();
  f.window.fire("focus");
  assert.equal(f.node("appearance-accent").disabled, false);
  assert.equal(f.node("search-provider").disabled, false);
  f.colour("#123456", "change");
  assert.equal(f.writes().length, 0, "The write waits instead of disappearing");
  f.reply();
  assert.equal(f.node("appearance-accent").value, "#123456");
  assert.equal(f.writes().length, 1);
  f.reply();
  assert.equal(f.saved(), "#123456");
}

// A stale read may complete after native input preview, but before change.
{
  const f = fixture();
  f.window.fire("focus");
  f.colour("#234567");
  f.reply();
  assert.equal(f.node("appearance-accent").value, "#234567");
  assert.equal(f.writes().length, 0, "Input alone must not save");
  f.node("appearance-accent").fire("change");
  f.reply();
  assert.equal(f.saved(), "#234567");
}

// Already-open native pickers can issue further changes while a write waits.
{
  const f = fixture();
  f.colour("#111111", "change");
  assert.equal(f.node("appearance-accent").disabled, false);
  assert.equal(f.node("appearance-accent-reset").disabled, true);
  f.colour("#222222", "change");
  f.colour("#333333", "change");
  f.reply();
  assert.equal(f.node("appearance-accent").value, "#333333");
  assert.deepEqual(f.writes().map(request => request.value.value), ["#111111", "#333333"]);
  f.reply();
  assert.equal(f.saved(), "#333333");
  assert.equal(f.node("appearance-accent-reset").disabled, false);
}

// Windows can send change after restoring the original preview on cancel.
{
  const f = fixture();
  f.colour("#abcdef");
  f.colour(NAVIS_DEFAULT_ACCENT);
  f.node("appearance-accent").fire("change");
  f.window.fire("focus");
  f.reply();
  assert.equal(f.writes().length, 0);
  assert.equal(f.saved(), NAVIS_DEFAULT_ACCENT);
  assert.equal(f.node("settings-status").textContent, "settings.ready");
  assert.equal(f.node("appearance-accent-reset").disabled, true);
}

// Cancelling without any native preview change also leaves the preference alone.
{
  const f = fixture();
  f.window.fire("focus");
  f.reply();
  assert.equal(f.writes().length, 0);
  assert.equal(f.saved(), NAVIS_DEFAULT_ACCENT);
  assert.equal(f.node("settings-status").textContent, "settings.ready");
}

// A timed-out read's late response must not be mistaken for a current request.
{
  const f = fixture();
  f.window.fire("focus");
  f.timeout();
  assert.equal(f.node("settings-status").textContent, "settings.failed");
  f.reply();
  assert.equal(f.node("settings-status").textContent, "settings.failed");
  f.colour("#345678", "change");
  f.reply();
  assert.equal(f.saved(), "#345678");
}

// Reset uses the returned policy default and the same durable write operation.
{
  const f = fixture("#aabbcc");
  f.window.fire("focus");
  assert.equal(f.node("appearance-accent-reset").disabled, false);
  f.node("appearance-accent-reset").fire("click");
  f.reply();
  assert.deepEqual(f.writes()[0].value, { key: "accent", value: NAVIS_DEFAULT_ACCENT });
  f.reply();
  assert.equal(f.saved(), NAVIS_DEFAULT_ACCENT);
  assert.equal(f.node("appearance-accent-reset").disabled, true);
  f.node("appearance-accent-reset").fire("click");
  assert.equal(f.writes().length, 1);
}

// Reset also records the policy default when undoing an uncommitted draft.
{
  const f = fixture();
  f.colour("#fedcba");
  f.node("appearance-accent-reset").fire("click");
  assert.deepEqual(f.writes()[0].value, { key: "accent", value: NAVIS_DEFAULT_ACCENT });
  f.reply();
  assert.equal(f.node("appearance-accent").value, NAVIS_DEFAULT_ACCENT);
  assert.equal(f.node("appearance-accent-reset").disabled, true);
}

console.log("PASS actual settings script with event stubs: focus/read/change races, preview draft, coalesced pending writes, cancel without save, and explicit default reset; no browser or page inspection");
