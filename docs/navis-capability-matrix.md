# Navis capability matrix

This contract describes the checked-out revision. Git commits and tags identify its version; requirements here are not a claim of completed release validation.

## Purpose

This document defines what Navis promises as a product. It sits above the executable build ledger in `config/runtime-capabilities.json`:

- this matrix decides which browser behaviours are required, deliberately retained, deferred, excluded, or awaiting a recorded capability decision;
- the runtime ledger proves which implementation graphs are actually present or absent; and
- controlled tests, selected WPT coverage, and representative site regressions prove the promised behaviour.

Gecko implementing an API does not automatically make that API a Navis promise. Conversely, a small public Core API does not prove that its Gecko compile closure is small.

## Decision states

| State | Meaning | Required evidence |
| --- | --- | --- |
| **MUST** | Part of the Navis release contract. A regression blocks release. | Deterministic positive tests and native evidence on every applicable required Platform. |
| **HOLD** | Product intent is not yet fixed or an external dependency is unresolved. Keep the capability available until named evidence resolves it. | A recorded product, provider, licensing, or controlled-fixture decision before any graph cut. |
| **RETAIN** | Intentional engine/security substrate, but not a standalone product promise or public Core feature. | Build/source-domain coverage and focused regression where the substrate is consumed. |
| **DEFER** | Outside this release contract. It may remain compiled when a coherent cut has not been justified, but Navis need not provide product UI for it. | It must fail closed if safe use requires product policy or UI that Navis does not provide. |
| **EXCLUDE** | Deliberately absent from every Navis production profile. | Negative configure, graph, package, and runtime checks as applicable. |

`DEFER` is not permission to expose a half-working or unsafe Web API. `HOLD` is temporary and is not permission for an indefinite compatibility bucket. Every HOLD entry must name the evidence or external decision that will resolve it.

## Browser scope

Navis is a general browser. The pinned Gecko ESR defines the Web-platform baseline; Navis does not select individual HTML/CSS/DOM APIs from a handful of sites or reimplement standards that Gecko already provides. Only capabilities explicitly excluded by this matrix depart from that baseline.

Navigation to arbitrary HTTP and HTTPS URLs, including user-entered public or private-network addresses, remains subject to Gecko's URI, origin, certificate, mixed-content and network-security rules. Site samples are regression evidence, not hostname allowlists or a reason to add DNS, IP-address or renderer limits. Ordinary search, sign-in, interactive sites and non-DRM media are in scope; account policy, region restrictions and human challenges are not bypassed.

## Web engine and security

| Capability | State | Navis boundary |
| --- | --- | --- |
| HTML, DOM, CSS, JavaScript, ES modules and WebAssembly | MUST | Retain Gecko's interoperable implementation needed by ordinary browsing, including Gecko-owned default form-control semantics and browser popup paths. Navis does not emulate Select/Date controls in page or Platform overlays. This is not a promise that every WPT passes. |
| Layout, fonts, bidirectional text and CJK shaping | MUST | Chinese and Latin content, selection and editable text must render correctly. |
| HTTPS/TLS, certificate validation and error handling | MUST | Fail closed on invalid security state; no Navis bypass of Gecko validation. |
| HTTP/1.1, HTTP/2, HTTP/3, redirects, compression, DNS and WebSocket | MUST | Ordinary modern-site networking remains Gecko-owned. System proxy support is required where Gecko exposes the native platform path. |
| Fetch/XHR, workers and cross-origin controls | MUST | Includes Web Workers, Service Workers, CORS, CSP, COOP/COEP, Permissions Policy and same-origin enforcement used by modern sites. |
| Fission remote content and site isolation | MUST | Multiprocess browsing, Fission, the content sandbox and dedicated special-content processes are immutable. Settings exposes Full (default: all determinable site origins isolated), Selective (Gecko high-value sites isolated) and Shared (ordinary sites may share Web processes) modes. A change applies only after relaunch so one running process never mixes policies. No mode moves Web content into the parent process or relaxes the sandbox. |
| Linux and Windows content sandbox | MUST | No sandbox relaxation for compatibility. |
| Cookies, cache and durable Web storage | MUST | Cookies, local/session storage, IndexedDB, Cache Storage and Service Worker state must support login and normal site use across application restarts. |
| Clear-all site data operation | MUST | Platform must provide bounded all-site clearing and a current-site cookies/site-data action in the authenticated site-information surface. |
| WebAuthn/passkeys/security keys | MUST | Retain Gecko's WebAuthn path and expose failures as product-safe state. |
| Accessibility tree and keyboard access | MUST | Gecko platform accessibility remains compiled; Navis chrome must also be operable without pointer-only controls. |
| Clipboard, text selection, keyboard, pointer, wheel and IME input | MUST | Includes CJK input and ordinary user-activated copy/paste. |
| Remote security/compatibility data with signature validation | RETAIN | Certificate/security data and justified Remote Settings collections are not telemetry. Endpoints and ownership remain auditable. |
| Gecko WebExtensions execution runtime | MUST | Retain XPIProvider, remote extension lifecycle, content scripts, network interception, extension storage and the bounded `browser.*` profile in `navis-extension-platform.md`. The runtime remains Core-private while generic frozen inventory/action/permission contracts are product-visible. |
| Upstream uBlock Origin | MUST | A reviewed, unmodified official Firefox XPI is loaded from an immutable application-owned location in every production build. It cannot be uninstalled, can be disabled/re-enabled by the user, and is shared by desktop and the Android backend. |
| Safe-browsing/reputation protection | EXCLUDE | Navis has no phishing/malware URL-reputation provider, dangerous-download reputation service, or product claim that such protection exists. Provider/list updates, reputation warnings and provider attribution are absent. TLS validation, certificate errors, same-origin enforcement, sandboxing and the built-in blocker remain separate protections. |
| WebTransport and other Gecko Web-platform APIs | RETAIN | Follow the pinned ESR's supported implementation and exposure policy unless this matrix explicitly records an exclusion. Target-site observation is regression evidence, not a standards-selection mechanism. |
| Geolocation provider | MUST | Core retains Gecko's Web API and per-origin permission boundary; Platform obtains coordinates from the native OS path: XDG Location Portal/GeoClue on Linux and `Windows.Devices.Geolocation` on Windows. Navis sends no location query to a Google/Mozilla endpoint and returns the standard unavailable result when the OS cannot provide a position. |
| Web Notifications and native delivery | MUST | Permission, page-created notifications and Linux/Windows native delivery are required. |
| Web Push | EXCLUDE | The entire content-facing Push API is absent, including `pushManager`, `PushManager`, Push subscription objects/events and Service Worker `onpush`/`onpushsubscriptionchange` entry points. No Mozilla Autopush endpoint, replacement Push service, subscription store or background connection is present. Service Workers, Cache Storage and Notifications remain independent capabilities. |
| WebUSB, WebBluetooth, WebSerial, WebHID and WebMIDI | DEFER | No device-permission/product UI promise. Pinned-ESR source may remain, but content exposure is locked absent until a later capability decision supplies the full permission and chooser contract. |
| WebXR | DEFER | No headset/session product contract. Legacy WebVR and WebXR content exposure are locked absent while their source may remain. |
| Payment Request, federated identity and digital-credential product integrations | DEFER | Their content exposure is locked absent. Ordinary site forms, OAuth redirects/popups and WebAuthn remain available. |

## Graphics, images and media

| Capability | State | Navis boundary |
| --- | --- | --- |
| WebRender/GPU process and software fallback | MUST | Hardware acceleration is retained, with a usable Gecko fallback when a driver is rejected. |
| Canvas 2D, SVG and common CSS graphics | MUST | Required as part of ordinary rendering. |
| WebGL | MUST | Required for common interactive web content. |
| WebGPU | MUST | Retained across WebIDL, DOM, Canvas/IPC, wgpu and composition integration. |
| JPEG, PNG, GIF, WebP, AVIF, ICO and SVG images | MUST | Ordinary image rendering for ordinary browsing. |
| JPEG XL decoding | RETAIN | Follow the selected ESR's compiled/exposed policy. Navis does not carry a separate JPEG XL cut in this profile. |
| HTML audio/video and Media Source Extensions | MUST | Required for ordinary non-DRM media. |
| H.264/AAC, VP9, AV1, Opus and Vorbis playback | MUST | Ordinary non-DRM playback; native codec ABI support remains a platform release gate. |
| Fullscreen media | MUST | Required for ordinary non-DRM media. |
| Encrypted Media Extensions, including ClearKey | EXCLUDE | Navis exposes no EME surface to content: no `navigator.requestMediaKeySystemAccess`, `MediaKeys` family, encrypted-media members on `HTMLMediaElement`, or encrypted key-system branch through Media Capabilities. Ordinary unencrypted HTML media, codecs and Media Source Extensions remain required. |
| Widevine DRM/CDM | EXCLUDE | No Widevine module, identifier advertisement, download/distribution path, DRM product UI or CDM sandbox integration is present. The exclusion is part of the wider EME capability cut rather than a ClearKey-only browser profile. |
| Picture-in-picture, casting and remote playback | DEFER | Optional media UX, not a primary-flow blocker. |
| Camera/microphone capture | EXCLUDE | No real capture backend, permission/indicator actors, or functional `getUserMedia` promise. |
| Screen capture/sharing | EXCLUDE | No capture product surface. |
| RTCPeerConnection, data channels and native libwebrtc | EXCLUDE | Explicit Navis graph exclusion. |

## Navis Core contract

| Capability | State | Navis boundary |
| --- | --- | --- |
| Runtime / Session / View / Delegate contract | MUST | Plain immutable data only; no Gecko, DOM, XPCOM, JNI or Platform UI object crosses the public boundary. |
| Shared Rust lifecycle state | MUST | Identity, ownership, View binding, activation, crash state, invariants and shutdown are one cross-platform source of truth. |
| Navigation commands and normalized state | MUST | Load, stop, reload, back and forward; URL, title, loading, security, history availability, failure and crash state. Gecko remains the source of actual network/navigation facts. |
| Navigation identity and stale-event rejection | MUST | Core must not allow an older asynchronous Gecko event to overwrite a newer public Session state. |
| Multiple live Sessions | MUST | Create, activate, switch and close independent browsing lifecycles. Platform may present them as tabs. Closing a window's sole remaining tab closes that Platform window rather than silently creating a replacement Session; other windows remain unaffected. |
| Live View detach/attach and host-window ownership | MUST | The existing cross-window-safe Core contract remains valid even if the first Platform UI is conservative about exposing it. |
| Focus, visibility and host geometry | MUST | Rendering activation and input focus remain separate. Resize/maximize must update the presented View. |
| Popup/new-session policy | MUST | Gecko user-activation policy runs first; an accepted popup becomes an owned Session and all other paths fail closed. |
| Generic permissions | MUST | Frozen request data, bounded allow/block/dismiss decisions, cancellation and per-Session serialization. Unsupported permission kinds fail closed. |
| Extension-independent public Core | MUST | Rust Core contains no extension identity, lifecycle, action or built-in-specific state. The complete WebExtensions host remains behind the target's Core-private Binding, so another Platform can reuse Core without reproducing desktop extension internals. |
| JavaScript modal, HTTP-auth and before-unload prompts | MUST | Product-owned Delegate UI; no Firefox Login Manager or native Gecko objects across the boundary. |
| File input/native file picker | MUST | Reuse platform picker plumbing without exposing file handles through Core. |
| Downloads | MUST | Start/progress/complete/fail state plus cancel, retry and removal; native destination selection stays platform-owned. |
| Session history and visited-link correctness | MUST | Back/forward and in-session history work; a process-level HistoryDelegate preserves visited-link semantics. |
| Content-process crash detection and recovery | MUST | Recover one Session without taking down Runtime or unrelated Sessions. No crash submission is implied. |
| Immutable build capability facts | MUST | Platform can adapt to a deliberately selected build profile without probing Gecko internals. |
| Persistent browsing history | MUST | Local visit/title/time data, visited-link projection, query, deletion and clear-all behaviour survive restart. Gecko Places or another Core-private store may implement it; native objects do not cross Core. |
| Bookmarks | MUST | Local create, edit, delete, organize and open operations with durable storage. Account synchronization is a separate excluded capability. |
| Cross-restart tab/session restoration | MUST | Persist and restore normal-window Session URLs, ordering and selected state after orderly shutdown and recoverable termination without serializing Gecko objects. |

## Navis Platform contract

| Capability | State | Navis boundary |
| --- | --- | --- |
| Linux x86_64 application | MUST | Development lead and native release gate. |
| Windows x86_64 application | MUST | Same Core behaviour, native launcher/process/sandbox/package replay; Linux evidence is not a substitute. |
| Linux/Windows aarch64-safe Core/API | RETAIN | No contract may encode x86_64 assumptions, but a binary is not promised without a native target matrix. |
| Material/Chrome desktop design system | MUST | Every Navis-owned surface follows the [Platform UI standard](https://github.com/navis-browser/platform/blob/main/docs/navis-ui-standard.md), using Chrome Stable 152.0.7977.64 as the frozen observable reference and its matching Chromium tag for uncertain implementation details. Capability scope remains Navis-owned. |
| Navis-owned UI internationalization | MUST | Every Navis-owned browser-chrome and `navis://` surface ships complete `en-US` and `zh-CN` resources, localized dynamic/accessibility state, system-language selection, explicit persistent language choice, verified English fallback and process-consistent switching. A changed choice exposes a one-click application Relaunch that preserves normal session restoration and respects unsaved-page cancellation; a partial page reload may not create mixed locale state. Core exposes only language-neutral state; OS-owned surfaces, Web content and upstream uBO retain their own localization ownership. See the [Platform localization contract](https://github.com/navis-browser/platform/blob/main/docs/navis-i18n.md). |
| Normal persistent Navis profile | MUST | Stable application-owned profile location; login cookies and Web storage survive restart. Test and developer runs may still use temporary profiles. |
| Arbitrary URL entry/open | MUST | Provide a Chrome-like omnibox flow for arbitrary URLs and non-URL search terms. A primary click that activates the omnibox selects the complete address, while its context menu retains Paste and dynamically offers Paste and go or Paste and search after classifying bounded clipboard text through the same resolver. Ordinary, selected-text and extension searches use the Profile's shared Gecko search service. The search contract includes local suggestions and separately consented provider suggestions, initially off and unavailable in private mode or for addresses/paths. |
| User-controlled automatic clean links | MUST | One global setting, off by default, consumes one bundled, versioned and provider-neutral policy across copied HTTP(S) links and Gecko's eligible top-level cross-site opens and redirects in normal and private browsing. The complete policy works offline: it pins the reviewed Mozilla global snapshot, retains Gecko's packaged exact-host rules and adds a registrable-domain-scoped Alibaba `spm` rule; it does not access `main/query-stripping` at runtime. It adds no dedicated context-menu command, does not rewrite ordinary text/clipboard data, never strips `spm` globally, and retains Gecko's same-site/add-on compatibility exceptions. See `navis-clean-links-policy.md`; Settings links to [Mozilla's Query Parameter Stripping documentation](https://firefox-source-docs.mozilla.org/toolkit/components/antitracking/anti-tracking/query-stripping/index.html) as a mechanism reference rather than a live-provider promise. |
| Back, forward, reload/stop and loading feedback | MUST | Controls reflect normalized Core state and remain responsive during failure/cancellation. |
| Session/tab presentation | MUST | Create, switch and close Sessions; accepted popups are visible and owned. |
| History and bookmark surfaces | MUST | Query/open/delete history and create/edit/delete/organize bookmarks without exposing Gecko storage objects. |
| Password-manager and autofill surfaces | MUST | Save/update prompts, credential selection, fill, reveal and deletion are explicit Navis UI, including a way to inspect and clear local credentials. |
| Startup Session restoration | MUST | Platform restores the last normal Session set through Core and never restores private Sessions. |
| Native window behaviour | MUST | Move, resize, minimize, maximize, close, fullscreen, DPI scaling and View geometry must work on both Platforms. |
| Permission, prompt, download and crash surfaces | MUST | Every required Core Delegate has a usable keyboard-accessible Platform presentation. |
| Native file/save pickers | MUST | Linux portal/native integration and Windows native integration. |
| Navis internal-page protocol and registry | MUST | Navis-owned pages use exact allowlisted `navis://<page>` entries, with `navis://urls` generated from the public registry. Core authenticates handler/channel/principal identity; unknown entries fail closed and Firefox product `about:` aliases are absent. Standards-required `about:blank` and `about:srcdoc` remain non-Navis engine documents. See `navis-internal-pages.md`. |
| Navis settings and help pages | MUST | `navis://settings/` is the Navis-owned, searchable Material settings center. Bounded subroutes use `navis://settings/<route>`; `navis://settings/help` owns existing application/engine version facts and local help information. Neither route aliases or embeds Firefox `about:preferences`/`about:support`, and settings are generated only from retained capabilities. |
| Basic security identity/error presentation | MUST | Current origin/security state and load failure must be visible; invalid certificates must not become a blank or silent success. Valid HTTPS, HTTP, Navis internal, immutable built-in-extension and internal-error identities are distinct immutable Core states. The site-information/certificate UI follows the Chrome/Material reference and does not restore Firefox's certificate-viewer frontend. |
| Generic extension management/action surfaces | MUST | Built-ins and user-installed extensions share `navis://extensions/`, toolbar actions, remote popups and options pages. uBlock Origin is required, enabled and pinned by default; its unmodified popup owns per-site controls, its ordinary manager entry owns enable/disable, and Navis adds no dedicated blocker UI or private adapter. |
| Desktop page developer tools | MUST | F12 toggles Gecko's complete page toolbox for the active Session, including Inspector, Console, Debugger, Network, Style Editor, Performance, Memory, Storage, Accessibility, Application and DOM tools. A Core-private local-target/host adapter preserves dock/window behavior without exposing Gecko objects or restoring Firefox browser chrome. Gecko retains the toolbox's native styling, protocol, panel, keyboard and accessibility semantics. Browser Toolbox, parent-process debugging and privileged WebDriver authority remain separate explicit security boundaries. |
| Packaging from an explicit manifest | MUST | Release artifacts exclude development/test products and pass native package/runtime audits. Built-in installation is not required. |
| Navis context menus | MUST | Platform presents one Material-skinned Gecko `menupopup` for Web content, tabs and the omnibox. Gecko owns popup lifecycle, focus, keyboard/accessibility semantics and display-edge constraint; Navis owns product styling. The Core-private Gecko Binding performs content hit testing and bounded commands, then exposes only frozen facts and command identifiers; DOM/XPCOM objects and Firefox product-menu code never cross the boundary. Menus include only actions supported by current capabilities and fail closed for unoffered or stale requests. |
| Multiple user profiles/profile manager | MUST | One editable identity per profile, UUIDv4 creation with accent, real data separation, default/remove/launch and normal/extensions-disabled restart. Existing profiles must retain their data. |
| Private-browsing product mode | MUST | Platform can create visibly distinct private Sessions/windows whose history, cookies, storage, form data and restoration state do not enter the normal persistent stores. |
| Installer and desktop-shortcut creation | DEFER | Portable artifacts remain acceptable; Navis must not create a Firefox executable or shortcut. |
| macOS x86_64/aarch64 Platform | DEFER | Separate future native target. |
| Android aarch64 application | MUST | Native Kotlin/Material Platform over the unchanged shared Rust Core through the Navis-owned `:navis-runtime-android` module and a thin JNI target binding. The product graph must not contain the `:geckoview` project/AAR or GeckoView Runtime/Session/View owners; selected Gecko Android process, surface, compositor, APZ, IME and accessibility primitives remain reusable engine internals. The application must cover browser, persistence, private, extension and bilingual UI capabilities. Developer mode opens the original Gecko page toolbox inside the phone application; a remote-debugging switch is not a substitute. Local extension management follows the same signed-XPI policy as desktop, without inventing an unsigned development-install capability. See the [Runtime Android contract](https://github.com/navis-browser/runtime/blob/main/docs/android-runtime.md) for the focused manual-handoff gate, distinct from full release certification. |

## Navis browser-shell interaction requirements

These rows are explicit requirements, not optional visual polish. They refine the broader Platform and design-system rows above and each requires a deterministic Linux/Windows replay.

| Capability | State | Navis boundary |
| --- | --- | --- |
| Mutually exclusive transient browser surfaces | MUST | Opening or interacting with another browser/content surface closes any unrelated anchored bubble, popup or menu. Side panels are persistent surfaces and follow their own lifecycle. A content context-menu request, page click, tab switch, navigation, focus transfer or competing popup cannot leave stale site information or another transient surface visible. |
| Trustworthy loopback site identity | MUST | Authenticated `localhost`, loopback IPv4 and loopback IPv6 pages receive a trustworthy local-page identity with the reason “This page is stored on your computer”; the state must come from the actual principal/URI and never from display text spoofing. Certificate and insecure remote-origin rules remain unchanged. |
| Integrated browser frame and adjacent new-tab affordance | MUST | Navis owns a Chrome-like integrated tab strip instead of showing a separate system title bar above it. Native move/resize/minimize/maximize/close semantics remain functional. The new-tab affordance sits immediately after the final visible tab rather than being pinned to the far edge. |
| Anchored native-control dismissal on content scroll | MUST | Browser-owned Select/Date/Time and comparable anchored popups close when their content anchor scrolls, navigates, disappears or loses the applicable interaction context; a stale popup may not float over unrelated content. Gecko still owns value/event/focus/accessibility semantics. |
| Chrome-referenced browser-control styling | MUST | Browser-owned form-control popup presentation and media controls, including video controls, use the frozen Chrome/Chromium reference for density, state and visual treatment while retaining Gecko engine semantics and trusted ownership. |
| Connected active-tab and toolbar geometry | MUST | Highlighted and hovered horizontal tabs visually join the omnibox toolbar: normal upper radii and symmetric outward-extending lower shoulders replace isolated four-rounded-corner pills. Shoulder-width leading and trailing gutters prevent endpoint tabs from colliding with native rounded-window clipping. Inactive, focus, loading and overflow states retain clear boundaries. |
| Dedicated history, bookmarks and passwords routes | MUST | `navis://history`, `navis://bookmarks` and `navis://passwords` are first-class management pages in addition to compact panels; they use the shared internal-page shell and never alias Firefox pages. |
| Disabled context-menu presentation | MUST | Unavailable commands remain accessible as disabled but have visibly reduced text/icon emphasis and cannot be mistaken for enabled commands in light, dark, private or forced-color modes. |
| Rounded context-menu elevation | MUST | Menu background, clipping, border and elevation share the same rounded geometry; no rectangular shadow or unrounded backing surface may protrude around a rounded menu. |
| Product-panel text context actions | MUST | History, bookmarks, passwords, downloads and comparable Navis panels provide at least the applicable text selection/copy/select-all context actions without exposing privileged DOM objects or enabling unsupported feature commands. |
| Rounded content-surface and panel boundary | MUST | The presented Web content is a Chrome/Edge-like rounded surface within the browser frame where layout permits. Opening a side panel preserves a clear, soft boundary between content and panel without obscuring page interaction or breaking View geometry. |
| Bookmark bar | MUST | A local bookmark bar can be shown/hidden, renders durable bookmark ordering and folders, opens entries through Core Sessions and has coherent empty/overflow/context states. No account sync is implied. |
| Per-site data clearing in site information | MUST | The authenticated site-information surface reports and clears cookies/site data for the current site through a bounded Core operation, updates visible state after completion and cannot clear a spoofed/display-only origin. |
| Navis new-tab and startup home | MUST | `navis://newtab` is a Navis-owned home/new-tab page and the default for application startup and new Sessions unless restored Sessions or an explicit user startup choice applies. A network example page is never the built-in default. |
| Settings information architecture and omnibox search providers | MUST | Settings-only SPA routes in order: search, privacy, appearance, downloads, help; unrelated management pages remain separate. Named search providers support add/edit/reorder/delete with four non-deletable built-ins and a default dropdown. Desktop and Android share Gecko engine storage with one-time legacy migration, optional suggestion URLs and explicit Profile consent. Support expands real engine/platform diagnostics without advertising absent services. See `navis-search-service.md` for the search contract and evidence boundaries. |

## Product services and deliberate exclusions

| Capability | State | Navis boundary |
| --- | --- | --- |
| Product telemetry/observability | EXCLUDE | No Glean/FOG, legacy Telemetry, DAP, Gecko Trace, collection, archive or submission path. Constant-off compatibility ABI is not a product capability. |
| Local ML/ONNX/llama/translations | EXCLUDE | No local inference runtime or Firefox ML feature graph. |
| Firefox frontend and Firefox product services | EXCLUDE | Navis owns its Platform UI and product policy. |
| Navis Account and cross-device data synchronization | EXCLUDE | Navis stores history, bookmarks, passwords and session state locally but provides no account, cloud upload or cross-device synchronization. This does not forbid selectively reusing local-only Gecko or Application Services storage components. |
| Gecko WebExtensions execution runtime | MUST | Navis uses Gecko's mature remote execution/runtime path for both application built-ins and signed user-selected local extensions. Extension work must not run in the Navis parent process. |
| Bundled upstream uBlock Origin package and baseline rules | MUST | A reviewed official Firefox release XPI and its bundled data are version/hash-owned production inputs. Navis updates extension code through an application release. |
| uBlock Origin filter-list updates | MUST | Use upstream uBO's own asset/list updater and normal controls rather than a Navis rules service. Its remote sources, cache and default-list licences remain auditable; auto-update can be disabled through uBO policy. |
| uBlock Origin dashboard and rules controls | RETAIN | The unmodified package retains its own dashboard, trusted-site, list and custom-rule capabilities. Navis guarantees the global/per-site on/off surface but does not fork that UI. |
| Local user-installed extensions and standalone manager | MUST | `navis://extensions/` is the sole install/management entry point for signed local XPI files. It provides permission confirmation, inventory, enable/disable, local update, uninstall, options and action pinning while preserving immutable built-ins. Web/MIME install, foreign-directory sideloading, an online store and autonomous extension-code updates remain absent. |
| Privileged and Firefox-product extension integrations | EXCLUDE | WebExtension Experiments, native messaging, PKCS#11, DevTools/profiler extensions, Firefox themes/sidebar, containers/tab groups, Accounts/sync, recommendations and store/abuse services are outside this compatibility profile. Required unsupported capabilities reject installation visibly. |
| Local password manager and autofill | MUST | Save, update, select, fill, reveal and delete local credentials behind explicit Platform UI and OS-appropriate at-rest protection. A Gecko password-management substrate may remain Core-private; no credential object crosses Core and no account sync is implied. |
| Printing and print preview | EXCLUDE | Not part of the current runtime graph or product contract. |
| Built-in updater/background maintenance | EXCLUDE | Security releases use externally replaced, explicitly packaged artifacts until a Navis-owned updater is designed. |
| Crash submission | EXCLUDE | Local content-process recovery is required; submission is not. |
| Restricted WebDriver BiDi and Marionette content automation | RETAIN | Production packages retain dormant loopback-only protocol endpoints so the exact release runtime can be automated. Desktop Embedder permanently denies parent/chrome scope and registers no extension install, uninstall or mutation command. |
| geckodriver, privileged WebDriver surfaces and test products | EXCLUDE | No geckodriver or test executable is packaged; parent-process/chrome automation, WebDriver extension mutation and test harness products remain absent. The retained desktop page DevTools closure does not grant the production WebDriver endpoint any additional authority. |
| PDF viewer | DEFER | PDF navigation may download/open externally; an integrated PDF.js-style product is not a release requirement. |
| Reader mode, translation and Firefox content recommendations | DEFER | Not part of the current product contract. |
| Spellcheck | RETAIN | Keep the ordinary text-editing substrate for now; it is not an independent product feature or a compile-time optimization target. |

Navis selects an unmodified official Firefox release of upstream uBlock Origin, not a new Rust filtering engine or a Navis-maintained behavioral fork. Each upgrade must pin the official artifact and source revision, verify its identity and required APIs, and record GPLv3, bundled/default-list licence and source- offer compliance. If integration requires modifying the XPI, it is no longer the accepted "upstream original" and requires a new product decision.

## Evidence and change rules

1. Every MUST entry needs an owner in Core, Platform, Gecko substrate, or a named Delegate, plus a deterministic regression test. Platform behaviour requires a native run on Linux and Windows x86_64 or Android aarch64 when that target owns the behavior.
2. Target-site checks supplement controlled fixtures. They never replace Fission, sandbox, permission, crash, storage, media or security tests.
3. The pinned Gecko ESR's upstream WPT and engine test results are the standards baseline. Navis runs focused differential WPT/fixtures where a semantic port, capability gate, preference or target backend can change behaviour; it does not derive a reduced standards set from target-site traffic.
4. A HOLD capability stays in the build while evidence is collected. Resolving it requires a recorded matrix change and, when excluded, a coherent graph cut plus negative tests.
5. A RETAIN entry may not silently grow into a public Navis capability. A DEFER entry may not silently become a release dependency.
6. An EXCLUDE entry is not complete when disabled by a preference alone. Its public API and executable build/package path must be absent unless this matrix explicitly names a narrow inert compatibility surface. Source may remain in the vendored Gecko tree without being configured into a Navis production graph.
7. No capability is cut solely because its public UI is absent. Compile-time work follows the measured transitive graph, and low-yield cuts are not presented as build-speed improvements.
8. Security invariants override site compatibility: Navis does not disable TLS validation, same-origin enforcement, Fission, or sandboxing to pass a target site.
9. The built-in uBlock Origin package requires deterministic request-filtering, cosmetic-filtering, disabled-state, per-site override, ruleset-integrity and target-site regressions through its ordinary extension surfaces. Tests must also prove that it cannot be uninstalled and that unauthorized profile, Web or sideloaded XPIs cannot start. Signed local XPIs deliberately admitted through the manager remain supported. The unmodified popup, dashboard, custom-list import, updater/cache/compiler path, rule replacement and restart persistence are exercised without privileged parent-process script injection.
