# Navis local extension platform

This contract describes the checked-out revision. Git commits and tags identify its version; package and native validation results are recorded separately.

## Outcome

Navis includes a general local WebExtensions host and no online extension store. The user installs an extension package deliberately from the standalone `navis://extensions/` manager.

The pinned Gecko WebExtensions runtime remains the execution engine. Navis owns product policy, the Core-private Binding for inventory/actions/popups, permission presentation and Platform UI. The public Rust Core has no extension or built-in-specific contract. Extension objects, Gecko browser elements and AddonManager objects never cross that boundary.

This is a compatibility profile, not a claim that every Firefox add-on works. An add-on is supported only when all APIs and product surfaces it requires are in the profile below.

## Package classes

| Class | Location | Install/update owner | User controls |
| --- | --- | --- | --- |
| Application built-in | Immutable application package and reviewed registry | Navis release | Enable or disable; cannot uninstall or replace independently |
| User extension | Active profile extension location | User through `navis://extensions/` | Install, inspect, enable, disable, replace with a newer local XPI and uninstall |

uBlock Origin remains the required, default-on application built-in and its ordinary toolbar action is pinned on a fresh profile. Built-ins and user extensions share the same remote execution, action, popup, options, permission and lifecycle machinery. The distinction is release policy metadata rather than a separate runtime.

## One lifecycle and one UI path

`DesktopExtensionManager` is the sole authority for extension identity, installation, update, enablement, private-window policy and removal. Built-ins are identified only by the immutable application registry. That registry says whether each package is required and whether its normal toolbar action starts pinned; it cannot name executable adapters or register privileged product features.

The same generic toolbar, popup host, options-page path and manager serve every extension. uBlock Origin's unmodified popup owns its per-site controls. Its global enable switch is the ordinary built-in lifecycle switch in `navis://extensions/`, and the user may unpin its action. Navis neither reads the popup DOM nor speaks uBO's private message protocol. Additional built-ins use another registry entry rather than another Core/Binding/Platform vertical.

## Installation and update boundary

- Stable Navis accepts only XPI packages whose signature validates against the built-in Mozilla add-on signing roots. Signature enforcement is a build fact, not a mutable user preference.
- Installation starts only from an explicit file selection in `navis://extensions/`. Web pages, MIME navigation, `InstallTrigger`, `mozAddonManager`, command-line side loading and foreign profile/system directory scanning are not installation entry points.
- Before mutation, Platform shows the extension identity, version, source file, required API permissions and required host origins. The user must confirm.
- Installing the same ID is an explicit local-package update. The manager shows the installed and candidate versions and rejects a downgrade unless a future product decision defines a recovery flow.
- Navis has no extension catalogue, recommendations, store account, remote package discovery or autonomous extension-code update service. Built-ins are updated with Navis; user extensions are updated from another signed local XPI selected by the user.
- A failed, cancelled or incompatible install leaves the previous installed version and its enabled state intact.
- If an older profile contains an extension discovered outside the manager, Navis displays it as a blocked side-load and keeps it disabled. The user may remove it, or explicitly select the same signed XPI in the manager to adopt it; it cannot otherwise be enabled, opened or invoked.

## Execution and responsiveness

`extensions.webextensions.remote` is locked on in every production profile. Extension backgrounds, pages and browser-action popups must not execute in the Navis parent process. A cold uBlock Origin rules compilation is valid extension work but may not block tab chrome, navigation controls or web-content frames.

The toolbar and popup host are generic:

1. Gecko registers an extension `action`/`browser_action` with the Core-private Binding backend.
2. Binding projects a frozen action snapshot containing only extension ID, presentation state and tab association.
3. Platform renders pinned actions and asks the backend to invoke one under the originating user gesture.
4. A declared popup is hosted as a remote `moz-extension://` browser in a bounded Platform surface. An action without a popup receives the standard extension click event.

Navis must not read an extension popup's DOM, simulate clicks in it or create a private adapter for one built-in. The original extension popup is the canonical interaction path.

## Compatibility profile

The baseline includes Gecko's ordinary WebExtensions lifecycle and the APIs needed by content-focused desktop extensions:

- Manifest V2 persistent/event backgrounds and the pinned ESR's supported Manifest V3 background model;
- `runtime`, `i18n`, `storage.local`, `storage.session`, `storage.managed`, `permissions`, `alarms`, `idle`, `clipboard`, `cookies`, `dns`, `downloads`, `notifications` and `management` inventory;
- content scripts, `scripting`, `webNavigation`, `webRequest`, blocking web request and `declarativeNetRequest`;
- Navis-backed `tabs`, `windows`, `action`/`browserAction`, `pageAction`, `commands`, `contextMenus`/`menus`, `omnibox`, `find`, `history`, `bookmarks`, `sessions`, `topSites`, `browsingData`, `search` and options pages;
- bounded browser/privacy/proxy settings where Navis has an equivalent product setting and can restore ownership when an extension is disabled or removed;
- required and optional host/API permission prompts, with private-window access off by default per user extension.

Blocking-request regression uses a real link activation from an ordinary Web document. A direct WebDriver navigation is browser-privileged and is not used as a substitute for a Web-originated request. Separately, Core marks browser-owned `loadUri()` and crash-restoration loads as disallowing principal inheritance, matching the URL-bar security contract while preserving the authority required to enter exact `navis://` routes.

The following are outside the Navis profile even if some pinned-Gecko source remains:

- WebExtension Experiments or any package-supplied privileged schema/code;
- native messaging, PKCS#11 and external executable host discovery;
- DevTools extension pages/panels and profiler APIs;
- Firefox themes, Firefox sidebar UI, contextual identities/containers and tab groups until Navis owns corresponding product contracts;
- Firefox Accounts, extension-data sync, recommendations, abuse submission and store services;
- native-messaging manifests as a `storage.managed` policy source; an absent Navis or operating-system policy is reported as no managed policy and never triggers external native-host discovery;
- extension installation or mutation through production WebDriver/BiDi.

An unsupported required permission, manifest key or API produces a visible compatibility rejection before installation; it must not silently install a partially functional add-on. Optional unsupported features remain absent and are reported by the manager.

`storage.sync` is specifically not an alias for `storage.local`: exposing it with different persistence and synchronization semantics would create a false compatibility claim. Extensions may feature-detect its absence, but packages whose required behavior depends on that area are outside this profile.

## Standalone manager

`navis://extensions/` is a Navis-authenticated internal page and the standalone desktop management UI. It provides:

- separate built-in and user-extension sections;
- name, version, icon, description, signature state, enabled state and update provenance;
- required API/host permissions and granted optional permissions;
- enable/disable, local install/update, uninstall, options-page and toolbar-pin controls as applicable;
- clear progress, cancellation, validation and restart-required states;
- keyboard, accessibility, English and Simplified Chinese coverage matching every other Navis-owned page.

Application built-ins never expose uninstall or local replacement actions. User extensions never acquire application-owned or trusted-Navis-page identity.

### Android native manager

Android applies the same package classes and lifecycle policy through a native Material manager rather than a `navis://` page. The manager consumes the same product-level `ExtensionHost` shape as the rest of the Android UI. Compose sees only immutable records, action snapshots, permission requests and popup descriptors; Gecko implementation objects never enter product state.

The build verifies the hash-pinned official uBlock Origin XPI and derives its APK asset directory from that archive. Startup compares the packaged registry, package digest, extension identity and locked application allow-list before the built-in is admitted. A missing or altered required built-in fails startup closed. Stable user installation and explicit local-package update start with a Storage Access Framework stream, are bounded and staged in private storage, and reach AddonManager only after bilingual identity and permission confirmation. Navis does not perform a remote catalogue or extension-code update check.

Android developer mode provides in-app page DevTools. It does not change the signed local-XPI installation policy or add a remote-debugger installation entry point.

### Android direct-runtime ownership

Android deliberately does not embed the GeckoView product API. In particular, `GeckoRuntime`, `GeckoSession`, `GeckoView` and `WebExtensionController` are not lifecycle owners, dependencies or values in Navis contracts.

Ownership is instead split as follows:

| Owner | Responsibilities |
| --- | --- |
| `AndroidBrowserRuntime` | Product tabs, active-tab choice, profile persistence, navigation policy, downloads and Platform UI coordination |
| `DirectEngineRuntimeAdapter` | Sole lifetime owner of the private `NavisAndroidRuntime`, `NavisAndroidSession` and `NavisAndroidExtensions` peers |
| `EngineExtensionPort` | Gecko-free asynchronous lifecycle, inventory, action, popup and tab-command contract |
| Navis Gecko host | AddonManager/WebExtensions objects, extension principals, action overrides, content execution and browser-API production |
| `AndroidExtensionManager` | Permission UX, local package transactions, pin/private policy, popup surface state and immutable Compose projection |

Android optional permissions use a single product-owned transaction rather than GeckoView's controller observer. The Gecko host accepts only a bounded request correlated to the exact active native tab, projects plain permission/origin/data-collection lists through `EngineExtensionPort`, and revalidates the extension, tab and private mode after the user answers. Tab activation or close, extension disable/removal, process loss, prompt replacement and Runtime shutdown all resolve the pending request as denied. Gecko commits the permission only after that positive product response; no Toolkit object crosses the port.

This is the same Runtime/Core/Platform architecture used elsewhere in Navis: Gecko supplies engine behavior behind a private Binding, while the product Runtime remains the single owner of sessions and tabs. A private Java or JS class under the Gecko source tree is not, by itself, a GeckoView ownership dependency.

`EngineExtensionCapabilities.DIRECT_PRODUCT_V1_COMPLETE` is the executable Android source contract. It covers the shared built-in/signed-local lifecycle, private access, actions, tabs/windows, popup/options, menus, commands, downloads, notifications, browsing data, bounded management, optional permissions and the audited engine-resident API profile. `AndroidBrowserRuntime` checks that complete contract before acquiring the extension manager, and only then advertises `EngineProjection.EXTENSIONS`. A partial or unavailable backend therefore fails startup through the dedicated required-built-in error surface; it cannot silently publish a smaller extension platform.

### Tabs, actions and popup transactions

Extension tab commands use a Navis-owned process-wide bridge. `tabs.create`, `tabs.update`, `tabs.remove`, activation and `runtime.openOptionsPage` are translated into bounded DTO commands and executed by `AndroidBrowserRuntime`. They must never be sent through GeckoView's tab delegate or its numeric tab-ID arithmetic. The mapping uses the stable Navis session identity, and an options page honors `options_ui.open_in_tab`.

The toolbar consumes a Gecko-free action snapshot resolved from Gecko's actual `action`/`browser_action`/`page_action` registry for the exact active product session. Invocation is one transaction:

1. verify that the extension is enabled and may access the exact active tab, including private-browsing policy;
2. grant `activeTab` only for that tab and originating user gesture;
3. dispatch the click or resolve the popup using the direct action backend;
4. if a popup exists, issue one extension ID, popup URI and single-use target token bound to the source session and browsing mode;
5. acknowledge successful target attachment before presenting the sheet;
6. revoke the grant and dismiss the surface on failure, tab change/close, extension disable/removal, surface detach or Runtime shutdown.

Cleanup is idempotent and serialized with inventory mutation. Refreshing an inventory snapshot cannot make a popup disappear from product state while its engine grant or target remains alive. A popup uses a separate Navis-owned private session and preserves the source tab's normal/private browsing mode. No uBlock-specific DOM adapter or message protocol is permitted.

### Browser APIs and producer boundary

WebExtensions content scripts, storage, runtime messaging and request blocking remain Gecko-owned. Product-owned data and actions cross only reviewed direct ports. For example, browsing-data removal coordinates Gecko cache/cookie/site storage with Navis history and encrypted-password stores, while page downloads arrive through the same original-response stream and bounded Android download coordinator used by ordinary content.

No native-message delegate is registered: `runtime.sendNativeMessage`, native messaging and PKCS#11 remain rejected capabilities. Unsupported tab attributes such as containers, discarded/reader state, pinning or muting are rejected rather than acknowledged without effect. APIs accepted by the compatibility checker must have both a Gecko producer and a direct Navis consumer; source presence or a boolean capability marker is not sufficient evidence.

`management` is deliberately asymmetric on Android: `get`, `getAll`, `getSelf` and lifecycle events expose only the Navis-managed application registry and non-foreign profile extensions. `install`, `uninstallSelf` and `setEnabled` remain explicit errors because installation state is owned by the standalone Navis extension manager and cannot be mutated by another extension.

Android package validation must cover:

- no definition or reference to the forbidden GeckoView lifecycle owners in the final DEX;
- built-in and signed-local lifecycle, restart persistence and negative-package residue checks;
- active-tab action/click/popup, private-mode denial and grant cleanup;
- direct tab create/update/remove/activate and options-page behavior;
- generic menus/commands behavior or an install-time compatibility rejection for every unsupported required surface;
- content script, storage and blocking-request behavior through the original unmodified extension package.

## Acceptance gates

Release packages from the same source freeze must each prove their applicable form of:

1. signed local install, confirmation, startup, disable, re-enable, local XPI update, uninstall and restart persistence;
2. rejection of unsigned, corrupt, incompatible, downgrade and unsupported- capability packages without residue;
3. immutable built-in behavior for uBlock Origin alongside at least two user extension fixtures;
4. generic action-with-popup, action-with-click, badge/icon updates, options page, context menu, content script, local/session storage and network interception;
5. required and optional permission allow/deny paths and private-window isolation;
6. no Web/MIME/automation/native-host bypass around the manager;
7. extension-process isolation, parent/UI responsiveness and bounded memory regression evidence during cold uBO initialization and extension update;
8. exact package inventory, signing configuration, dual-language manager UI, accessibility and restart-clean process/profile state.

Source integration, package checks, native behavior and human acceptance are separate evidence. A capability marker or a previous built-in-only test cannot substitute for the general extension contract. Test-only extensions and test identities must not enter shipping packages; public package audits verify the actual product extension bytes and signing policy.
