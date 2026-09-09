// SPDX-License-Identifier: MPL-2.0

// Execute the retained Gecko actors and the real Session/Platform close paths.
// No product instance, user tab, rendered document or native app is opened.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const source = read("../runtime/embedder/modules/DesktopEngine.sys.mjs");
const handler = source.slice(source.indexOf("  #handleWindowClose(event) {"),
  source.indexOf("  #bindBrowser(browser) {"));
assert.ok(handler.startsWith("  #handleWindowClose(event) {"));
const child = read("../runtime/gecko/toolkit/actors/BrowserElementChild.sys.mjs");
const parent = read("../runtime/gecko/toolkit/actors/BrowserElementParent.sys.mjs");
const { Session, Child, Parent } = vm.runInNewContext(`
  class Session {
    #browser; #closed = false;
    constructor(browser) { this.#browser = browser; }
    replace(browser) { this.#browser = browser; }
    close() { this.#closed = true; }
    handle(event) { this.#handleWindowClose(event); }
    ${handler}
  }
  ${child.replace("export class", "class")}
  ${parent.replace("export class", "class")}
  ({Session, Child: BrowserElementChild, Parent: BrowserElementParent});
`, { JSWindowActorChild: class {}, JSWindowActorParent: class {} });

function fixture() {
  const requests = [];
  class ChromeEvent {
    constructor(type, options) { Object.assign(this, options, { type, isTrusted: false }); }
    preventDefault() { this.prevented = true; }
    stopPropagation() { this.stopped = true; }
  }
  const window = { closed: false, CustomEvent: ChromeEvent,
    dispatchEvent(event) { requests.push(event); } };
  const browser = { isRemoteBrowser: true, ownerGlobal: window, documentGlobal: window,
    browsingContext: { parent: null },
    dispatchEvent(event) { event.target = this; session.handle(event); } };
  const session = new Session(browser);
  const receiver = new Parent();
  receiver.manager = { browsingContext: { parent: null, embedderElement: browser } };
  const sender = new Child(); sender.manager = { browsingContext: { parent: null } };
  sender.sendAsyncMessage = name => receiver.receiveMessage({ name });
  const event = () => Object.assign(new ChromeEvent("DOMWindowClose", {}), { target: browser });
  return { session, browser, window, requests, sender, receiver, event };
}

{
  const f = fixture();
  f.sender.handleEvent({ type: "DOMWindowClose" });
  assert.equal(f.requests.length, 1, "Actual Toolkit child/parent forwarding reaches Platform");
  assert.equal(f.requests[0].type, "NavisBindingTabCloseRequested");
  assert.equal(f.requests[0].detail.session, f.session);
  const event = f.event(); f.session.handle(event);
  assert.equal(event.prevented, true, "Never let a tab close tear down the containing window implicitly");
  assert.equal(event.stopped, true);
}
for (const block of [
  f => { f.sender.manager.browsingContext.parent = {}; },
  f => { f.receiver.manager.browsingContext.parent = {}; },
  f => { f.session.replace({ ...f.browser }); },
  f => { f.session.replace(null); },
  f => { f.session.close(); },
  f => { f.window.closed = true; },
]) {
  const f = fixture(); block(f);
  f.sender.handleEvent({ type: "DOMWindowClose" });
  assert.equal(f.requests.length, 0, "Ignore subframes, stale embedders and closed owners");
}
{
  const f = fixture();
  const foreign = f.event(); foreign.target = {};
  f.session.handle(foreign); assert.equal(f.requests.length, 0);
  f.browser.isRemoteBrowser = false;
  const event = f.event();
  event.target = { docShell: { browsingContext: f.browser.browsingContext } };
  f.session.handle(event); assert.equal(f.requests.length, 0, "Reject untrusted nonremote close");
  event.isTrusted = true;
  event.target.docShell.browsingContext = { parent: f.browser.browsingContext };
  f.session.handle(event); assert.equal(f.requests.length, 0, "Reject nonremote subframe close");
  event.target.docShell.browsingContext = f.browser.browsingContext;
  f.session.handle(event); assert.equal(f.requests.length, 1);
}
assert.equal(source.split('browser.addEventListener("DOMWindowClose", this.#windowCloseListener);').length, 2);
assert.equal(source.split('browser.removeEventListener("DOMWindowClose", this.#windowCloseListener);').length, 2);

// Reuse the existing Platform path: remove only the requested tab, preserve
// background selection, and retain the deliberate last-tab-closes-window rule.
const main = read("../platform/gecko-chrome/chrome/content/main.mjs");
assert.match(main, /"NavisBindingTabCloseRequested", \(event\) => \{\s*closeSession\(records\.get\(event\.detail\.session\.id\)\)/);
const close = main.slice(main.indexOf("  const closeSession = (record) => {"),
  main.indexOf("  const showTabContextMenu ="));
for (const [count, activeIndex, closeIndex] of [[3, 1, 1], [3, 0, 2], [1, 0, 0]]) {
  const records = new Map();
  let windowClosed = 0, selected = null;
  for (let id = 0; id < count; id++) {
    const session = { id, close() { this.closed = true; } };
    const element = () => ({ remove() { this.removed = true; } });
    records.set(id, { session, tab: element(), panel: element() });
  }
  const record = records.get(closeIndex);
  const invoke = vm.runInNewContext(`${close}; closeSession;`, {
    records, tabHoverRecord: null, hideTabHoverCard() {}, activeRecord: records.get(activeIndex),
    orderedSessionRecords: () => [...records.values()],
    window: { close() { windowClosed++; } },
    runtime: { closeDeveloperTools: async () => {} },
    selectSession(value) { selected = value; }, renderSessionCount() {}, persistSessionState() {}, console,
  });
  invoke(record);
  if (count === 1) {
    assert.equal(windowClosed, 1);
  } else {
    assert.equal(windowClosed, 0); assert.equal(records.size, count - 1);
    assert.equal(record.session.closed, true); assert.equal(record.tab.removed, true);
    assert.equal(record.panel.removed, true);
    assert.equal(!!selected, closeIndex === activeIndex);
  }
}
// Do not infer download intent from URL/file extension or close the source page.
// The retained native helper owns the new-target/opener decision and dispatches
// its close when download handling starts, independent of download completion.
const download = read("../runtime/gecko/uriloader/exthandler/nsExternalHelperAppService.cpp");
assert.match(download, /SetShouldCloseWindow\(\s*loadInfo->GetIsNewWindowTarget\(\)\)/);
const helper = read("../runtime/gecko/docshell/base/nsDSURIContentListener.cpp");
assert.match(helper, /if \(!mShouldCloseWindow\)/);
assert.match(helper, /newBC != mBrowsingContext && newBC && !newBC->IsDiscarded\(\)/);
assert.match(helper, /mBCToClose->Close\(CallerType::System, IgnoreErrors\(\)\)/);
console.log("PASS download tab close: actual Gecko actors→owned Session→Platform, remote/nonremote safety, listener lifetime, neighbours/last-tab; native download-intent boundary retained");
