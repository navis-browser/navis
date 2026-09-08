import assert from "node:assert/strict";
import vm from "node:vm";
import { renderNavisInternalPage } from "../../platform/gecko-chrome/chrome/content/internal-pages.mjs";
import { getNavisInternalPage } from "../../runtime/embedder/modules/DesktopInternalPages.sys.mjs";
import { createNavisLocalizer } from "../../platform/gecko-chrome/chrome/content/localization-core.mjs";

// Executes the actual page script with event, frame and control stubs. This is
// not a browser/DOM inspection or a claim about device geometry or rendering.
class Control {
  children = [];
  attributes = new Map();
  listeners = new Map();
  dataset = {};
  style = {};
  textContent = "";
  className = "";
  hidden = false;
  disabled = false;
  parent = null;
  insertions = 0;
  classList = { add: name => { this.className += " " + name; } };
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }
  fire(type, data = {}) {
    for (const handler of this.listeners.get(type) || []) handler({ target: this, ...data });
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  toggleAttribute(name, enabled) {
    if (enabled) this.attributes.set(name, "");
    else this.attributes.delete(name);
  }
  get firstElementChild() { return this.children[0] ?? null; }
  get nextElementSibling() {
    if (!this.parent) return null;
    return this.parent.children[this.parent.children.indexOf(this) + 1] ?? null;
  }
  insertBefore(child, before) {
    if (child === before) return child;
    child.remove();
    const index = before === null ? this.children.length : this.children.indexOf(before);
    assert(index >= 0, "Insertion anchor must belong to the list");
    this.children.splice(index, 0, child);
    child.parent = this;
    this.insertions++;
    return child;
  }
  append(...children) { for (const child of children) this.insertBefore(child, null); }
  remove() {
    if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1);
    this.parent = null;
  }
  replaceChildren() { throw new Error("Download progress must not replace the list"); }
}

function fixture(locale = "en-US", visible = true, focused = true) {
  const html = renderNavisInternalPage({
    page: getNavisInternalPage("downloads"), pages: [], diagnostics: {}, locale, nonce: "download-test",
  });
  const source = [...html.matchAll(/<script nonce="download-test">([\s\S]*?)<\/script>/g)]
    .map(match => match[1]).find(script => script.includes("const downloadRows ="));
  assert(source, "Downloads must include the real management script");
  const list = new Control(), status = new Control();
  const window = new Control(), document = new Control();
  const requests = [], frames = new Map();
  let serial = 0, visibilityObserver;
  document.documentElement = { dataset: { pageKey: "downloads" } };
  document.visibilityState = visible ? "visible" : "hidden";
  document.focused = focused;
  document.hasFocus = () => document.focused;
  document.getElementById = id => ({ "management-list": list, "management-status": status }[id] ?? null);
  document.createElement = () => new Control();
  document.dispatchEvent = event => requests.push(structuredClone(event.detail));
  window.NavisL10n = createNavisLocalizer(locale);
  vm.runInNewContext(source, {
    window, document,
    CustomEvent: class { constructor(type, options) { Object.assign(this, { type }, options); } },
    requestAnimationFrame(callback) { frames.set(++serial, callback); return serial; },
    cancelAnimationFrame(id) { frames.delete(id); },
    IntersectionObserver: class {
      observed = new Set();
      constructor(callback) { this.callback = callback; visibilityObserver = this; }
      observe(element) { this.observed.add(element); }
      unobserve(element) { this.observed.delete(element); }
    },
  });
  const frame = () => {
    const batch = [...frames.values()];
    frames.clear();
    for (const callback of batch) callback();
  };
  const row = id => list.children.find(element => element.dataset.id === id);
  const fields = id => {
    const element = row(id);
    return {
      element, title: element.children[0].children[0], detail: element.children[0].children[1],
      progress: element.children[0].children[2],
      cancel: element.children[1].children[0], retry: element.children[1].children[1], remove: element.children[1].children[2],
      open: element.children[1].children[3],
    };
  };
  return {
    list, status, window, document, requests, frames, frame, row, fields, visibilityObserver,
    reply(items, revision = 1, extra = {}) {
      window.fire("NavisManagementState", { detail: { pageKey: "downloads", outcome: "ready", items, revision, ...extra } });
    },
    push(items, revision) {
      window.fire("NavisDownloadsChanged", { detail: { pageKey: "downloads", items, revision } });
    },
    seen(id, visible = true) {
      visibilityObserver.callback([{ target: row(id), isIntersecting: visible, intersectionRatio: visible ? 1 : 0 }]);
    },
    attention: () => requests.filter(request => request.command === "downloads:acknowledge"),
  };
}

function item(overrides = {}) {
  return {
    id: "one", fileName: "archive.zip", status: "downloading", currentBytes: 20,
    totalBytes: 100, progress: 20, canCancel: true, canRetry: false, attentionToken: null,
    ...overrides,
  };
}

for (const locale of ["en-US", "zh-CN"]) {
  const f = fixture(locale);
  f.window.fire("pageshow");
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].command, "downloads:get");
  f.reply([item()]);
  assert.equal(f.list.children.length, 0, "Changes wait for the frame boundary");
  f.frame();
  const first = f.fields("one");
  assert.equal(first.progress.getAttribute("aria-valuenow"), "20");
  assert(first.detail.textContent.includes("20%"));
  assert(first.detail.textContent.includes("100"));
  assert.equal(first.progress.hidden, false);
  assert.equal(first.remove.hidden, true);
  assert.equal(first.cancel.hidden, false);
  assert.equal(f.attention().length, 0);
  const insertionCount = f.list.insertions;
  f.document.activeElement = first.cancel;
  f.push([item({ progress: 35, currentBytes: 35 })], 2);
  f.push([item({ progress: 60, currentBytes: 60 })], 3);
  f.frame();
  assert.equal(f.fields("one").element, first.element);
  assert.equal(f.fields("one").cancel, first.cancel);
  assert.equal(f.document.activeElement, first.cancel);
  assert.equal(f.list.insertions, insertionCount, "Progress does not detach focused rows");
  assert.equal(first.progress.getAttribute("aria-valuenow"), "60");
  assert.equal(f.requests.length, 1, "Pushes do not poll the Runtime");
  f.push([item({ progress: 30 })], 2);
  f.frame();
  assert.equal(first.progress.getAttribute("aria-valuenow"), "60", "Old snapshots cannot rewind progress");

  f.push([item({ totalBytes: -1, progress: 80, currentBytes: 70 })], 4);
  f.frame();
  assert.equal(first.progress.getAttribute("aria-valuenow"), null);
  assert(first.progress.attributes.has("data-indeterminate"));
  assert(!first.detail.textContent.includes("%"), "Unknown totals never invent a percentage");
  f.push([item({ status: "pending", currentBytes: 0, progress: 0 })], 5);
  f.frame();
  assert(first.detail.textContent.includes(windowText(locale, "downloads.starting")));
  assert.equal(first.progress.getAttribute("aria-valuenow"), null);
  assert.equal(first.remove.hidden, true, "Starting transfers are active too");
  for (const [offset, downloadStatus] of ["canceled", "failed"].entries()) {
    f.push([item({ status: downloadStatus, canRetry: true, canCancel: false })], 6 + offset);
    f.frame();
    assert.equal(first.progress.hidden, true);
    assert.equal(first.retry.hidden, false);
    assert.equal(first.cancel.hidden, true);
    assert.equal(first.remove.hidden, false);
    assert(first.detail.textContent.includes(windowText(locale, "downloads." + downloadStatus)));
    assert(first.detail.textContent.includes("20"), "Terminal rows retain transferred bytes");
  }
}

function windowText(locale, key) { return createNavisLocalizer(locale).text(key); }

// Open belongs to completed transfers only, remains present-but-disabled when
// the target is missing, and can be used repeatedly after a successful launch.
for (const locale of ["en-US", "zh-CN"]) {
  const f = fixture(locale);
  f.reply([item()]); f.frame();
  const open = f.fields("one").open;
  assert.equal(open.hidden, true);
  f.push([item({ status: "complete", canCancel: false, canOpen: false })], 2); f.frame();
  assert.equal(open.hidden, false);
  assert.equal(open.disabled, true);
  assert.equal(open.textContent, windowText(locale, "common.open"));
  open.fire("click");
  assert.equal(f.requests.length, 0);
  const completed = item({ status: "complete", canCancel: false, canOpen: true });
  f.push([completed], 3); f.frame();
  assert.equal(f.fields("one").open, open);
  assert.equal(open.disabled, false);
  open.fire("click"); open.fire("click");
  assert.equal(open.disabled, true);
  assert.deepEqual(f.requests, [{ command: "downloads:open", value: "one" }]);
  f.push([completed], 4); f.frame();
  assert.equal(open.disabled, true, "Progress/attention pushes cannot unlock a pending open");
  f.reply([completed], 4); f.frame();
  assert.equal(open.disabled, false, "Success must not permanently disable Open");
  open.fire("click", { detail: 2 });
  assert.equal(f.requests.length, 1, "The second click of one double-click is not a second OS launch");
  open.fire("click");
  assert.equal(f.requests.length, 2);
  f.reply(undefined, 4, { outcome: "failed" });
  assert.equal(open.disabled, false);
  assert.equal(f.status.textContent, createNavisLocalizer(locale).text("downloads.openFailed", { name: "archive.zip" }));
  f.push([{ ...completed, canOpen: false }], 5); f.frame();
  assert.equal(open.disabled, true);
}

// An Open queued behind a quiet refresh runs exactly once; other snapshots do
// not lose its pending flag or generate a second OS launch from a double click.
{
  const f = fixture();
  const completed = item({ status: "complete", canOpen: true, canCancel: false });
  f.reply([completed]); f.frame();
  f.window.fire("focus");
  const open = f.fields("one").open;
  open.fire("click"); open.fire("click");
  assert.equal(f.requests.length, 1);
  f.reply([completed], 2);
  assert.deepEqual(f.requests.at(-1), { command: "downloads:open", value: "one" });
  f.frame();
  assert.equal(open.disabled, true);
  f.reply([completed], 2); f.frame();
  assert.equal(open.disabled, false);
  assert.equal(f.requests.filter(request => request.command === "downloads:open").length, 1);
}

// An actual action remains wired through progress ticks; passive push must not
// release its in-flight lock, and a click during a quiet read must not vanish.
{
  const f = fixture();
  f.reply([item()]); f.frame();
  const button = f.fields("one").cancel;
  f.window.fire("focus");
  assert.equal(f.requests.at(-1).command, "downloads:get");
  button.fire("click");
  button.fire("click");
  assert.equal(f.requests.length, 1, "Action waits behind the existing request");
  f.push([item({ progress: 40 })], 2); f.frame();
  assert.equal(f.requests.length, 1, "Push does not pretend the read completed");
  f.reply([item({ progress: 25 })], 1);
  assert.deepEqual(f.requests.at(-1), { command: "downloads:cancel", value: "one" });
  assert.equal(f.requests.length, 2, "The repeated waiting click is coalesced");
  f.reply([item({ status: "canceled", canCancel: false, canRetry: true })], 3); f.frame();
  f.fields("one").retry.fire("click");
  assert.deepEqual(f.requests.at(-1), { command: "downloads:retry", value: "one" });
}

// Attention requires a presented, onscreen terminal token and a focused page.
{
  const f = fixture();
  const complete = item({ status: "complete", canCancel: false, progress: 100, attentionToken: "token-1" });
  f.reply([complete, item({ ...complete, id: "offscreen", attentionToken: "offscreen-token" })]);
  f.frame(); f.frame();
  assert.equal(f.attention().length, 0, "Rendered but offscreen rows remain unread");
  f.seen("one");
  assert.equal(f.attention().length, 0, "Visibility callback alone does not acknowledge");
  f.frame();
  assert.deepEqual(f.attention()[0].value, [{ id: "one", token: "token-1" }]);
  f.push([complete], 2); f.frame(); f.frame();
  assert.equal(f.attention().length, 1, "Repeated snapshots do not resend a receipt");
  f.push([item()], 3); f.frame();
  f.push([{ ...complete, attentionToken: "token-2" }], 4); f.frame(); f.frame();
  assert.deepEqual(f.attention().at(-1).value, [{ id: "one", token: "token-2" }], "A retried download gets its own receipt");
  assert.equal(f.visibilityObserver.observed.size, 1, "Removed rows are unobserved");
}

// Background and focus loss between render and acknowledgement never consume
// attention. Returning to the page schedules a new render before receipt.
{
  const f = fixture("en-US", false, false);
  const complete = item({ status: "complete", attentionToken: "background" });
  f.reply([complete]); f.frame();
  assert.equal(f.list.children.length, 0);
  assert.equal(f.attention().length, 0);
  f.document.visibilityState = "visible";
  f.document.fire("visibilitychange"); f.frame(); f.seen("one"); f.frame();
  assert.equal(f.attention().length, 0, "An unfocused visible window remains unread");
  f.document.focused = true;
  f.window.fire("focus"); f.frame();
  f.document.focused = false;
  f.window.fire("blur"); f.frame();
  assert.equal(f.attention().length, 0);
  f.document.focused = true;
  f.window.fire("focus"); f.frame(); f.frame();
  assert.deepEqual(f.attention()[0].value, [{ id: "one", token: "background" }]);
}

// A failed command remains an error even while normal progress pushes arrive.
{
  const f = fixture();
  f.reply([item()]); f.frame();
  f.fields("one").cancel.fire("click");
  f.reply(undefined, 1, { outcome: "failed" });
  const error = f.status.textContent;
  f.push([item({ progress: 60 })], 2); f.frame();
  assert.equal(f.status.textContent, error);
  assert.equal(f.fields("one").progress.getAttribute("aria-valuenow"), "60");
}

// Leaving the page cancels pending frame work rather than sending late receipts.
{
  const f = fixture();
  f.reply([item({ status: "complete", attentionToken: "late" })]); f.frame();
  f.seen("one");
  f.window.fire("pagehide"); f.frame();
  assert.equal(f.attention().length, 0);
}

console.log("Downloads page live snapshots, stable rows, progress states, queued clicks and attention receipts passed (en-US/zh-CN)");
