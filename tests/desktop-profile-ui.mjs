/* Focused logic/DOM-boundary checks; does not launch or inspect a browser. */
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import {
  mountProfileMenu, profileAvatarPresentation, showProfileDialog,
} from "../product/chrome/content/profile-ui.mjs";
import { profileMessages } from "../product/chrome/content/profile-locales.mjs";
import { profilesPageScript } from "../product/chrome/content/profile-page.mjs";

class Element {
  constructor(document, tag) {
    this.ownerDocument = document;
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.style = { setProperty(key, value) { this[key] = value; } };
    this.listeners = new Map();
    this.className = "";
    this.value = "";
    this.classList = {
      add: name => { this.className += ` ${name}`; },
      contains: name => this.className.split(/\s+/).includes(name),
    };
  }
  get isConnected() { return this === this.ownerDocument.body || Boolean(this.parentNode?.isConnected); }
  append(...children) {
    for (const child of children) {
      child.remove();
      child.parentNode = this;
      this.children.push(child);
    }
  }
  prepend(child) { this.append(child); this.children.unshift(this.children.pop()); }
  remove() {
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter(child => child !== this);
      this.parentNode = null;
    }
  }
  replaceChildren(...children) {
    for (const child of [...this.children]) child.remove();
    this.append(...children);
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(type, handler) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), handler]);
  }
  async emit(type, event = {}) {
    event.preventDefault ??= () => { event.defaultPrevented = true; };
    if (type === "click" && this.disabled) return event;
    await Promise.all((this.listeners.get(type) || []).map(handler => handler(event)));
    return event;
  }
  closest() {
    for (let element = this; element; element = element.parentNode) {
      if (element.hidden || "hidden" in element.attributes || "inert" in element.attributes) return element;
    }
    return null;
  }
  focus() { this.ownerDocument.activeElement = this; }
  reportValidity() { return true; }
  showModal() { this.open = true; }
  close() { this.open = false; this.emit("close"); }
}
function documentFixture() {
  const document = { createElement(tag) { return new Element(document, tag); } };
  document.createElementNS = (_, tag) => document.createElement(tag);
  document.body = document.createElement("body");
  const opener = document.createElement("button");
  document.body.append(opener);
  opener.focus();
  return { document, opener };
}
function descendants(node) { return [node, ...node.children.flatMap(descendants)]; }
function byClass(node, name) { return descendants(node).find(child => child.classList.contains(name)); }
function inputs(dialog) {
  return {
    name: descendants(dialog).find(child => child.type === "text"),
    accent: descendants(dialog).find(child => child.type === "color"),
    form: descendants(dialog).find(child => child.tagName === "form"),
    save: descendants(dialog).find(child => child.type === "submit"),
    cancel: descendants(dialog).find(child => child.textContent === "Cancel"),
    swatches: descendants(dialog).filter(child => child.classList.contains("profile-color-swatch")),
  };
}
function pageFixture() {
  const { document } = documentFixture();
  for (const id of ["profiles-list", "profiles-status", "profiles-create", "profiles-restart", "profiles-restart-safe"]) {
    const element = document.createElement(id.startsWith("profiles-restart") || id === "profiles-create" ? "button" : "div");
    element.id = id;
    document.body.append(element);
  }
  document.getElementById = id => descendants(document.body).find(element => element.id === id);
  const window = document.createElement("window");
  const requests = [];
  document.dispatchEvent = event => { requests.push(structuredClone(event.detail)); };
  vm.runInNewContext(profilesPageScript("en-US"), {
    document, window,
    CustomEvent: class { constructor(type, options) { Object.assign(this, { type }, options); } },
  });
  return {
    document, window, requests,
    list: document.getElementById("profiles-list"),
    status: document.getElementById("profiles-status"),
    create: document.getElementById("profiles-create"),
    dialog: () => document.body.children.find(child => child.tagName === "dialog"),
    reply: (profiles, extra = {}) => window.emit("NavisManagementState", { detail: {
      pageKey: "profiles", outcome: "ready", profileManagement: { profiles, isListOutdated: false }, ...extra,
    } }),
  };
}
const pageUser = overrides => ({
  id: "current-profile", userName: "Initial", accentColor: "#0b57d0",
  current: true, default: true, inUse: true, canRemove: false, canLaunch: false, ...overrides,
});

let checks = 0;
async function test(name, run) {
  await run();
  checks++;
  process.stdout.write(`PASS ${name}\n`);
}

await test("avatar keeps Chinese, supplementary characters and emoji graphemes", () => {
  for (const [userName, initial] of [["冷曜", "冷"], [" alice", "A"], ["𠮷田", "𠮷"], ["👩🏽‍💻 Dev", "👩🏽‍💻"], ["👨‍👩‍👧 Family", "👨‍👩‍👧"], ["", "?"]]) {
    assert.equal(profileAvatarPresentation({ userName }).initial, initial);
  }
});
await test("avatar chooses contrasting black/white and rejects invalid colors", () => {
  assert.equal(profileAvatarPresentation({ accentColor: "#FFFFFF" }).color, "#000000");
  assert.equal(profileAvatarPresentation({ accentColor: "#000000" }).color, "#ffffff");
  assert.equal(profileAvatarPresentation({ accentColor: "#0B57D0" }).background, "#0b57d0");
  assert.equal(profileAvatarPresentation({ accentColor: "url(example)" }).background, "#0b57d0");
});
await test("serialized dialog is self-contained and presets/custom colors share one value", async () => {
  const { document, opener } = documentFixture();
  const isolatedDialog = vm.runInNewContext(`(${showProfileDialog.toString()})`);
  const dialog = isolatedDialog(document, profileMessages("en-US"), {
    mode: "create", submit: async () => {},
  });
  const { name, accent, swatches, cancel } = inputs(dialog);
  assert.equal(document.activeElement, name);
  assert.equal(swatches.length, 6);
  assert.ok([...swatches, cancel, inputs(dialog).save].every(control => control.classList.contains("ui-button")), "chrome controls retain the shared Material ripple hook");
  assert.equal(accent.value, "#0b57d0");
  await swatches[1].emit("click");
  assert.equal(accent.value, "#00897b");
  assert.equal(swatches[1].attributes["aria-pressed"], "true");
  assert.equal(swatches[0].attributes["aria-pressed"], "false");
  accent.value = "#123456";
  await accent.emit("input");
  assert.equal(byClass(dialog, "profile-color-value").textContent, "#123456");
  assert.ok(swatches.every(swatch => swatch.attributes["aria-pressed"] === "false"));
  await cancel.emit("click");
  assert.equal(dialog.isConnected, false);
  assert.equal(document.activeElement, opener);
});
await test("pending submit is single-shot, blocks cancel, and keeps all values after failure", async () => {
  const { document } = documentFixture();
  let finish;
  let calls = 0;
  const values = [];
  const dialog = showProfileDialog(document, profileMessages("en-US"), {
    user: { userName: "Old", accentColor: "#0b57d0" },
    submit: value => { calls++; values.push(value); return new Promise((resolve, reject) => { finish = { resolve, reject }; }); },
  });
  const { name, accent, swatches, form, save, cancel } = inputs(dialog);
  name.value = "  New name  ";
  accent.value = "#abcdef";
  const pending = form.emit("submit");
  assert.equal(calls, 1);
  assert.deepEqual(values[0], { userName: "New name", accentColor: "#abcdef" });
  assert.ok([name, accent, save, cancel, ...swatches].every(control => control.disabled));
  assert.equal((await dialog.emit("cancel")).defaultPrevented, true);
  await form.emit("submit");
  assert.equal(calls, 1);
  finish.reject(new Error("fixture failure"));
  await pending;
  assert.equal(dialog.open, true);
  assert.equal(name.value, "  New name  ");
  assert.equal(accent.value, "#abcdef");
  assert.ok([name, accent, save, cancel, ...swatches].every(control => !control.disabled));
  assert.equal(byClass(dialog, "profile-dialog-status").textContent, profileMessages("en-US").failed);
  const retry = form.emit("submit");
  finish.resolve();
  await retry;
  assert.equal(calls, 2);
  assert.equal(dialog.isConnected, false);
});
await test("blank name cannot submit and explicit visible returnFocus wins", async () => {
  const { document, opener } = documentFixture();
  const hiddenMenu = document.createElement("section");
  hiddenMenu.hidden = true;
  const hiddenButton = document.createElement("button");
  hiddenMenu.append(hiddenButton);
  document.body.append(hiddenMenu);
  hiddenButton.focus();
  let calls = 0;
  const dialog = showProfileDialog(document, profileMessages("en-US"), {
    returnFocus: opener, submit: async () => { calls++; },
  });
  const { form, cancel } = inputs(dialog);
  await form.emit("submit");
  assert.equal(calls, 0);
  await cancel.emit("click");
  assert.equal(document.activeElement, opener);
});
await test("created profile launch failure locks identity and retries the same ID without creating again", async () => {
  const { document, opener } = documentFixture();
  const messages = profileMessages("en-US");
  const creates = [], launches = [];
  let failLaunch = true;
  const dialog = showProfileDialog(document, messages, {
    mode: "create",
    submit: async value => { creates.push(value); return { id: "created-profile", ...value, launched: false }; },
    launch: async id => { launches.push(id); if (failLaunch) throw new Error("launch-failed"); },
  });
  const { name, accent, form, save, cancel, swatches } = inputs(dialog);
  name.value = "Work";
  await form.emit("submit");
  assert.equal(dialog.open, true);
  assert.equal(save.textContent, messages.retryLaunch);
  assert.equal(cancel.textContent, messages.close);
  assert.equal(byClass(dialog, "profile-dialog-status").textContent, messages.createdLaunchFailed);
  assert.ok([name, accent, ...swatches].every(control => control.disabled));
  assert.equal(save.disabled, false);
  assert.equal(cancel.disabled, false);
  await form.emit("submit");
  assert.equal(dialog.open, true);
  assert.equal(byClass(dialog, "profile-dialog-status").textContent, messages.createdLaunchFailed);
  failLaunch = false;
  await form.emit("submit");
  assert.equal(creates.length, 1);
  assert.deepEqual(launches, ["created-profile", "created-profile"]);
  assert.equal(dialog.isConnected, false);
  assert.equal(document.activeElement, opener);
});
await test("edit saves preserve newer identity fields that this editor left unchanged", async () => {
  for (const editedField of ["userName", "accentColor"]) {
    const { document } = documentFixture();
    const latest = { userName: "Other window", accentColor: "#123456" };
    let saved;
    const dialog = showProfileDialog(document, profileMessages("en-US"), {
      user: { userName: "Old", accentColor: "#0b57d0" },
      current: async () => latest,
      submit: async value => { saved = value; },
    });
    const { name, accent, form } = inputs(dialog);
    if (editedField === "userName") name.value = "My name";
    else accent.value = "#abcdef";
    await form.emit("submit");
    assert.deepEqual(saved, editedField === "userName"
      ? { userName: "My name", accentColor: latest.accentColor }
      : { userName: latest.userName, accentColor: "#abcdef" });
  }
});
await test("failed identity refresh keeps the editor open without submitting stale fields", async () => {
  const { document } = documentFixture();
  let submitted = false;
  const dialog = showProfileDialog(document, profileMessages("en-US"), {
    user: { userName: "Old", accentColor: "#0b57d0" },
    current: async () => { throw new Error("read-failed"); },
    submit: async () => { submitted = true; },
  });
  const { name, form } = inputs(dialog);
  name.value = "My name";
  await form.emit("submit");
  assert.equal(submitted, false);
  assert.equal(name.value, "My name");
  assert.equal(name.disabled, false);
  assert.equal(dialog.open, true);
});
await test("menu has compact identity, pencil and exactly two icon/label actions", async () => {
  const { document, opener } = documentFixture();
  const container = document.createElement("section");
  document.body.append(container);
  let dismissed = 0;
  const navigations = [];
  await mountProfileMenu({ container, locale: "zh-CN", returnFocus: opener,
    current: async () => ({ userName: "冷曜", accentColor: "#ffffff" }),
    update: async () => {}, create: async () => {},
    dismiss: () => { dismissed++; container.hidden = true; }, navigate: uri => navigations.push(uri),
  });
  const avatar = byClass(container, "profile-menu-avatar");
  assert.equal(avatar.textContent, "冷");
  assert.equal(avatar.style.color, "#000000");
  const pencil = byClass(container, "profile-menu-edit");
  assert.equal(pencil.children[0].attributes["data-icon"], "edit");
  const group = byClass(container, "profile-menu-actions");
  assert.equal(group.children.length, 2);
  assert.deepEqual(group.children.map(button => button.children[0].attributes["data-icon"]), ["plus", "profile"]);
  assert.ok(group.children.every(button => button.classList.contains("menu-item") && button.children[1].classList.contains("menu-item-label")));
  await group.children[1].emit("click");
  assert.deepEqual(navigations, ["navis://profiles/"]);
  container.hidden = false;
  await pencil.emit("click");
  const dialog = document.body.children.find(child => child.tagName === "dialog");
  dialog.close();
  assert.equal(document.activeElement, opener);
  assert.equal(dismissed, 2);
});
await test("late current result cannot overwrite a superseded menu", async () => {
  const { document } = documentFixture();
  const container = document.createElement("section");
  document.body.append(container);
  const existing = document.createElement("p");
  container.append(existing);
  await mountProfileMenu({ container, locale: "en-US", isCurrent: () => false,
    current: async () => ({ userName: "stale" }),
  });
  assert.deepEqual(container.children, [existing]);
});
await test("menu edit reads current identity again before opening and ignores dismissed read replies", async () => {
  const { document, opener } = documentFixture();
  const container = document.createElement("section");
  document.body.append(container);
  let currentUser = { userName: "Initial", accentColor: "#0b57d0" };
  let isCurrent = true;
  let delayedRead = null;
  await mountProfileMenu({ container, locale: "en-US", returnFocus: opener,
    current: () => delayedRead || Promise.resolve(currentUser),
    update: async () => {}, create: async () => {},
    dismiss: () => { container.hidden = true; }, navigate: () => {}, isCurrent: () => isCurrent,
  });
  const pencil = byClass(container, "profile-menu-edit");
  currentUser = { userName: "New from page", accentColor: "#abcdef" };
  await pencil.emit("click");
  const dialog = document.body.children.find(child => child.tagName === "dialog");
  assert.equal(inputs(dialog).name.value, currentUser.userName);
  assert.equal(inputs(dialog).accent.value, currentUser.accentColor);
  dialog.close();
  container.hidden = false;
  let resolve;
  delayedRead = new Promise(done => { resolve = done; });
  const pending = pencil.emit("click");
  isCurrent = false;
  resolve(currentUser);
  await pending;
  assert.ok(!document.body.children.some(child => child.tagName === "dialog"));
  assert.equal(document.activeElement, opener);
});
await test("serialized profiles page refreshes identity on focus and pageshow without overlapping reads", async () => {
  const f = pageFixture();
  assert.deepEqual(f.requests, [{ command: "profiles:get" }]);
  await f.window.emit("pageshow");
  assert.equal(f.requests.length, 1);
  await f.reply([pageUser()]);
  assert.equal(f.list.children[0].children[0].textContent, "Initial");
  assert.equal(f.requests.length, 2);
  await f.window.emit("NavisProfileChanged");
  assert.equal(f.requests.length, 2, "An identity push waits for the active read");
  await f.reply([pageUser()]);
  assert.equal(f.requests.length, 3, "A push arriving during an old read triggers a follow-up read");
  await f.reply([pageUser({ userName: "Changed in menu" })]);
  assert.equal(f.list.children[0].children[0].textContent, "Changed in menu");
  await f.window.emit("focus");
  assert.equal(f.requests.length, 4);
  await f.reply([pageUser({ userName: "Changed on focus" })]);
  assert.equal(f.list.children[0].children[0].textContent, "Changed on focus");
  await f.window.emit("pageshow");
  assert.equal(f.requests.length, 5);
  await f.reply([pageUser({ userName: "Changed in another window" })]);
  assert.equal(f.list.children[0].children[0].textContent, "Changed in another window");
});
await test("serialized page refreshes before edit and save, defers active-dialog redraw, and restores edit focus", async () => {
  const f = pageFixture();
  await f.reply([pageUser()]);
  const originalEdit = f.list.children[0].children[2].children[0];
  originalEdit.focus();
  const opening = originalEdit.emit("click");
  await Promise.resolve();
  assert.equal(f.requests.at(-1).command, "profiles:get");
  assert.equal(f.dialog(), undefined);
  await f.reply([pageUser({ userName: "Fresh name", accentColor: "#abcdef" })]);
  await opening;
  const dialog = f.dialog();
  const { name, accent, form } = inputs(dialog);
  assert.equal(name.value, "Fresh name");
  assert.equal(accent.value, "#abcdef");
  const readCount = f.requests.length;
  await f.window.emit("focus");
  await f.window.emit("pageshow");
  await f.window.emit("NavisProfileChanged");
  assert.equal(f.requests.length, readCount, "Active editor defers passive refresh");
  assert.equal(f.dialog(), dialog);
  assert.equal(originalEdit.isConnected, true, "Fresh read must not detach the editor's return-focus target");
  name.value = "My edited name";
  const saving = form.emit("submit");
  assert.equal(f.requests.at(-1).command, "profiles:get");
  await f.reply([pageUser({ userName: "Another edit", accentColor: "#663399" })]);
  assert.deepEqual(f.requests.at(-1), {
    command: "profile:update", value: { userName: "My edited name", accentColor: "#663399" },
  });
  const saved = pageUser({ userName: "My edited name", accentColor: "#663399" });
  await f.reply([saved]);
  await saving;
  assert.equal(f.dialog(), undefined);
  assert.equal(f.requests.at(-1).command, "profiles:get", "Deferred focus refresh runs after closing");
  const refreshedEdit = f.list.children[0].children[2].children[0];
  assert.notEqual(refreshedEdit, originalEdit);
  assert.equal(f.document.activeElement, refreshedEdit);
  await f.reply([saved]);
  assert.equal(f.list.children[0].children[0].textContent, "My edited name");
  assert.equal(f.document.activeElement, f.list.children[0].children[2].children[0]);
});
await test("serialized page carries committed create result into launch-only recovery", async () => {
  const f = pageFixture();
  await f.reply([pageUser()]);
  f.create.focus();
  await f.create.emit("click");
  const dialog = f.dialog();
  const { name, form, save } = inputs(dialog);
  name.value = "Work";
  const creating = form.emit("submit");
  assert.equal(f.requests.at(-1).command, "profiles:create");
  const created = pageUser({ id: "new-profile", userName: "Work", current: false, default: false, inUse: false, canLaunch: true, canRemove: true });
  await f.reply([pageUser(), created], { profileOperation: { id: created.id, userName: created.userName, accentColor: created.accentColor, launched: false } });
  await creating;
  assert.equal(f.dialog(), dialog);
  assert.equal(save.textContent, profileMessages("en-US").retryLaunch);
  assert.equal(f.status.textContent, profileMessages("en-US").createdLaunchFailed);
  const retry = form.emit("submit");
  assert.deepEqual(f.requests.at(-1), { command: "profiles:launch", value: { id: "new-profile" } });
  await f.reply([pageUser(), created]);
  await retry;
  assert.equal(f.dialog(), undefined);
  assert.equal(f.requests.filter(request => request.command === "profiles:create").length, 1);
  assert.equal(f.list.children.length, 2);
  assert.equal(f.document.activeElement, f.create);
});
await test("serialized page does not open a stale editor when its latest-identity read fails", async () => {
  const f = pageFixture();
  await f.reply([pageUser()]);
  const opening = f.list.children[0].children[2].children[0].emit("click");
  await Promise.resolve();
  await f.reply([], { outcome: "operation-failed" });
  await opening;
  assert.equal(f.dialog(), undefined);
  assert.equal(f.status.textContent, profileMessages("en-US").failed);
});
await test("a malformed save refresh preserves the prior page snapshot and typed editor values", async () => {
  const f = pageFixture();
  await f.reply([pageUser()]);
  const edit = f.list.children[0].children[2].children[0];
  edit.focus();
  const opening = edit.emit("click");
  await Promise.resolve();
  await f.reply([pageUser()]);
  await opening;
  const { name, form, cancel } = inputs(f.dialog());
  name.value = "My name";
  const saving = form.emit("submit");
  await f.reply([], { profileManagement: null });
  await saving;
  assert.equal(name.value, "My name");
  assert.equal(f.requests.filter(request => request.command === "profile:update").length, 0);
  await cancel.emit("click");
  assert.equal(f.dialog(), undefined);
  assert.equal(f.list.children[0].children[0].textContent, "Initial");
  assert.equal(f.document.activeElement, f.list.children[0].children[2].children[0]);
});
await test("dialog styles stand alone and both locales have matching labels", () => {
  const css = readFileSync(new URL("../product/chrome/content/profile-ui.css", import.meta.url), "utf8");
  for (const selector of [".profile-input", ".profile-button", ".profile-color-swatch", ".profile-custom-color", ".profile-menu-actions"]) {
    assert.ok(css.includes(`${selector} {`));
  }
  const english = profileMessages("en-US"), chinese = profileMessages("zh-CN");
  assert.deepEqual(Object.keys(english).sort(), Object.keys(chinese).sort());
  for (const key of Object.keys(english)) assert.ok(english[key] && chinese[key]);
});
process.stdout.write(`${checks} focused profile UI checks passed\n`);
