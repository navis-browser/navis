// SPDX-License-Identifier: MPL-2.0

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { resolveNavisInternalPageURI } from "../../runtime/embedder/modules/DesktopInternalPages.sys.mjs";

function newURI(value) {
  const url = new URL(value);
  return {
    spec: url.href, specIgnoringRef: url.href.split("#")[0],
    asciiHost: url.hostname, filePath: url.pathname, port: url.port ? Number(url.port) : -1,
    hasQuery: !!url.search, hasRef: !!url.hash, hasUserPass: !!(url.username || url.password),
    schemeIs: scheme => url.protocol === `${scheme}:`,
  };
}
const childSource = readFileSync(new URL("../../runtime/embedder/components/DesktopInternalPageChild.sys.mjs", import.meta.url), "utf8")
  .replace(/^import .*;\n/gm, "").replace("export class DesktopInternalPageChild", "class DesktopInternalPageChild");
const context = { JSWindowActorChild: class {}, resolveNavisInternalPageURI,
  Services: { io: { newURI } }, Cu: { waiveXrays: value => value } };
vm.runInNewContext(`${childSource}\nthis.Actor = DesktopInternalPageChild;`, context);
const actor = new context.Actor(), requests = [];
const document = { documentURIObject: newURI("navis://settings/help") };
actor.contentWindow = { document };
actor.sendQuery = (name, data) => { requests.push({ name, data: structuredClone(data) }); return Promise.resolve({}); };
function click(href = "navis://support/", options = {}) {
  const link = { ownerDocument: document, href, target: options.linkTarget || "", hasAttribute: name => name === "download" && options.download };
  const target = { ownerDocument: document, closest: () => link };
  const event = { type: "click", button: 0, isTrusted: true, defaultPrevented: false,
    target, preventDefault() { this.defaultPrevented = true; }, ...options };
  actor.handleEvent(event);
  return event;
}
const activation = click();
assert.equal(activation.defaultPrevented, true, "An internal link must not enter Gecko's content-triggered dangerous-URI load path");
assert.deepEqual(requests.pop(), { name: "DesktopInternalPage:Command", data: {
  pageKey: "settings/help", command: "internal:navigate", value: { url: "navis://support/", disposition: "current" },
} });
for (const [options, disposition] of [
  [{ ctrlKey: true }, "background"], [{ metaKey: true }, "background"],
  [{ ctrlKey: true, shiftKey: true }, "foreground"], [{ shiftKey: true }, "window"],
  [{ type: "auxclick", button: 1 }, "background"],
  [{ type: "auxclick", button: 1, shiftKey: true }, "foreground"],
  [{ linkTarget: "_blank" }, "foreground"],
  [{ linkTarget: "named-window" }, "foreground"],
  [{ linkTarget: "_top" }, "current"],
]) {
  assert.equal(click("navis://urls/", options).defaultPrevented, true);
  assert.equal(requests.pop().data.value.disposition, disposition);
}
for (const value of ["navis://missing/", "navis://settings/missing", "navis://user@support/",
                     "navis://support/?secret", "navis://support:42/", "navis://settings/%68elp"]) {
  assert.equal(click(value).defaultPrevented, true, "Malformed internal links fail before native IPC");
}
assert.equal(click("navis://support/", { isTrusted: false }).defaultPrevented, true);
assert.equal(click("navis://support/", { altKey: true }).defaultPrevented, true);
assert.equal(click("navis://support/", { download: true }).defaultPrevented, true);
assert.equal(requests.length, 0, "Invalid or synthetic activations cannot request privileged navigation");
assert.equal(click("https://example.test/").defaultPrevented, false);
assert.equal(click("navis://settings/help#section").defaultPrevented, false, "Same-document anchors retain native behavior");
click("navis://settings/help#section", { ctrlKey: true });
assert.equal(requests.pop().data.value.disposition, "background", "Modified fragment links also use browser-owned navigation");
click("navis://support/", { defaultPrevented: true });
click("navis://support/", { type: "auxclick", button: 2 });
document.documentURIObject = newURI("https://example.test/navis://settings/help");
assert.equal(click().defaultPrevented, false, "The bridge never elevates a web link");
assert.equal(requests.length, 0);

// Execute the actual Session authentication and navigation methods. No second
// implementation of the production routing policy is used by the fixture.
const engine = readFileSync(new URL("../../runtime/embedder/modules/DesktopEngine.sys.mjs", import.meta.url), "utf8");
const methods = engine.slice(engine.indexOf("  #handleInternalPageCommand(actor, data) {"), engine.indexOf("  #handleContentInteraction(actor, data) {"));
const OPEN_FROM_GECKO = Symbol(), HANDLE_INTERNAL_PAGE_COMMAND = Symbol();
const loads = [], opens = [];
const browser = { browsingContext: { id: 7 }, page: { key: "settings/help", id: "settings" }, documentGlobal: {} };
const manager = { browsingContext: { id: 7 }, isCurrentGlobal: true, rootFrameLoader: { ownerElement: browser } };
const runtime = { [OPEN_FROM_GECKO]: value => { opens.push(value); return {}; },
  [HANDLE_INTERNAL_PAGE_COMMAND]: () => { throw new Error("Unexpected fallback"); } };
const sessionContext = { browser, runtime, loads, resolveNavisInternalPageURI, Services: { io: { newURI } },
  OPEN_FROM_GECKO, HANDLE_INTERNAL_PAGE_COMMAND, authenticatedInternalPage: value => value.page,
  Ci: { nsIBrowserDOMWindow: { OPEN_NEWTAB_BACKGROUND: 1, OPEN_NEWTAB_FOREGROUND: 2, OPEN_NEWWINDOW: 3 } } };
vm.runInNewContext(`class Session {
  #browser = browser; #runtime = runtime; #assertOpen() {}
  loadUri(url) { loads.push(url); }
  request(actor, data) { return this.#handleInternalPageCommand(actor, data); }
  ${methods}
} this.session = new Session();`, sessionContext);
const request = { pageKey: "settings/help", command: "internal:navigate", value: { url: "navis://support/", disposition: "current" } };
sessionContext.session.request({ manager }, request);
assert.deepEqual(loads, ["navis://support/"]);
for (const [disposition, where] of [["background", 1], ["foreground", 2], ["window", 3]]) {
  sessionContext.session.request({ manager }, { ...request, value: { ...request.value, disposition } });
  assert.equal(opens.at(-1).where, where);
  assert.equal(opens.at(-1).ownerWindow, browser.documentGlobal);
  assert.equal(opens.at(-1).openerBrowser, browser);
  assert.equal(opens.at(-1).triggeringPrincipal, undefined, "Only the authenticated parent operation supplies load authority");
}
for (const value of [null, "navis://support/", { url: "https://example.test/", disposition: "current" },
  { url: "navis://missing/", disposition: "current" }, { url: "navis://support/?secret", disposition: "current" },
  { url: "navis://support/", disposition: "download" }, { url: "x".repeat(8193), disposition: "current" }]) {
  assert.throws(() => sessionContext.session.request({ manager }, { ...request, value }));
}
for (const invalid of [
  { ...manager, isCurrentGlobal: false },
  { ...manager, browsingContext: { id: 8 } },
  { ...manager, rootFrameLoader: { ownerElement: {} } },
]) assert.throws(() => sessionContext.session.request({ manager: invalid }, request));
assert.throws(() => sessionContext.session.request({ manager }, { ...request, pageKey: "urls" }));
browser.page = null;
assert.throws(() => sessionContext.session.request({ manager }, request));
assert.equal(loads.length, 1, "Rejected or stale requests do not mutate navigation");
assert.equal(opens.length, 3, "Rejected requests do not create sessions");

const registry = readFileSync(new URL("../../runtime/embedder/components/DesktopEmbedderActorRegistry.sys.mjs", import.meta.url), "utf8");
const registration = registry.slice(registry.indexOf('registerWindowActor("DesktopInternalPage"'), registry.indexOf('if (AppConstants.MOZ_WEBRTC)'));
for (const type of ["click", "auxclick"]) assert.match(registration, new RegExp(`${type}: \\{ capture: true, mozSystemGroup: true, wantUntrusted: true \\}`));
console.log("PASS authenticated internal navigation: native-default suppression, exact routes, modifiers, same-document links, stale/foreign/untrusted rejection and parent-owned Session loads");
