// SPDX-License-Identifier: MPL-2.0
// Exercise the pinned error-page action against the product's real defaults.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { test } from "node:test";

const defaults = readFileSync(new URL("../../platform/gecko-chrome/app/profile/navis.js", import.meta.url), "utf8");
const upstream = readFileSync(new URL("../../runtime/gecko/toolkit/content/aboutNetError.mjs", import.meta.url), "utf8");
const action = upstream.match(/function addCertException\(\) \{[\s\S]*?\n\}/)?.[0];
assert.ok(action, "The pinned certificate-error acceptance action must be available");

async function accept({ privateWindow = false, permanent, reject = false } = {}) {
  const prefs = new Map();
  // Other product preferences contain build-time platform conditionals.
  const certificateDefaults = [...defaults.matchAll(/^pref\("security\.certerrors\.[^\n]+;$/gm)]
    .map(match => match[0]).join("\n");
  vm.runInNewContext(certificateDefaults, {
    pref: (name, value) => prefs.set(name, value),
    locked: true,
  });
  if (permanent !== undefined) prefs.set("security.certerrors.permanentOverride", permanent);
  const overrides = [];
  let reloads = 0;
  const context = vm.createContext({
    RPMIsWindowPrivate: () => privateWindow,
    RPMGetBoolPref(name) {
      assert.equal(typeof prefs.get(name), "boolean", `Missing boolean error-page default: ${name}`);
      return prefs.get(name);
    },
    document: {
      addCertException(temporary) {
        overrides.push(temporary);
        return reject ? Promise.reject(new Error("Native override rejected")) : Promise.resolve();
      },
    },
    location: { reload: () => reloads++ },
  });
  vm.runInContext(`${action}\naddCertException();`, context);
  await new Promise(resolve => setImmediate(resolve));
  return { overrides, reloads };
}

test("ordinary acceptance remembers the exception and reloads", async () => {
  assert.deepEqual(await accept(), { overrides: [false], reloads: 1 });
});

test("a user can restrict exceptions to this session", async () => {
  assert.deepEqual(await accept({ permanent: false }), { overrides: [true], reloads: 1 });
});

test("private acceptance is temporary even with the permanent default", async () => {
  assert.deepEqual(await accept({ privateWindow: true }), { overrides: [true], reloads: 1 });
});

test("a native rejection never reloads the failed page", async () => {
  assert.deepEqual(await accept({ reject: true }), { overrides: [false], reloads: 0 });
});
