/* Executes production toolbar/panel handlers with focused boundary stubs. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../product/chrome/content/main.mjs", import.meta.url), "utf8");
const start = source.indexOf("  const performDownloadAction = async (");
const end = source.indexOf("  closeCompetingTransientSurfaces = (", start);
assert.ok(start > 0 && end > start);
const handlers = source.slice(start, end);

class Element {
  constructor(document, tag) {
    this.ownerDocument = document;
    this.tagName = tag;
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.listeners = {};
    this.style = { setProperty(key, value) { this[key] = value; }, removeProperty(key) { delete this[key]; } };
    this.hidden = false;
  }
  get isConnected() { return this === this.ownerDocument.body || Boolean(this.parentNode?.isConnected); }
  get firstElementChild() { return this.children[0] || null; }
  get nextElementSibling() { return this.parentNode?.children[this.parentNode.children.indexOf(this) + 1] || null; }
  append(...children) { for (const child of children) this.insertBefore(child, null); }
  insertBefore(child, before) {
    if (child === before) return;
    child.remove();
    const index = before ? this.children.indexOf(before) : this.children.length;
    assert.ok(index >= 0);
    this.children.splice(index, 0, child);
    child.parentNode = this;
  }
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
  toggleAttribute(key, force) { if (force) this.setAttribute(key, ""); else this.removeAttribute(key); }
  removeAttribute(key) { delete this.attributes[key]; if (key === "value") delete this.value; }
  addEventListener(key, callback) { this.listeners[key] = callback; }
}

function fixture(privateMode = false) {
  let nextFrame = 1;
  const frames = new Map();
  const document = {
    focused: true, visibilityState: "visible", creations: 0,
    hasFocus() { return this.focused; },
    createElement(tag) { this.creations++; return new Element(this, tag); },
  };
  document.body = document.createElement("body");
  const window = {
    requestAnimationFrame(callback) { const id = nextFrame++; frames.set(id, callback); return id; },
    cancelAnimationFrame(id) { frames.delete(id); },
  };
  class IntersectionObserver {
    constructor(callback, options) { this.callback = callback; this.options = options; this.observed = new Set(); }
    observe(element) { this.observed.add(element); }
    unobserve(element) { this.observed.delete(element); }
    disconnect() { this.observed.clear(); }
    report(element, visible) {
      this.callback([{ target: element, isIntersecting: visible, intersectionRatio: visible ? 0.5 : 0 }]);
    }
  }
  const downloadsToggle = document.createElement("button");
  const downloadCount = document.createElement("span");
  const downloadToolbarProgress = document.createElement("svg");
  downloadToolbarProgress.setAttribute("hidden", "");
  Object.defineProperty(downloadToolbarProgress, "hidden", {
    get() { throw new Error("SVGElement does not implement HTMLElement.hidden"); },
    set() { throw new Error("SVGElement does not implement HTMLElement.hidden"); },
  });
  const downloadsPanel = document.createElement("section");
  const downloadsEmpty = document.createElement("p");
  const downloadList = document.createElement("div");
  document.body.append(downloadsToggle, downloadsPanel);
  downloadsPanel.append(downloadList, downloadsEmpty);
  downloadsPanel.hidden = true;
  const acknowledgements = [];
  const openCalls = [], openReplies = [], actionErrors = [];
  let attentionReads = 0;
  let api;
  const runtime = {
    downloads: [],
    openDownload(id, { window: owner }) {
      assert.equal(owner, window);
      openCalls.push(id);
      return new Promise((resolve, reject) => openReplies.push({ resolve, reject }));
    },
    getDownloadAttention({ privateMode: selected }) {
      attentionReads++;
      const downloads = this.downloads.filter(download => download.private === selected);
      return {
        activeCount: downloads.filter(download => ["pending", "downloading"].includes(download.status)).length,
        unseenCompleted: downloads.filter(download => download.status === "complete" && download.attentionToken)
          .map(download => ({ id: download.id, token: download.attentionToken })),
      };
    },
    acknowledgeDownloadsSeen(seen, { window: owner }) {
      assert.equal(owner, window);
      acknowledgements.push(seen);
      for (const item of seen) {
        const download = this.downloads.find(download => download.id === item.id && download.private === privateMode);
        if (download?.attentionToken === item.token) download.attentionToken = null;
      }
      api.schedule();
      return true;
    },
  };
  const factory = new Function("environment", `
    const {document, window, IntersectionObserver, runtime, privateMode,
      downloadsToggle, downloadCount, downloadToolbarProgress, downloadsPanel,
      downloadsEmpty, downloadList} = environment;
    const t = (key, value = {}) => key + JSON.stringify(value);
    const downloadStatusText = download => download.status + ":" + download.currentBytes;
    const showTransientStatus = message => environment.actionErrors.push(message);
    const console = {error() {}};
    const closeCompetingTransientSurfaces = () => {};
    const clearMaterialRipples = () => {};
    const positionAnchoredSurface = (panel, anchor) => { environment.anchor = anchor; };
    ${handlers}
    return {render: renderDownloads, schedule: scheduleDownloadsRender, open: setDownloadsOpen,
      observer: downloadsVisibilityObserver, rows: downloadRows};
  `);
  const environment = { document, window, IntersectionObserver, runtime, privateMode,
    downloadsToggle, downloadCount, downloadToolbarProgress, downloadsPanel, downloadsEmpty, downloadList,
    openCalls, openReplies, actionErrors };
  api = factory(environment);
  return { ...environment, ...api, acknowledgements,
    attentionReads: () => attentionReads,
    frame() {
      for (const [id, callback] of [...frames]) {
        if (frames.delete(id)) callback();
      }
    },
  };
}
const download = (id, overrides = {}) => ({
  id, private: false, fileName: `fixture-${id}.txt`, sourceUrl: "https://example.test/fixture",
  status: "downloading", currentBytes: 25, totalBytes: 100, progress: 25,
  canCancel: true, canRetry: false, attentionToken: null, ...overrides,
});
const complete = (id, token = `completion-${id}`) => download(id, {
  status: "complete", currentBytes: 100, canCancel: false, progress: 100, attentionToken: token,
});
let checks = 0;
function test(name, run) { run(); checks++; process.stdout.write(`PASS ${name}\n`); }

test("empty and historical-only windows hide the button without building hidden panel rows", () => {
  const f = fixture();
  const before = f.document.creations;
  f.render();
  assert.equal(f.downloadsToggle.hidden, true);
  f.runtime.downloads = [complete("old", null)];
  f.render();
  assert.equal(f.downloadsToggle.hidden, true);
  assert.equal(f.document.creations, before);
  assert.equal(f.rows.size, 0);
});
test("pending is active and unknown total never invents a percentage", () => {
  const f = fixture();
  f.runtime.downloads = [download("pending", { status: "pending", totalBytes: 0, currentBytes: 0, progress: null })];
  f.render();
  assert.equal(f.downloadsToggle.hidden, false);
  assert.equal(f.downloadCount.textContent, "1");
  assert.equal(Object.hasOwn(f.downloadToolbarProgress.attributes, "hidden"), false);
  assert.equal(f.downloadToolbarProgress.dataset.indeterminate, "true");
  assert.equal(f.downloadToolbarProgress.style["--download-progress"], undefined);
  assert.match(f.downloadsToggle.title, /^downloads.activeCount/);
  f.open(true);
  const row = f.rows.get("pending");
  assert.equal(row.progress.hidden, false);
  assert.equal(row.progress.value, undefined);
  assert.equal(row.actions.children.length, 1, "pending offers cancel, not remove");
});
test("known progress is byte weighted and mixed unknown totals become indeterminate", () => {
  const f = fixture();
  f.runtime.downloads = [download("a", { currentBytes: 120, totalBytes: 200 }), download("b", { currentBytes: 80 })];
  f.render();
  assert.equal(f.downloadToolbarProgress.style["--download-progress"], "67");
  assert.match(f.downloadsToggle.title, /^downloads.activeProgress/);
  f.runtime.downloads.push(download("unknown", { totalBytes: -1, progress: null }));
  f.render();
  assert.equal(f.downloadToolbarProgress.style["--download-progress"], undefined);
  assert.equal(f.downloadToolbarProgress.dataset.indeterminate, "true");
  assert.match(f.downloadsToggle.title, /^downloads.activeCount/);
});
test("only positive-intersection foreground rows acknowledge original id/token", () => {
  const f = fixture();
  f.runtime.downloads = [complete(1), complete(2)];
  f.render();
  assert.equal(f.downloadsToggle.hidden, false);
  f.open(true);
  assert.equal(f.observer.options.root, f.downloadsPanel);
  f.observer.report(f.rows.get("1").item, false);
  f.frame();
  assert.equal(f.acknowledgements.length, 0);
  f.observer.report(f.rows.get("2").item, true);
  f.frame();
  assert.deepEqual(f.acknowledgements, [[{ id: 2, token: "completion-2" }]]);
  assert.equal(f.runtime.downloads[0].attentionToken, "completion-1");
});
test("background/blurred panels do not mark viewed; focus can acknowledge previously visible rows", () => {
  const f = fixture();
  f.runtime.downloads = [complete("x")];
  f.document.focused = false;
  f.open(true);
  f.observer.report(f.rows.get("x").item, true);
  f.frame();
  assert.equal(f.acknowledgements.length, 0);
  f.document.focused = true;
  f.document.visibilityState = "hidden";
  f.render();
  f.frame();
  assert.equal(f.acknowledgements.length, 0);
  f.document.visibilityState = "visible";
  f.render();
  f.frame();
  assert.equal(f.acknowledgements.length, 1);
});
test("blur or panel close before the acknowledgement frame preserves unread state", () => {
  const f = fixture();
  f.runtime.downloads = [complete("x")];
  f.open(true);
  f.observer.report(f.rows.get("x").item, true);
  f.document.focused = false;
  f.frame();
  assert.equal(f.acknowledgements.length, 0);
  f.document.focused = true;
  f.render();
  f.open(false);
  f.frame();
  assert.equal(f.acknowledgements.length, 0);
  assert.equal(f.observer.observed.size, 0);
  assert.equal(f.downloadsToggle.hidden, false);
});
test("completed icon stays until viewed and stays anchored while the panel is open", () => {
  const f = fixture();
  f.runtime.downloads = [download("x")];
  f.render();
  f.runtime.downloads = [complete("x")];
  f.render();
  assert.equal(Object.hasOwn(f.downloadToolbarProgress.attributes, "hidden"), true);
  assert.equal(f.downloadsToggle.hidden, false);
  f.open(true);
  f.observer.report(f.rows.get("x").item, true);
  f.frame();
  f.frame();
  assert.equal(f.downloadsToggle.hidden, false);
  assert.equal(f.downloadCount.hidden, true);
  f.open(false);
  f.frame();
  assert.equal(f.downloadsToggle.hidden, true);
});
test("progress is frame-coalesced and preserves row/action identity and scroll order", () => {
  const f = fixture();
  f.runtime.downloads = [download("a"), download("b")];
  f.open(true);
  const row = f.rows.get("b"), cancel = row.actions.children[0];
  const reads = f.attentionReads();
  f.runtime.downloads[1].currentBytes = 90;
  f.runtime.downloads[1].progress = 90;
  f.schedule(); f.schedule(); f.schedule();
  f.frame();
  assert.equal(f.attentionReads(), reads + 1);
  assert.equal(f.rows.get("b"), row);
  assert.equal(row.actions.children[0], cancel);
  assert.equal(row.progress.value, 90);
  assert.deepEqual(f.downloadList.children.map(item => item.dataset.downloadId), ["b", "a"]);
});
test("removed/detached rows and the other privacy mode cannot be acknowledged", () => {
  const f = fixture(true);
  f.runtime.downloads = [complete("normal"), { ...complete("private"), private: true }];
  f.open(true);
  assert.equal(f.downloadCount.textContent, "1");
  assert.equal(f.rows.has("normal"), false);
  const stale = f.rows.get("private").item;
  f.runtime.downloads = [];
  f.render();
  f.observer.report(stale, true);
  f.frame();
  assert.equal(f.acknowledgements.length, 0);
});
test("download button is not force-hidden and progress uses a decorative unknown arc", () => {
  const css = readFileSync(new URL("../product/chrome/content/main.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /#toolbar-actions\s*>[^{}]*#downloads-toggle[^{}]*\{[^{}]*display:\s*none/);
  assert.match(css, /data-indeterminate="true"\] circle\s*\{[^}]*stroke-dasharray:\s*25 75/);
  assert.ok(source.includes("onDownloadsChanged: scheduleDownloadsRender"));
  assert.ok(source.includes('window.removeEventListener("focus", scheduleDownloadsRender)'));
});

// Real action handlers and the shared action executor run here, rather than an
// empty performDownloadAction stub: this catches successful opens staying locked.
{
  const f = fixture();
  f.runtime.downloads = [complete("openable")];
  f.open(true);
  const row = f.rows.get("openable"), button = row.buttons.open;
  assert.equal(button.hidden, false);
  assert.equal(button.disabled, true, "Missing files keep Open visible but disabled");
  await button.listeners.click();
  assert.equal(f.openCalls.length, 0);
  f.runtime.downloads[0].canOpen = true;
  f.render();
  assert.equal(row.buttons.open, button);
  assert.equal(button.disabled, false);
  const first = button.listeners.click();
  await button.listeners.click();
  assert.deepEqual(f.openCalls, ["openable"]);
  f.render();
  assert.equal(row.buttons.open, button);
  assert.equal(button.disabled, true);
  f.openReplies.shift().resolve(true); await first;
  assert.equal(button.disabled, false, "A successful open is reusable");
  await button.listeners.click({ detail: 2 });
  assert.equal(f.openCalls.length, 1, "Even a fast first launch does not repeat on double-click");
  const second = button.listeners.click();
  f.openReplies.shift().resolve(false); await second;
  assert.equal(button.disabled, false);
  assert.match(f.actionErrors.at(-1), /^downloads.openFailed/);
  const third = button.listeners.click();
  f.openReplies.shift().reject(new Error("OS association unavailable")); await third;
  assert.equal(button.disabled, false);
  assert.equal(f.actionErrors.length, 2);
  const fourth = button.listeners.click();
  f.runtime.downloads[0].canOpen = false;
  f.render();
  f.openReplies.shift().resolve(true); await fourth;
  assert.equal(button.disabled, true, "An in-flight result cannot reenable a now-missing file");
  f.runtime.downloads[0] = download("openable");
  f.render();
  assert.equal(button.hidden, true, "Retrying or active transfers offer no Open");
  await button.listeners.click();
  assert.equal(f.openCalls.length, 4);
  checks++;
  process.stdout.write("PASS completed-only Open, missing targets, double-click guard, repeat use and failure recovery\n");
}
process.stdout.write(`${checks} focused download toolbar checks passed\n`);
