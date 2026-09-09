# Navis development and test standards

These standards apply to all implementation work.

## Working habits and style

- Keep changes small enough to state one behavioral intent and its rollback.
- Treat `core/rust/runtime` as dependency-free safe Rust. Run `rustfmt`; forbid unsafe code there. Unsafe code is confined to reviewed FFI boundaries.
- Keep C++ and privileged JavaScript in target backends. Platform code consumes the public Runtime/Session/View/Delegate contract and may not reach around it to Gecko objects.
- Apply the [Platform UI standard](https://github.com/navis-browser/platform/blob/main/docs/navis-ui-standard.md) to every Navis-owned surface. Use shared Material tokens and components rather than page-local colors, spacing, radii, shadows, focus styles or motion values.
- When a UI implementation detail is uncertain, observe the pinned Chrome executable and inspect the matching pinned Chromium tag. Record the path and behavioral conclusion for a non-trivial translation; do not import Chromium framework or product-service dependencies into Navis.
- Represent boundary data as immutable plain values. Do not expose principals, docshells, browsing contexts, process identifiers, DOM nodes, XPCOM objects, or target UI types through the public Core contract.
- Every graph cut must cover configure, build directories, generated bindings, native/Rust dependencies, IPC, packaging, capability reporting, and negative tests. A preference-only disable is not a completed cut.
- Preserve an enumerated, hash-owned semantic-port series. ESR work reviews and rewrites intent; it never hides maintenance inside a history merge.
- Do not mix refactors, capability cuts, and upstream-version changes in one evidence run unless the interaction itself is the experiment.
- Batch causally related fixes behind focused checks, then run the applicable build and packaged-runtime regressions. Use failure evidence to determine which checks a correction needs rather than repeating unrelated suites.
- A tooling or host limitation that does not weaken a product requirement may be bypassed through an explicit, bounded alternative. Record the limitation, exact workaround and preserved acceptance boundary, continue useful work, and report the accumulated workaround ledger at the milestone handoff.

## Unit-test standard

Shared Core changes require deterministic tests for:

- the successful state transition;
- every rejected precondition that can be reached through a public operation;
- idempotent close/shutdown behavior where promised;
- ownership and bidirectional-binding invariants;
- rollback/no-mutation on rejected transitions; and
- cross-window or cross-surface behavior when ownership can move.

A bug fix begins with a failing test at the lowest layer that can express the defect. Tests do not depend on timing, internet access, window focus, or a particular hash-map iteration order. FFI additions require null/error mapping coverage or a native integration test; ABI values are append-only within a released Core version.

## Regression gates

Run the smallest sufficient gate first, followed by every gate made relevant by the changed dependency edge:

| Change | Required before handoff |
| --- | --- |
| Shared Rust lifecycle | rustfmt, all Core unit tests, invariant/static boundary checks, both desktop consumers |
| Core/private FFI ABI | complete header/export inventory, C++ and Rust size/alignment/offset/enum/function-type assertions, host Core/FFI tests, Linux and Windows aarch64 source checks, then both native package builders |
| Backend JS/XPCOM/C++ | source-boundary check, incremental build, independent shell, Navis Delegate smoke |
| Capability/build graph | configure, source-domain audit, clean build, package audit, positive retained-capability and negative removed-capability tests |
| Remote security/configuration data | exact source-policy gate, configure allowlist, stale-profile denial test, upstream valid/invalid-signature suite, exact dump/timestamp/attachment package audit and native archive replay |
| Deferred browser-mediated Web API | pinned-ESR WebIDL/pref review, locked product exposure, packaged preference audit, secure-content feature-detection absence on Linux and Windows, and an explicit capability/UI decision before activation |
| WebAuthn/passkeys | immutable Delegate projection and origin-authentication gate, fail-closed prompt lifecycle, standard virtual-authenticator registration/modal and conditional assertion/direct-attestation/cancel/failure replay, native accessible UI evidence, exact package audit, Linux no-device/no-fallback provider gate and native Windows provider replay; physical USB-key evidence when hardware is available, or an explicit acceptance-owner waiver recorded without a success claim |
| Packaging/platform glue | native package audit, startup, navigation/View, Fission, sandbox, crash/recovery |
| Gecko semantic port or ESR pin | clean preparation replay, complete update review, clean build, full native regression matrix |
| Platform UI only | boundary and design-system checks, incremental package/run, affected state fixtures, UI/accessibility automation, focused visual comparison across applicable light/dark/private/forced-color and DPI states, and a native manual check |
| Context menu or native Web interaction | actor/Delegate contract tests, runtime rejection of unoffered and stale requests, the deterministic HTML interaction baseline, Gecko popup lifecycle plus Platform keyboard/accessibility automation, open/closed and edge-placement screenshots, dark/private/forced-color composited pixel gates for disabled state plus rounded elevation, and native execution of every changed command |
| Browser-owned default control | Gecko value/event/focus/accessibility replay, open/move/dismiss/reopen/keyboard-commit/close coverage for every changed popup, and applicable dark/forced-color/DPI visual phases on both native Platforms |

Linux is the lead gate, not evidence for Windows. A platform/architecture claim exists only after its native run. Internet site testing supplements controlled fixtures; it cannot replace deterministic contract and security tests.

## UI regression standard

Chrome-like appearance is not accepted by impression alone. A Navis-owned UI change uses the frozen Chrome/Chromium reference in the [Platform UI standard](https://github.com/navis-browser/platform/blob/main/docs/navis-ui-standard.md), covers every affected interaction state, and tests keyboard focus plus accessible role/name/state. Release-critical surfaces keep deterministic Linux and Windows screenshot fixtures across the required theme, window-size and DPI matrix. OS-owned pickers and consent surfaces remain native and are verified at their Platform boundary.

Browser-owned default form controls retain Gecko's value, event, focus and accessibility semantics even when a complex popup uses an internal DOM/CSS document. A chrome-host change must verify popup open, keyboard traversal, commit into the originating element, close/non-showing state and the browser security boundary; a screenshot of the closed field alone is insufficient.

A capability exclusion also removes its UI, labels, settings and empty placeholders. A visual refactor may not add a Core capability or bypass a Delegate merely to reproduce a Chrome control.

The deterministic HTML interaction baseline contains the retained native links, images, media, selection, editable regions and form controls in one local page. It catches changes to default interaction and context-menu command availability without turning external websites into unit fixtures. Platform assertions are shared; visual baselines remain native per OS, scale and theme. Select, Date and Time additionally retain open/moved/closed screenshots and accessibility trees because their popup documents are outside ordinary content DOM assertions. Linux release evidence includes dark, forced-colors and 150% phases; a stable pair of closed-state screenshots may be identical, while every actual open popup must remain visually distinct from its moved and closed states.

Retained spellcheck is accepted only when the package contains the exact reviewed dictionary inventory and an editable misspelling produces and applies a native browser-menu suggestion through Gecko editor semantics. Source or manifest presence alone is insufficient; extra/tampered dictionary bytes and an unbounded suggestion projection must fail the release gate.

Retained WebAuthn is not accepted from WebIDL exposure alone. The extracted product must complete registration and assertion through a standard WebDriver virtual authenticator, exercise cancellation through native Platform UI, and show that no native transaction identity or credential material crossed the Delegate. Platform-provider claims additionally require native OS replay.

## Build discipline

- A release build requires one reviewed project-source freeze manifest. It hashes the complete Navis production, build, test, policy and contract source scope, including Android, while excluding Gecko/Gradle objects and generated artifacts. Linux, Windows and Android candidate wrappers must verify the same aggregate before invoking Mach; Android verifies it again after Gradle returns. Any added, removed, mode-changed or byte-changed source invalidates the freeze and must be resolved before another candidate build.
- One explicit UTC `NAVIS_BUILD_ID` identifies the Linux, Windows and Android artifacts built from that shared freeze. Every packaged application.ini, platform.ini, AppConstants projection and platform-native toolkit BuildID must match it. A failed attempt that emitted no artifact may resume from the same verified source, BuildID and sccache-backed object graph; any source change requires a replacement freeze and a new BuildID, and an emitted artifact is never overwritten.
- Reuse a verified object directory only when configure inputs, source tree, target triple, and generated-source provenance match.
- Candidate profiles keep `AUTOCLOBBER` empty. Their wrappers first verify the immutable object-directory identity, run an in-place configure, then verify the resulting target/release/cache feature set before the one native build. A missing graph or Gecko clobber request is an explicit bootstrap/review event; a wrapper never creates or silently deletes the graph.
- Candidate profiles may use sccache. Cache hits reduce compilation work but do not replace source-freeze, configure, package-inventory or artifact-hash evidence. Empty-object/cache-disabled builds are reserved for explicit graph and timing baselines, not required for every unchanged candidate iteration.
- A clean-build timing comparison uses empty object directories, the same host, pin, profile, job count, cache state policy, and thermal policy. Incremental and configuration-switch times are labeled separately.
- A package after any capability-graph change must recreate the selected object directory's derived distribution staging before audit. Old `dist/bin`, omni, or product-package files are never evidence for a new graph, even when the compile database and linked source audit are current.
- Build logs record pin, semantic-port hashes, mozconfig, target, start/end, peak memory, CPU utilization, package hash, and test result.
- Native UI automation must target the tested executable's windows, isolate test data, bound execution and clean up its own resources. It must not send input to unrelated applications or alter user profiles outside the test.

## Definition of done

A change is done only when implementation, tests, executable capability/source ledgers, documentation, reproducible commands and applicable UI-standard evidence agree. “Compiles” is not a behavior result; “starts” is not a browsing result; and an old artifact is not evidence for a changed source graph. Navis itself is not ready for manual release acceptance until the reviewed capability acceptance checklist, applicable code tests and the real packaged-product automation matrix all pass on their required native targets.
