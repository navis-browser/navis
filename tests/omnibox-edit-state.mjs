import assert from "node:assert/strict";

import { OmniboxEditState } from "../product/chrome/content/omnibox-edit-state.mjs";

const state = new OmniboxEditState();
assert.equal(state.editing, false);
assert.equal(state.rejected, false);
assert.equal(state.preservesInput, false);

state.beginEditing();
assert.equal(state.preservesInput, true);
assert.equal(state.blur(), true);
assert.equal(state.preservesInput, false);

state.beginEditing();
state.reject();
assert.equal(state.editing, true);
assert.equal(state.rejected, true);
assert.equal(state.blur(), false);
assert.equal(state.editing, false);
assert.equal(state.rejected, true);
assert.equal(state.preservesInput, true);

state.beginEditing();
assert.equal(state.blur(), false);
assert.equal(state.preservesInput, true);

state.updateInput();
assert.equal(state.rejected, false);
assert.equal(state.blur(), true);

state.beginEditing();
state.accept();
assert.equal(state.blur(), false);
assert.equal(state.preservesInput, false);

state.reject();
state.reset();
assert.equal(state.editing, false);
assert.equal(state.rejected, false);
assert.equal(state.preservesInput, false);

console.log("Navis omnibox edit-state tests passed.");
