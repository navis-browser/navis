/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const moduleUrl = new URL("../gecko/mobile/shared/modules/navis/NavisAndroidProduct.sys.mjs", import.meta.url);
const source = (await readFile(moduleUrl, "utf8"))
  .replace(/import \{[\s\S]*?\} from "resource:\/\/gre\/modules\/navis\/DesktopCleanLinks\.sys\.mjs";/u, "")
  .replace("export async function query(", "async function query(");

function fixture() {
  let resolvePermission;
  let collections = 0;
  const browser = {
    browsingContext: {
      currentWindowGlobal: {},
      currentURI: { spec: "https://example.com/editor" },
      sessionHistory: { entries: [{ url: "https://example.com/previous" }], index: 0 },
    },
    asyncPermitUnload: () => new Promise(resolve => { resolvePermission = resolve; }),
  };
  const window = { tab: { linkedBrowser: browser }, closed: false };
  const sandbox = {
    Ci: { nsIClearDataService: {} },
    ChromeUtils: { importESModule(uri) {
      assert.equal(uri, "resource://gre/modules/sessionstore/SessionHistory.sys.mjs");
      return { SessionHistory: { collectFromParent(currentUri, includePrivateData, history) {
        collections++;
        assert.equal(currentUri, browser.browsingContext.currentURI.spec);
        assert.equal(includePrivateData, true);
        return { entries: history.entries, index: history.index };
      } } };
    } },
  };
  vm.runInNewContext(`${source}\nglobalThis.query = query;`, sandbox, { filename: moduleUrl.pathname });
  return {
    browser, window,
    run: () => sandbox.query(window, "session:can-close"),
    decide: value => resolvePermission({ permitUnload: value }),
    collections: () => collections,
  };
}

test("captures the exact engine history only after the user's leave decision", async () => {
  const f = fixture();
  const result = f.run();
  assert.equal(f.collections(), 0);
  f.browser.browsingContext.sessionHistory.entries.push({ url: "https://example.com/editor" });
  f.browser.browsingContext.sessionHistory.index = 1;
  f.decide(true);
  const reply = await result;
  assert.equal(reply.allowed, true);
  assert.deepEqual(JSON.parse(reply.state), {
    version: 1,
    history: { entries: [{ url: "https://example.com/previous" }, { url: "https://example.com/editor" }], index: 1 },
  });
  assert.equal(f.collections(), 1);
});

test("a cancelled beforeunload returns no snapshot", async () => {
  const f = fixture();
  const result = f.run();
  f.decide(false);
  const reply = await result;
  assert.equal(reply.allowed, false);
  assert.equal(reply.state, undefined);
  assert.equal(f.collections(), 0);
});

for (const [description, change] of [
  ["document replacement at the same URL", f => { f.browser.browsingContext.currentWindowGlobal = {}; }],
  ["same-document URL navigation", f => { f.browser.browsingContext.currentURI.spec += "#next"; }],
  ["browser replacement", f => { f.window.tab.linkedBrowser = {}; }],
  ["window closure", f => { f.window.closed = true; }],
]) {
  test(`cancels a restart after ${description} while permission was pending`, async () => {
    const f = fixture();
    const result = f.run();
    change(f);
    f.decide(true);
    assert.equal((await result).allowed, false);
    assert.equal(f.collections(), 0);
  });
}

test("missing history fails rather than saving a guessed stale state", async () => {
  const f = fixture();
  f.browser.browsingContext.sessionHistory = null;
  const result = f.run();
  f.decide(true);
  await assert.rejects(result, /no restorable session history/u);
});

test("oversize engine snapshots fail before a process can restart", async () => {
  const f = fixture();
  f.browser.browsingContext.sessionHistory.entries = [{ data: "x".repeat(1024 * 1024) }];
  const result = f.run();
  f.decide(true);
  await assert.rejects(result, /exceeds its storage limit/u);
});

function preferenceFixture(save) {
  const prefFile = {};
  const nsIFile = {};
  const window = { tab: { linkedBrowser: {} }, closed: false };
  const sandbox = {
    Ci: { nsIClearDataService: {}, nsIFile },
    Services: {
      dirsvc: { get(key, type) {
        assert.equal(key, "PrefF");
        assert.equal(type, nsIFile);
        return prefFile;
      } },
      prefs: { savePrefFile(file) {
        // null would merely schedule an async save on the current ESR.
        assert.equal(file, prefFile);
        save();
      } },
    },
  };
  vm.runInNewContext(`${source}\nglobalThis.query = query;`, sandbox);
  return { window, run: () => sandbox.query(window, "prefs:flush") };
}

test("preference acknowledgement follows the explicit-file blocking save", async () => {
  const calls = [];
  const f = preferenceFixture(() => calls.push("save-returned"));
  const reply = await f.run();
  calls.push("acknowledged");
  assert.equal(reply.flushed, true);
  assert.deepEqual(calls, ["save-returned", "acknowledged"]);
});

test("a reported preference save failure cannot become a success acknowledgement", async () => {
  const f = preferenceFixture(() => { throw new Error("fixture write failure"); });
  await assert.rejects(f.run(), /fixture write failure/u);
});

test("closed hosts cannot flush preferences", async () => {
  let saves = 0;
  const f = preferenceFixture(() => saves++);
  f.window.closed = true;
  await assert.rejects(f.run(), /no live host/u);
  assert.equal(saves, 0);
});

test("the authenticated host admits the fixed preference operation", async () => {
  const host = await readFile(new URL("../gecko/mobile/shared/chrome/navis/host.js", import.meta.url), "utf8");
  const operationSet = host.match(/const PRODUCT_QUERIES = new Set\(\[([\s\S]*?)\]\)/u)?.[1];
  assert.ok(operationSet?.includes('"prefs:flush"'));
  assert.ok(host.includes("!PRODUCT_QUERIES.has(data?.operation)"));
});
