# Navis internal-page protocol

## Decision

User-facing Navis-owned internal pages use the product protocol `navis://<page>`; they do not use Firefox's `about:<page>` namespace. [The Runtime registry](https://github.com/navis-browser/runtime/blob/main/embedder/modules/DesktopInternalPages.sys.mjs) defines the exact routes and which entries appear in `navis://urls`. Examples below describe page purposes, not platform acceptance:

| URL | Purpose |
| --- | --- |
| `navis://urls/` | Discoverable index of every public internal page present in this build. |
| `navis://support/` | Local diagnostics, with sections for deliberately absent capabilities omitted cleanly. |
| `navis://settings/` | Searchable Navis-owned settings center and default route. |
| `navis://settings/help` | About Navis, existing application/engine version facts and local help information within the settings shell; a bounded, unlisted settings route. |
| `navis://history` | Persistent local browsing history. |
| `navis://bookmarks` | Persistent local bookmark management. |
| `navis://passwords` | Local credential management behind re-authentication where the platform provides it. |
| `navis://downloads` | Durable download presentation when the toolbar surface expands into a full page. |
| `navis://newtab` | Product-owned new-tab document. |

The registry is not a wildcard. A build may add a page only by adding an owned registry entry, capability-matrix owner and regression test. `navis://urls` is generated from the public portion of that registry, so it cannot drift into a handwritten list of pages that are absent from the build.

`navis://settings/` is a bounded route family rather than permission for arbitrary paths. Core recognizes only registered subroutes; every other path fails closed. The settings shell follows Chrome's observable information architecture and Material behavior, but its controls come exclusively from the Navis capability matrix. It does not load, frame or re-skin Firefox preferences or support documents.

`navis://support/` follows the same capability boundary. It is a concise local troubleshooting report for the Navis application, retained Gecko engine paths, graphics/media/network paths Navis actually hosts, current capability switches, and the operating environment. It is not a reconstruction of Firefox `about:support`, nor a place to speculate about future Navis features. Updater/channel state, account sync, Safe Browsing, Push, Widevine, telemetry, crash submission, user-installable add-ons, enterprise policy and other absent product systems have no row or placeholder. Adding a diagnostic requires an implemented capability or a concrete runtime dependency plus a regression that proves the value comes from the active package rather than a hard-coded claim. Development-only interfaces such as WebDriver stay out of the default user report even when the release package retains them for automation.

`navis://settings/help` is the Navis-owned About surface. Its footer is part of the localized page, not imported Firefox chrome: the `en-US` catalogue credits William Varmus and the `zh-CN` catalogue credits 冷曜. The following localized line attributes the Mozilla Gecko open-source project and the other open-source software that makes Navis possible. This preserves the distinction between Navis product authorship and the engine/software provenance; it neither rebrands Gecko as Navis code nor restores a Firefox About page. Footer presence and both language variants are deterministic internal-page test fixtures.

Firefox product pages such as `about:support`, `about:preferences`, `about:addons`, `about:config`, `about:telemetry`, `about:glean` and `about:webrtc` are not aliases. An entered name that is not on the retained engine allowlist or the Navis registry fails closed; it does not fall back to search, DNS or an arbitrary network request.

## Required exception: standards and engine documents

Removing the Firefox product namespace does not mean deleting the Web-platform semantics of `about:blank` or `about:srcdoc`. Those are retained engine documents and are **not** Navis internal pages. Other non-product schemes such as `data:`, `blob:`, `view-source:` and `moz-extension:` likewise do not gain a Navis identity.

Gecko may temporarily retain a private implementation URI while a lower-level engine path is being replaced, but it must never make that URI a public Navis page, advertise it in `navis://urls`, or derive a trusted product indicator from it. A user-visible product surface is complete only after its public entry and presentation use the Navis registry.

Network and certificate error documents are a separate case. The omnibox continues to show the URL whose load failed, rather than a synthetic `navis://error` URL. Core projects an authenticated internal-error document kind and the original failure/security facts to Platform.

## Trust and identity model

The omnibox and site-information panel never classify an internal page by testing address-bar text. Classification is produced below Platform from all of the following facts:

1. Gecko selected Navis's registered protocol handler;
2. that handler resolved an exact, allowlisted page identifier;
3. the resulting channel and document principal carry the Navis internal-page identity; and
4. Core froze that identity into the current navigation/security snapshot.

Platform may then show a dedicated product mark and the label “Navis internal page”. It must not reuse wording or artwork that means an HTTPS peer was authenticated. The identity means that the page code and assets came from the installed Navis package; it does not describe a remote encrypted connection.

A title, path, fragment, user-info field, percent-encoded string, redirect parameter or DOM mutation containing the characters `navis://` can never produce this identity. A web document cannot embed or top-level navigate to a privileged internal page. Browser-owned navigation and links from another authorized internal page are explicit protocol-handler operations.

The unmodified uBlock Origin dashboard remains a `moz-extension://` document. When its principal and extension identity match the immutable application built-in registry, Platform may label it “Navis built-in extension”; it may not label it “Navis internal page”. The generic user-installable extension trust path remains excluded.

Navis deliberately does **not** rename that Gecko ABI to `navis-extension://` and does not display a cosmetic scheme alias. In the pinned ESR the string participates in protocol registration, principals and origin attributes, CSP/CORS, process isolation, workers and persistent-storage keys. The fixed official uBlock Origin 1.74.0 package also uses `browser.runtime.getURL("")` beginning with `moz-extension://` to select its Gecko-specific behavior. Renaming it would therefore either break the unmodified-package requirement and Firefox-extension compatibility, or make the displayed URL disagree with the real security principal. Product provenance is communicated by the authenticated built-in-extension identity, not by falsifying the engine scheme.

## Protocol and page security invariants

- The protocol handler resolves exact page identifiers to packaged resources; it has no DNS, network, file-system or search fallback.
- Each internal page has an explicit privilege/bridge allowlist. Merely using the scheme does not grant every page every Core command.
- Page scripts and styles are packaged. Remote scripts, inline script created from remote data and untrusted HTML injection are forbidden by a restrictive Content Security Policy.
- Internal documents are not normal Web origins. Web content cannot obtain their principal, read them, frame them, open a scripting relationship with them or use them as a privilege trampoline.
- URL query/fragment values and data returned by Core are untrusted display data until validated and escaped. No secret, password or certificate object is serialized into an internal-page URL.
- History, bookmarks, passwords and support bridges return plain immutable records and bounded commands through the public Core contract. They do not expose XPCOM, DOM or Gecko storage objects.
- Unknown pages, malformed authority/path combinations and privilege failures fail closed and receive a designed Navis error state.

## Certificate and site-information presentation

The toolbar's leading identity control opens a Chrome/Material-style site-information surface. HTTPS pages receive connection and certificate facts from Gecko/NSS through the Core security snapshot. A certificate-details action opens a Navis-owned Platform dialog or internal component using sanitized immutable certificate records; it does not restore Firefox's `about:certificate`/certificate-viewer frontend.

On a `navis://` document the same anchor instead shows the Navis internal-page identity, page name and package provenance. It does not invent certificate fields. On a registered built-in extension it shows the distinct built-in extension identity. These three states have separate accessible names and regression fixtures.

## Ownership and tests

- Core/private Gecko binding owns protocol registration, channel/principal classification, exact page identifiers and immutable security facts.
- Shared Core owns the normalized `InternalPage`, `InternalError`, `BuiltInExtension` or ordinary Web security identity exposed to Platform.
- Platform owns page markup, Material components, navigation, focus, accessibility and the toolbar/site-information presentation.

The Linux and Windows release gates must prove at least:

1. every registry entry opens from direct entry and from `navis://urls`;
2. every unknown or malformed `navis://` URL fails closed without network fallback;
3. web content cannot frame, script, fetch or spoof the internal identity;
4. `about:blank` and `about:srcdoc` retain their Web-platform behavior but do not receive a Navis mark;
5. excluded Firefox pages and product aliases are unreachable;
6. internal, built-in-extension, valid HTTPS, HTTP and certificate-error identities are distinguishable in Core, accessibility and screenshots; and
7. pages pass keyboard, scale, theme, long-CJK-value and packaged-resource audits under the common Navis UI standard.
