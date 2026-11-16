// SPDX-License-Identifier: MPL-2.0
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { getNavisInternalPage } from "../../runtime/embedder/modules/DesktopInternalPages.sys.mjs";
import { renderNavisInternalPage } from "../../platform/gecko-chrome/chrome/content/internal-pages.mjs";
import { createNavisLocalizer } from "../../platform/gecko-chrome/chrome/content/localization-core.mjs";

for (const locale of ["en-US", "zh-CN"]) {
  test(`${locale}: real settings script renders safe exception rows, serializes removal and preserves focus/errors`, () => {
    const html = renderNavisInternalPage({page: getNavisInternalPage("settings", "privacy"),
      pages: [], diagnostics: {application: [], engine: [], graphics: [], media: [], network: [], capabilities: [], system: []}, locale, nonce: "certificates"});
    const script = [...html.matchAll(/<script nonce="certificates">([\s\S]*?)<\/script>/g)]
      .map(match => match[1]).find(value => value.includes("const showRoute ="));
    const t = createNavisLocalizer(locale).text;
    assert(html.includes(t("settings.certificateExceptionsDescription")));
    let document;
    class Node {
      children = []; dataset = {}; attributes = new Map(); listeners = new Map();
      disabled = true; hidden = false; textContent = ""; value = ""; elements = [];
      constructor(tag = "div") { this.tag = tag; }
      append(...nodes) { this.children.push(...nodes); }
      replaceChildren(...nodes) { this.children = nodes; }
      querySelectorAll(selector) { return this.children.flatMap(child =>
        [...(child.tag === selector ? [child] : []), ...child.querySelectorAll(selector)]); }
      querySelector() { return null; }
      addEventListener(type, fn) { this.listeners.set(type, fn); }
      fire(type, event = {}) { this.listeners.get(type)?.({target: this, preventDefault() {}, ...event}); }
      setAttribute(key, value) { this.attributes.set(key, value); }
      getAttribute(key) { return this.attributes.get(key); }
      toggleAttribute() {}
      focus() { document.activeElement = this; }
      close() {}
    }
    const nodes = new Map(), requests = [];
    const node = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
    document = {getElementById: node, querySelectorAll: () => [], querySelector: () => null,
      createElement: tag => new Node(tag), addEventListener() {},
      documentElement: {dataset: {}, style: {setProperty() {}}},
      dispatchEvent: event => requests.push(structuredClone(event.detail))};
    const window = new Node(); window.NavisL10n = {text: t};
    vm.runInNewContext(script, {document, window, location: {pathname: "/privacy"},
      setTimeout: () => 1, clearTimeout() {},
      CustomEvent: class {constructor(type, value) {Object.assign(this, {type}, value);}}});
    const entries = [{id: "first", hostPort: "<img>:443", fingerprint: "AA"},
      {id: "second", hostPort: "other.test:443", fingerprint: "BB"}];
    const reply = (entries, outcome = "ready") => window.fire("NavisSettingsState", {
      detail: {pageKey: "settings/privacy", outcome, certificateExceptions: entries}});
    const buttons = () => node("certificate-exceptions").querySelectorAll("button");
    window.fire("pageshow"); reply(entries);
    assert.equal(buttons().length, 2); assert.equal(node("certificate-exceptions-empty").hidden, true);
    assert.equal(node("certificate-exceptions").children[0].children[0].children[0].textContent, "<img>:443");
    assert.equal(buttons()[0].getAttribute("aria-label"), t("settings.revokeCertificateExceptionFor", {site: "<img>:443"}));
    assert.equal(buttons()[0].disabled, false);
    buttons()[0].focus(); buttons()[0].fire("click");
    assert.deepEqual(requests.at(-1), {command: "settings:revoke-certificate-exception", value: "first"});
    assert(buttons().every(button => button.disabled));
    reply(entries, "failed");
    assert.equal(buttons().length, 2); assert.equal(buttons()[0].disabled, false);
    assert.equal(node("settings-status").textContent, t("settings.failed"));
    buttons()[0].fire("click"); reply(entries.slice(1), "certificate-exception-revoked");
    assert.equal(buttons().length, 1); assert.equal(document.activeElement, buttons()[0]);
    assert.equal(node("settings-status").textContent, t("settings.certificateExceptionRevoked"));
    buttons()[0].fire("click"); reply([], "certificate-exception-revoked");
    assert.equal(node("certificate-exceptions-empty").hidden, false);
    assert.equal(document.activeElement, node("certificate-exceptions-empty"));
  });
}

test("the authenticated actor accepts only bounded exception IDs", async () => {
  const source = (await readFile(new URL("../../runtime/embedder/components/DesktopInternalPageChild.sys.mjs", import.meta.url), "utf8"))
    .replace(/^import .*;$/gmu, "").replace("export class DesktopInternalPageChild", "class DesktopInternalPageChild");
  const context = {JSWindowActorChild: class {}, Cu: {waiveXrays: value => value}};
  vm.runInNewContext(`${source}\nglobalThis.sanitize = sanitizeCommandValue; globalThis.commands = SETTINGS_COMMANDS;`, context);
  const command = "settings:revoke-certificate-exception";
  assert(context.commands.has(command));
  assert.equal(context.sanitize(command, "opaque-id").value, "opaque-id");
  for (const value of [undefined, null, "", "x".repeat(65), {}, [], true]) {
    assert.equal(context.sanitize(command, value).valid, false);
  }
});
