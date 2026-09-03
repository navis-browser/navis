import assert from "node:assert/strict";

import { setOmniboxValidity } from "../product/chrome/content/omnibox-validity.mjs";

class FakeConstraintControl {
  constructor() {
    this.attributes = new Map();
    this.validationMessage = "";
  }

  setCustomValidity(message) {
    this.validationMessage = message;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }
}

const address = new FakeConstraintControl();
const localizedMessage = "Enter a valid address or search term";

assert.equal(setOmniboxValidity(address, localizedMessage), true);
assert.equal(address.validationMessage, localizedMessage);
assert.equal(address.hasAttribute("aria-invalid"), true);
assert.equal(address.getAttribute("aria-invalid"), "true");

assert.equal(setOmniboxValidity(address), false);
assert.equal(address.validationMessage, "");
assert.equal(address.hasAttribute("aria-invalid"), false);
assert.equal(address.getAttribute("aria-invalid"), null);

assert.throws(() => setOmniboxValidity({}), TypeError);
assert.throws(() => setOmniboxValidity(address, null), TypeError);

console.log("Navis omnibox native validity tests passed.");
