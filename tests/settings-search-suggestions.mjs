import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { getNavisInternalPage, resolveNavisInternalPageURI } from "../../runtime/embedder/modules/DesktopInternalPages.sys.mjs";
import { renderNavisInternalPage } from "../../platform/gecko-chrome/chrome/content/internal-pages.mjs";
import { createNavisLocalizer } from "../../platform/gecko-chrome/chrome/content/localization-core.mjs";

const diagnostics = { application: [], engine: [], graphics: [], media: [], network: [], capabilities: [], system: [] };
let source;
for (const locale of ["en-US", "zh-CN"]) {
  const html = renderNavisInternalPage({ page: getNavisInternalPage("settings", "search"), pages: [], diagnostics, locale, nonce: "suggestions" });
  const field = html.match(/<input name="suggestionTemplate"[^>]*>/)?.[0];
  assert(field, "Provider editing must expose its optional suggestion URL");
  assert.match(field, /maxlength="4096"/);
  assert.doesNotMatch(field, /\brequired\b/);
  assert.match(field, /aria-describedby="provider-suggestions-help"/);
  const toggle = html.match(/<button id="remote-suggestions-toggle"[^>]*>/)?.[0];
  assert.match(toggle ?? "", /role="switch"[^>]*aria-checked="false"/);
  assert.match(toggle, /disabled/);
  const t = createNavisLocalizer(locale).text;
  for (const key of ["settings.providerSuggestionTemplate", "settings.remoteSuggestions", "settings.remoteSuggestionsDescription"]) {
    assert(html.includes(t(key)), `${locale} localizes ${key}`);
  }
  source = [...html.matchAll(/<script nonce="suggestions">([\s\S]*?)<\/script>/g)]
    .map(match => match[1]).find(script => script.includes("const showRoute ="));
}

class Control {
  value = ""; disabled = true; hidden = false; textContent = ""; dataset = {};
  attributes = new Map(); listeners = new Map(); elements = []; children = [];
  addEventListener(type, callback) { this.listeners.set(type, callback); }
  fire(type, details = {}) { this.listeners.get(type)?.({ target: this, preventDefault() {}, ...details }); }
  setAttribute(key, value) { this.attributes.set(key, value); }
  getAttribute(key) { return this.attributes.get(key); }
  removeAttribute(key) { this.attributes.delete(key); }
  toggleAttribute(key, enabled) { if (enabled) this.attributes.set(key, ""); else this.attributes.delete(key); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const nodes = new Map();
const node = id => { if (!nodes.has(id)) nodes.set(id, new Control()); return nodes.get(id); };
for (const name of ["name", "template", "suggestionTemplate"]) {
  const field = new Control();
  node("provider-form").elements.push(field);
  node("provider-form").elements[name] = field;
}
const requests = [], timers = new Map();
let timerId = 0;
const window = new Control();
window.NavisL10n = { text: key => key };
const context = {
  document: {
    getElementById: node, querySelectorAll: () => [], querySelector: () => null,
    documentElement: { dataset: {}, style: { setProperty() {} } }, addEventListener() {},
    dispatchEvent: event => requests.push(structuredClone(event.detail)),
  }, window, location: { pathname: "/search" },
  setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
  clearTimeout: id => timers.delete(id),
  CustomEvent: class { constructor(type, options) { Object.assign(this, { type }, options); } },
};
vm.runInNewContext(source.replace(/\}\)\(\);$/, "globalThis.editProviderForTest = editProvider;})();"), context);
const reply = (remoteSuggestionsEnabled, outcome = "ready") => window.fire("NavisSettingsState", {
  detail: { pageKey: "settings/search", outcome, search: { remoteSuggestionsEnabled } },
});
const toggle = node("remote-suggestions-toggle");
toggle.setAttribute("aria-checked", "false");
window.fire("pageshow");
assert(toggle.disabled, "Do not offer opt-in before settings load");
reply(false);
assert.equal(toggle.disabled, false);
assert.equal(toggle.getAttribute("aria-checked"), "false");

// An opt-in made during a focus refresh must wait for that read and survive it.
window.fire("focus");
toggle.fire("click");
assert.equal(requests.at(-1).command, "settings:get");
assert(toggle.disabled);
reply(false);
assert.deepEqual(requests.at(-1), { command: "settings:set-remote-suggestions", value: true });
assert.equal(toggle.getAttribute("aria-checked"), "false", "Unconfirmed opt-in must not become the displayed state");
reply(true, "search-provider-updated");
assert.equal(toggle.getAttribute("aria-checked"), "true");
assert.equal(toggle.disabled, false);
toggle.fire("click");
assert.deepEqual(requests.at(-1), { command: "settings:set-remote-suggestions", value: false });
reply(false, "failed");
assert.equal(toggle.getAttribute("aria-checked"), "true", "A rejected write retains confirmed state");

const provider = { id: "custom", name: "Custom", template: "https://example.com/?q={searchTerms}", suggestionTemplate: "https://example.com/suggest?q={searchTerms}" };
context.editProviderForTest(provider);
assert.equal(node("provider-form").elements.suggestionTemplate.value, provider.suggestionTemplate);
node("provider-form").fire("submit");
assert.deepEqual(requests.at(-1), { command: "settings:search-update", value: provider });
assert(node("provider-form").elements.suggestionTemplate.disabled);
reply(true, "search-provider-updated");
assert.equal(node("provider-dialog").open, false);
context.editProviderForTest(provider);
node("provider-form").elements.suggestionTemplate.value = "";
node("provider-form").fire("submit");
assert.equal(requests.at(-1).value.suggestionTemplate, "", "Clearing the endpoint is a deliberate provider update");
reply(true, "search-provider-updated");
context.editProviderForTest(null);
assert.equal(node("provider-form").elements.suggestionTemplate.value, "", "Add does not retain an edited provider's endpoint");

const actorSource = readFileSync(new URL("../../runtime/embedder/components/DesktopInternalPageChild.sys.mjs", import.meta.url), "utf8")
  .replace(/^import .*;\n/gm, "").replace("export class DesktopInternalPageChild", "class DesktopInternalPageChild") + "\nglobalThis.Actor = DesktopInternalPageChild;";
const actorContext = { JSWindowActorChild: class {}, resolveNavisInternalPageURI,
  Cu: { waiveXrays: value => value, cloneInto: value => structuredClone(value) } };
vm.runInNewContext(actorSource, actorContext);
const actor = new actorContext.Actor(), actorRequests = [];
const document = { documentURIObject: { schemeIs: scheme => scheme === "navis", asciiHost: "settings", hasUserPass: false,
  port: -1, hasQuery: false, filePath: "/search", specIgnoringRef: "navis://settings/search" } };
actor.contentWindow = { document };
actor.sendQuery = (_topic, data) => { actorRequests.push(structuredClone(data)); return new Promise(() => {}); };
const send = (command, value) => actor.handleEvent({ target: document, type: "NavisSettingsCommand", detail: { command, value } });
for (const suggestionTemplate of [provider.suggestionTemplate, "", "x".repeat(4096)]) {
  send("settings:search-update", { ...provider, suggestionTemplate });
  assert.equal(actorRequests.at(-1).value.suggestionTemplate, suggestionTemplate);
}
for (const value of [false, true]) {
  send("settings:set-remote-suggestions", value);
  assert.equal(actorRequests.at(-1).value, value);
}
const count = actorRequests.length;
for (const suggestionTemplate of ["x".repeat(4097), 7, true, {}, []]) send("settings:search-update", { ...provider, suggestionTemplate });
for (const value of [undefined, null, "true", 1, {}, []]) send("settings:set-remote-suggestions", value);
send("settings:set-arbitrary-pref", true);
document.documentURIObject.asciiHost = "history";
send("settings:set-remote-suggestions", true);
assert.equal(actorRequests.length, count, "Only bounded, typed values from settings reach the parent");
console.log("PASS desktop suggestion settings: EN/ZH optional endpoint and opt-in, real script read/write ownership and failure state, provider endpoint round-trip/clear, actor bounds and route allowlist; no browser execution");
