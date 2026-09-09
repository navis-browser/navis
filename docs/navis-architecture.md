# Navis architecture

## Product equation

```text
Navis = Navis Core + Navis Platform
```

`Navis Core` owns browser behavior. `Navis Platform` presents that behavior as an operating-system application. `View` remains a first-class Core API object; it is not a third top-level layer.

```text
Navis Platform
  Linux / Windows / Android product UI, startup, packaging, OS integration
                         |
                         v
Navis Core public contract
  Runtime / Session / View / Delegates + plain immutable data
                         |
          +--------------+---------------+
          |                              |
Shared Rust control core       Core-private target backend
  identity and lifecycle         Gecko objects and native surfaces
  ownership and transitions      desktop and Android
          |                              |
          +--------------+---------------+
                         |
              Gecko-derived web engine
 DOM / JS / layout / network / graphics / media / IPC / sandbox
```

The dependency arrow points from Platform to Core. Desktop chrome happens to use XHTML, CSS, and JavaScript executed by Gecko, but that execution mechanism does not make Platform part of the web engine and does not let Core depend on product UI.

Natural-language presentation follows the same boundary. Navis Core and its Delegates expose stable state and identifiers, never translated sentences. Platform owns locale selection, message catalogues, formatting and accessible names. Navis ships `en-US` and `zh-CN`; the desktop resource and fallback contract is specified in the [Platform localization contract](https://github.com/navis-browser/platform/blob/main/docs/navis-i18n.md). Android uses native Android resources, but it consumes the same language-neutral Core state rather than desktop XHTML or JavaScript strings.

All Navis-owned Platform surfaces use the [Material/Chrome design system](https://github.com/navis-browser/platform/blob/main/docs/navis-ui-standard.md). Chrome defines the observable desktop reference and the matching Chromium tag may resolve UI implementation details, but neither changes the dependency direction or introduces Chromium framework, engine or product-service code into Navis.

Navis-owned desktop document pages use the exact allowlisted `navis://<page>` protocol defined in `navis-internal-pages.md`. Core authenticates the protocol handler, resolved page identity and document principal before exposing an immutable internal-page security identity; Platform never infers trust from address-bar text. Android may present an equivalent product destination as a native Platform surface, but that surface is not a Web document and does not receive or imitate the authenticated protocol identity. Firefox product `about:` pages are not a second public namespace. Standards-required `about:blank` and `about:srcdoc` remain engine documents and do not receive the Navis internal-page identity.

## Shared Rust control core

The shared crate under `core/rust/runtime` is the platform-neutral source of truth for:

- Runtime open/closed state and identity allocation;
- window-context and Session ownership;
- View registration and one-to-one Session binding;
- active Session selection per window context;
- live detach/attach transitions, including cross-window ownership transfer;
- navigation command/content identity and normalized URL, title, loading, security, history-availability and failure state;
- rejection of callbacks correlated to an older identity and of duplicate or late completion after a terminal navigation state;
- crash lifecycle state; and
- graph invariants and shutdown.

It has no third-party dependency and may not import Gecko, XPCOM, DOM, UI, operating-system, JNI, or event-loop types. This is the code intended to be compiled unchanged for Linux, Windows, macOS, and Android CPU targets.

The control core does not render a page. Gecko-derived code remains responsible for JavaScript, DOM, layout, networking, media, graphics, process isolation, and sandboxing. It is also the source of observed redirect, security, history and network-completion facts. A thin backend correlates those facts to Core navigation identities and commits a Rust transition only after the corresponding native operation succeeds, so a failed docshell/surface transfer leaves the control graph unchanged and an older network callback cannot overwrite a newer Session snapshot.

Application extension lifecycle is not duplicated in Rust Core. `DesktopExtensionManager` owns the general Gecko extension lifecycle, and the Core-private desktop Binding projects its inventory, actions and popups to the Platform. Application built-ins differ only by immutable release policy and use those same generic paths. No built-in supplies a privileged product contribution or private adapter.

## Target backends

The current desktop backend lives under `embedder/` and is mounted into the pinned Gecko tree as `desktop-embedder/`. Its private native component projects the Rust lifecycle graph into XPCOM; its JavaScript owns Gecko browser elements, frame-loader swaps, prompt bridges, and Delegate projection. Neither interface is a public third layer.

Android calls the same shared Rust control core through a thin JNI boundary and a separate Core-private binding. The Android product depends on the Navis-owned `:navis-runtime-android` module under `runtime/android/`, whose Runtime/Session/View peers bind directly to selected Gecko Android process, surface, compositor, APZ, IME and accessibility primitives. It does not depend on the `:geckoview` project or AAR and does not instantiate GeckoView's Runtime/Session/View classes. Platform-specific bindings are allowed; a second lifecycle or product-policy authority is not. The Android integration boundary is recorded in the [Runtime Android contract](https://github.com/navis-browser/runtime/blob/main/docs/android-runtime.md).

## Platform and CPU matrix

Navis targets Linux, Windows and Android, with Linux desktop as the development lead. The contracts must not encode a CPU architecture. Build and test identities always include both operating system and target architecture:

| Platform | Status | Architectures |
| --- | --- | --- |
| Linux | development lead | x86_64 first; aarch64 contract-safe |
| Windows | required | x86_64 first; aarch64 contract-safe |
| macOS | future | x86_64 and aarch64 are distinct targets |
| Android | required | aarch64 first; other supported ABIs remain explicit |

“Contract-safe” means no shared API assumption prevents the target. It does not claim a tested or shipped binary until that platform/architecture pair passes its native matrix.

## Source and update model

Navis pins one Firefox ESR source revision. Gecko and Navis remain separate repositories. Downstream changes are enumerated semantic ports: for an ESR update, review the complete upstream commit and endpoint-tree changes, then re-express each required intent against the new source. Do not merge or cherry-pick upstream history into Navis.

The canonical split-workspace source mapping is:

```text
runtime/core/              shared Rust control core and narrow C ABI
runtime/embedder/          Core-private Gecko desktop backend
runtime/android/           Core-private Android engine/Gradle backend
runtime/patches/gecko/     enumerated Gecko semantic ports
runtime/vendor/            pinned upstream Gecko revision
runtime/gecko/             generated Gecko working tree, never source of record
platform/gecko-chrome/     shared desktop Platform projection
platform/android/          Kotlin/Compose Android Platform projection
navis/                     composition, build/distribution, contracts and integration tests
```

`runtime/embedder/` is a Core-private desktop backend. Its directory name does not create a public architecture layer.

## Navis capability baseline

The capability baseline retains ordinary DOM/JS/layout/networking, WebRender, WebGL, WebGPU, common unencrypted media playback, Fission, sandboxing, WebAuthn, accessibility, storage, security configuration services, and a Core-private Gecko WebExtensions host for immutable application built-ins and signed user-selected local XPI packages. Navis owns a standalone extension manager but no online extension store. It excludes Web/MIME installation, real camera/microphone capture, PeerConnection/data channels/native libwebrtc, product telemetry, local ML/ONNX, EME/Widevine, Web Push, Safe Browsing, printing, updater/crash submission, privileged WebDriver surfaces, geckodriver and Firefox test/product services. Navis desktop explicitly retains Gecko's page DevTools client/server/toolbox closure behind a Core-private Session adapter; this does not restore privileged WebDriver authority or Firefox browser chrome or widen the loopback content-only WebDriver boundary. The extension compatibility and security boundary is defined in `navis-extension-platform.md`.

The current ESR153 Linux graph contains no executable Gecko/Glean telemetry capability. A constant-off `nsITelemetry` ABI/process-clock facade remains inside the Core-private backend because retained engine callers require that surface; it owns no recorder, store, archive, or submission path. Narrowing that compatibility ABI further is backend technical debt, not a public Core or Platform capability. Windows and cross-ESR release claims require their own reconstruction and native replay.
