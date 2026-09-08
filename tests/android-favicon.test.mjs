import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createNavisAndroidFaviconBridge, registerNavisAndroidFaviconActors } from
  "../gecko/mobile/shared/modules/navis/NavisAndroidFavicon.sys.mjs";

test("host CSP permits inert favicon data images but not script or network expansion", () => {
  const host = readFileSync(new URL("../gecko/mobile/shared/chrome/navis/host.xhtml", import.meta.url), "utf8");
  const policy = /<\?csp\s+([\s\S]*?)\?>/u.exec(host)?.[1];
  assert.ok(policy, "chrome host must declare a CSP");
  const directives = new Map(policy.split(";").map(value => value.trim()).filter(Boolean)
    .map(value => { const [name, ...sources] = value.split(/\s+/u); return [name, sources]; }));
  assert.deepEqual(directives.get("default-src"), ["chrome:", "resource:"]);
  assert.deepEqual(directives.get("img-src"), ["chrome:", "resource:", "data:"]);
  assert.deepEqual([...directives.keys()].sort(), ["default-src", "img-src"]);
});

function fixture({ width = 32, height = 32, result = "data:image/png;base64,YWJj" } = {}) {
  const manager = { isCurrentGlobal: true };
  const actor = { manager };
  const browser = {
    currentURI: { spec: "https://example.com/" },
    browsingContext: { currentWindowGlobal: manager },
  };
  const decoded = [];
  const images = [];
  const drawings = [];
  const updates = [];
  const document = {
    createElementNS(_ns, tag) {
      if (tag === "img") {
        let resolve, reject;
        const done = new Promise((a, b) => { resolve = a; reject = b; });
        decoded.push({ resolve, reject });
        const image = { naturalWidth: width, naturalHeight: height,
          decode: () => done, removeAttribute: () => {} };
        images.push(image);
        return image;
      }
      assert.equal(tag, "canvas");
      return { getContext: () => ({ drawImage: (...args) => drawings.push(args) }),
        toDataURL: type => { assert.equal(type, "image/png"); return result; } };
    },
  };
  const bridge = createNavisAndroidFaviconBridge({ browser, document, publish: data => updates.push(data) });
  const receive = data => browser.desktopEmbedderFaviconChanged(actor, {
    pageURL: browser.currentURI.spec, iconURL: "data:image/x-icon;base64,YWJj", ...data,
  });
  return { actor, browser, bridge, receive, decoded, images, drawings, updates };
}

test("shared desktop producer is registered once, top-level HTTP(S) only", () => {
  const registrations = [];
  globalThis.ChromeUtils = { registerWindowActor: (...args) => registrations.push(args) };
  registerNavisAndroidFaviconActors();
  registerNavisAndroidFaviconActors();
  assert.equal(registrations.length, 1);
  const [name, options] = registrations[0];
  assert.equal(name, "DesktopFavicon");
  assert.deepEqual(options.matches, ["http://*/*", "https://*/*"]);
  assert.equal(options.allFrames, undefined);
  assert.ok(options.parent.esModuleURI.endsWith("DesktopFaviconParent.sys.mjs"));
  assert.ok(options.child.esModuleURI.endsWith("DesktopFaviconChild.sys.mjs"));
});

test("engine icon data is normalized to bounded PNG preserving aspect ratio", async () => {
  const f = fixture({ width: 80, height: 40 });
  const pending = f.receive();
  f.decoded[0].resolve();
  await pending;
  assert.deepEqual(f.updates, [{ pageURL: "https://example.com/", png: "data:image/png;base64,YWJj" }]);
  assert.deepEqual(f.drawings[0].slice(1), [0, 16, 64, 32]);
});

test("raw URLs, non-images and oversized inputs cannot reach privileged image loading", async () => {
  const f = fixture();
  for (const iconURL of ["https://tracker.example/icon.png", "file:///secret.png",
    "data:text/html,evil", `data:image/png;base64,${"A".repeat(768 * 1024)}`]) {
    await f.receive({ iconURL });
  }
  assert.equal(f.images.length, 0);
  assert.deepEqual(f.updates, []);
});

test("old URL and wrong document actor are rejected before decoding", async () => {
  const f = fixture();
  await f.receive({ pageURL: "https://old.example/" });
  f.actor.manager = { isCurrentGlobal: true };
  await f.receive();
  assert.equal(f.images.length, 0);
});

test("same-URL reload invalidates outstanding icon work", async () => {
  const f = fixture();
  const pending = f.receive();
  f.bridge.invalidate();
  f.decoded[0].resolve();
  await pending;
  assert.deepEqual(f.updates, [{ pageURL: "https://example.com/", png: "" }]);
});

test("newer icon and current document win asynchronous races", async () => {
  const f = fixture();
  const first = f.receive();
  const second = f.receive({ iconURL: "data:image/svg+xml,%3Csvg/%3E" });
  f.decoded[1].resolve();
  await second;
  f.decoded[0].resolve();
  await first;
  assert.equal(f.updates.length, 1);
  const third = f.receive();
  f.browser.browsingContext.currentWindowGlobal = { isCurrentGlobal: true };
  f.decoded[2].resolve();
  await third;
  assert.equal(f.updates.length, 1);
});

test("closed host and failed or overlarge image decode leave the placeholder", async () => {
  for (const scenario of ["close", "bad", "oversized", "output"]) {
    const f = fixture({ width: scenario === "oversized" ? 4097 : 32,
      result: scenario === "output" ? `data:image/png;base64,${"A".repeat(65536)}` : undefined });
    const pending = f.receive();
    if (scenario === "close") f.bridge.close();
    if (scenario === "bad") f.decoded[0].reject(new Error("bad image"));
    else f.decoded[0].resolve();
    await pending;
    assert.deepEqual(f.updates, [], scenario);
    if (scenario === "close") assert.equal(f.browser.desktopEmbedderFaviconChanged, null);
  }
});
