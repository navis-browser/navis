import assert from "node:assert/strict";

import {
  DESKTOP_PROCESS_ISOLATION_MODES,
  desktopProcessIsolationStrategy,
  getDesktopProcessIsolationState,
  initializeDesktopProcessIsolation,
  isDesktopProcessIsolationMode,
  setDesktopProcessIsolationMode,
} from "../embedder/modules/DesktopProcessIsolation.sys.mjs";

const stringPrefs = new Map();
const integerPrefs = new Map();
globalThis.Services = {
  prefs: {
    getStringPref(name, fallback) {
      return stringPrefs.get(name) ?? fallback;
    },
    setStringPref(name, value) {
      stringPrefs.set(name, value);
    },
    setIntPref(name, value) {
      integerPrefs.set(name, value);
    },
  },
};

assert.deepEqual(DESKTOP_PROCESS_ISOLATION_MODES, [
  "full",
  "selective",
  "shared",
]);
assert.equal(desktopProcessIsolationStrategy("full"), 1);
assert.equal(desktopProcessIsolationStrategy("selective"), 2);
assert.equal(desktopProcessIsolationStrategy("shared"), 0);
assert.equal(desktopProcessIsolationStrategy("unknown"), 1);

for (const mode of DESKTOP_PROCESS_ISOLATION_MODES) {
  assert.equal(isDesktopProcessIsolationMode(mode), true);
}
for (const mode of ["", "everything", "nothing", null, 1]) {
  assert.equal(isDesktopProcessIsolationMode(mode), false);
}

assert.deepEqual(getDesktopProcessIsolationState(), {
  available: DESKTOP_PROCESS_ISOLATION_MODES,
  selected: "full",
  active: "full",
  strategy: 1,
  restartRequired: false,
});
assert.equal(initializeDesktopProcessIsolation(), "full");
assert.equal(integerPrefs.get("fission.webContentIsolationStrategy"), 1);
assert.deepEqual(setDesktopProcessIsolationMode("selective"), {
  available: DESKTOP_PROCESS_ISOLATION_MODES,
  selected: "selective",
  active: "full",
  strategy: 1,
  restartRequired: true,
});
assert.equal(integerPrefs.get("fission.webContentIsolationStrategy"), 1);
assert.equal(setDesktopProcessIsolationMode("full").restartRequired, false);
assert.throws(
  () => setDesktopProcessIsolationMode("unsupported"),
  /Unknown Navis process-isolation mode/u
);

console.log("Navis desktop process-isolation pure module tests passed.");
