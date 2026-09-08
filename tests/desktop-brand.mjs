import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import {
  NAVIS_MARK, NAVIS_BRAND_STYLE, NAVIS_BRAND_SCRIPT, renderNavisMark,
  installBrandEntrances,
} from "../../platform/gecko-chrome/chrome/content/brand.mjs";
import { createIcon, setIcon } from "../../platform/gecko-chrome/chrome/content/design-system.mjs";
import { renderNavisInternalPage } from "../../platform/gecko-chrome/chrome/content/internal-pages.mjs";
import { getNavisInternalPage, getNavisInternalPages } from "../../runtime/embedder/modules/DesktopInternalPages.sys.mjs";

const logoSource = await readFile(new URL("../../platform/docs/navis-logo-shapes.html", import.meta.url), "utf8");
const canonical = JSON.parse(await readFile(new URL("../../platform/gecko-chrome/branding/navis-mark.json", import.meta.url), "utf8"));
assert.deepEqual(NAVIS_MARK, canonical, "Desktop consumes the generated canonical native-platform branding model");
assert(Object.isFrozen(NAVIS_MARK) && Object.isFrozen(NAVIS_MARK.seams.paths) && Object.isFrozen(NAVIS_MARK.gradients.contour.stops));
for (const path of [NAVIS_MARK.channel.path, ...NAVIS_MARK.seams.paths]) {
  assert(logoSource.includes(path), "Canonical paths must match the supplied logo");
}
const markup = renderNavisMark("navis-test", { animated: true });
assert.match(markup, /viewBox="0 0 220 220"/);
assert.match(markup, /x="22" y="22" width="176" height="176" rx="40"/);
assert.match(markup, /transform="rotate\(42 110 110\)"/);
assert.match(markup, /stroke-width="22\.5"/);
assert.equal((markup.match(/stroke-width="6\.8"/g) || []).length, 2);
for (const color of ["#448aff", "#536fe7", "#7355db", "#4082f0", "#4e68d9", "#6c50ce"]) assert(markup.includes(color));
assert.doesNotMatch(markup, /var\(--accent|<script|<image|<use|<foreignObject|onload=|href=/);
assert.throws(() => renderNavisMark('navis-x" onload="bad'), /application-owned/);
assert.doesNotMatch(renderNavisMark("navis-static"), /data-brand-entrance/);
assert.match(markup, /<filter/);
assert.match(markup, /frostDiffuse/);
assert.match(markup, /frostFog/);
assert.match(markup, /-rim/);
assert.doesNotMatch(renderNavisMark("navis-compact", { variant: "compact" }), /<filter|feDropShadow|frostDiffuse|frostFog/);
assert.throws(() => renderNavisMark("navis-compact", { variant: "compact", animated: true }), /always static/);
new vm.Script(NAVIS_BRAND_SCRIPT);

function fakeDocument() {
  const document = { createElementNS(namespaceURI, localName) {
    return { namespaceURI, localName, ownerDocument: document, attributes: {}, children: [],
      classList: { values: new Set(), add(value) { this.values.add(value); }, contains(value) { return this.values.has(value); } },
      setAttribute(key, value) { this.attributes[key] = value; },
      getAttribute(key) { return this.attributes[key]; },
      append(node) { this.children.push(node); }, prepend(node) { this.children.unshift(node); },
      replaceChildren() { this.children = []; },
    };
  } };
  return document;
}
function walk(node) { return [node, ...node.children.flatMap(walk)]; }
const document = fakeDocument();
const first = createIcon(document, "navis");
const second = createIcon(document, "navis");
assert.equal(first.attributes.viewBox, "0 0 220 220");
assert(walk(first).every(node => node.namespaceURI === "http://www.w3.org/2000/svg"), "All nested geometry must be SVG even in XHTML chrome");
assert(!walk(first).some(node => node.localName === "filter" || node.localName === "feDropShadow"), "Repeated 16–24px toolbar/tab icons keep canonical geometry without per-tab filter stacks");
const firstIds = walk(first).map(node => node.attributes.id).filter(Boolean);
const secondIds = walk(second).map(node => node.attributes.id).filter(Boolean);
assert(firstIds.every(id => !secondIds.includes(id)), "Multiple toolbar/tab marks cannot collide in gradient or clip IDs");
assert.doesNotMatch(JSON.stringify(first.attributes), /entrance/);
const container = { ownerDocument: document, children: [first] };
setIcon(container, "globe");
assert.equal(first.attributes.viewBox, "0 0 20 20", "Switching from internal identity restores functional-icon viewport");
assert.equal(first.attributes["data-icon"], "globe");
assert(!walk(first).some(node => node.localName === "defs"));
setIcon(container, "navis");
assert.equal(first.attributes.viewBox, "0 0 220 220");
assert(walk(first).some(node => node.attributes.d === NAVIS_MARK.channel.path));
const reusedChildren = first.children;
setIcon(container, "navis");
assert.equal(first.children, reusedChildren, "Repeated Session identity updates reuse brand filters and gradients");
assert.throws(() => createIcon(document, "unknown"), /Unknown Navis icon/);

const diagnostics = { application: [], engine: [], graphics: [], media: [], network: [], capabilities: [], system: [] };
for (const locale of ["en-US", "zh-CN"]) {
  for (const [id, route] of [["newtab", ""], ["settings", "search"], ["settings", "help"], ["support", ""], ["credits", ""], ["urls", ""]]) {
    const page = getNavisInternalPage(id, route);
    const html = renderNavisInternalPage({ page, pages: getNavisInternalPages(), diagnostics, nonce: "brand-test", locale });
    assert.match(html, /class="navis-brand-mark"/);
    assert.doesNotMatch(html, /class="(?:newtab|about)-mark"[^>]*>N</);
    assert.match(html, /img-src 'none'/, "Branding does not open a new image/network CSP permission");
    assert.doesNotMatch(html, /<script[^>]+src=/);
    const ids = [...html.matchAll(/\bid="(navis-[^"]+)"/g)].map(match => match[1]);
    assert.equal(new Set(ids).size, ids.length, "One shared settings SPA must not duplicate SVG IDs");
    const brandScript = [...html.matchAll(/<script nonce="brand-test">([\s\S]*?)<\/script>/g)].find(match => match[1].includes("function installBrandEntrances"));
    assert.equal(Boolean(brandScript), id === "newtab" || id === "settings");
  }
}
const shell = await readFile(new URL("../../platform/gecko-chrome/chrome/content/main.mjs", import.meta.url), "utf8");
assert.match(shell, /const useFavicon = icon === "globe" && Boolean\(state\.favicon\)/);
assert.match(shell, /if \(hasVisibleLoading\(state\)\) \{\s+icon = "loading";/, "Ordinary web loading retains its semantic progress icon");
const jar = await readFile(new URL("../../platform/gecko-chrome/chrome/jar.mn", import.meta.url), "utf8");
assert.match(jar, /content\/brand\.mjs\s+\(content\/brand\.mjs\)/);

function eventTarget(extra = {}) {
  const events = new Map();
  return { ...extra, events,
    addEventListener(type, handler) { events.set(type, handler); },
    removeEventListener(type, handler) { if (events.get(type) === handler) events.delete(type); },
    emit(type, value = {}) { events.get(type)?.(value); },
  };
}
function lifecycle({ hidden = false, reduced = false, observerAvailable = true } = {}) {
  const mark = { dataset: { brandEntrance: "pending" } };
  const media = eventTarget({ matches: reduced });
  const doc = eventTarget({ hidden, querySelectorAll: () => [mark] });
  let callback, observer;
  const owner = eventTarget({ document: doc, matchMedia: () => media,
    IntersectionObserver: observerAvailable ? class {
      constructor(handler) { callback = handler; observer = this; this.observed = new Set(); }
      observe(target) { this.observed.add(target); }
      unobserve(target) { this.observed.delete(target); }
      disconnect() { this.disconnected = true; this.observed.clear(); }
    } : undefined,
  });
  installBrandEntrances(owner);
  return { mark, media, doc, owner, get observer() { return observer; },
    intersection(value) { callback?.([{ target: mark, isIntersecting: value }]); },
    complete() { doc.emit("animationend", { animationName: "navis-mark-counterflow", target: { closest: () => mark } }); },
  };
}
const entrance = lifecycle();
assert.equal(entrance.mark.dataset.brandEntrance, "pending");
entrance.intersection(false);
assert.equal(entrance.mark.dataset.brandEntrance, "pending", "Hidden SPA about content does not consume its one entrance");
entrance.intersection(true);
assert.equal(entrance.mark.dataset.brandEntrance, "playing");
entrance.complete();
assert.equal(entrance.mark.dataset.brandEntrance, "done");
entrance.intersection(true);
assert.equal(entrance.mark.dataset.brandEntrance, "done", "No replay after scrolling or reopening an SPA section");
assert.equal(entrance.observer.observed.size, 0);
entrance.owner.emit("pagehide");
assert(entrance.observer.disconnected);
assert.equal(entrance.doc.events.size, 0);
assert.equal(entrance.media.events.size, 0);
for (const settings of [{ reduced: true }, { observerAvailable: false }]) {
  const state = lifecycle(settings);
  assert.equal(state.mark.dataset.brandEntrance, "done", "Reduced motion or unsupported observer always leaves a visible static mark");
  assert.equal(state.doc.events.size, 0);
}
const background = lifecycle({ hidden: true });
background.intersection(true);
assert.equal(background.mark.dataset.brandEntrance, "pending");
background.doc.hidden = false;
background.doc.emit("visibilitychange");
assert.equal(background.mark.dataset.brandEntrance, "playing");
background.doc.hidden = true;
background.doc.emit("visibilitychange");
assert.equal(background.mark.dataset.brandEntrance, "done", "Backgrounding stops pending animated work without replay");
const offscreen = lifecycle();
offscreen.intersection(true);
offscreen.intersection(false);
assert.equal(offscreen.mark.dataset.brandEntrance, "done", "Hiding/scrolling the mark stops its animation");
const preference = lifecycle();
preference.intersection(true);
preference.media.matches = true;
preference.media.emit("change");
assert.equal(preference.mark.dataset.brandEntrance, "done");
assert.match(NAVIS_BRAND_STYLE, /prefers-reduced-motion: reduce/);
assert.match(NAVIS_BRAND_STYLE, /720ms/);
assert.match(NAVIS_BRAND_STYLE, /920ms 420ms/);
assert.doesNotMatch(NAVIS_BRAND_STYLE, /infinite/);
assert.doesNotMatch(NAVIS_BRAND_SCRIPT, /setTimeout|setInterval|requestAnimationFrame|getBoundingClientRect/);
console.log("PASS desktop brand: canonical geometry, unique XHTML SVG namespaces/IDs, localized internal surfaces/CSP, favicon/loading boundaries, one-shot visible entrance, reduced-motion and hidden lifecycle");
