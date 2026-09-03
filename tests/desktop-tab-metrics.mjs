import assert from "node:assert/strict";

import {
  SUBFRAME_MEMORY_WEIGHT,
  TOP_LEVEL_FRAME_MEMORY_WEIGHT,
  allocateTabMemory,
} from "../embedder/modules/DesktopTabMetrics.sys.mjs";

const mebibyte = 1024 * 1024;
const first = { id: 1 };
const second = { id: 2 };
const missing = { id: 3 };
const estimates = allocateTabMemory(
  new Map([
    [
      first,
      new Map([
        [101, TOP_LEVEL_FRAME_MEMORY_WEIGHT],
        [202, SUBFRAME_MEMORY_WEIGHT],
      ]),
    ],
    [second, new Map([[101, TOP_LEVEL_FRAME_MEMORY_WEIGHT]])],
    [missing, new Map([[303, TOP_LEVEL_FRAME_MEMORY_WEIGHT]])],
  ]),
  new Map([
    [101, 120 * mebibyte],
    [202, 30 * mebibyte],
  ])
);

assert.deepEqual(estimates.get(first), {
  available: true,
  bytes: 90 * mebibyte,
  processCount: 2,
  estimated: true,
});
assert.deepEqual(estimates.get(second), {
  available: true,
  bytes: 60 * mebibyte,
  processCount: 1,
  estimated: true,
});
assert.deepEqual(estimates.get(missing), {
  available: false,
  bytes: 0,
  processCount: 0,
  estimated: true,
});

console.log("Navis desktop tab-metrics pure module tests passed.");
