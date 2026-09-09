# Navis contributor and agent instructions

Read `docs/README.md`, the relevant public contracts and `docs/development-standards.md` before substantial work. Capability scope takes precedence over architecture, then UI policy and implementation detail. A historical result or Firefox/Chrome feature is not a product requirement.

- Preserve Runtime/Core -> private backend ownership and Platform -> Core dependency direction. Core identity/lifecycle state stays dependency-free safe Rust; Gecko, XPCOM, DOM, JNI and OS objects remain in reviewed backends.
- One product capability set spans required platforms; layout, gestures and native integration may differ. Do not silently change scope during cleanup.
- Use shared UI tokens/components and the accepted Chrome/Material semantics. Android uses official Compose Material components; Gecko DevTools retains its accepted upstream styling.
- Reuse existing implementations, the standard library and native facilities before adding dependencies. Fix the smallest common cause, not every example separately. No speculative abstractions or omission of security/test gates.
- Preserve user changes, profiles, artifacts and valid caches/objects. Batch related edits with focused checks; do not rebuild for docs/test-only changes.
- Scope UI automation to the exact owned process/window, with resource limits and bounded cleanup. Never bind unrelated applications to test lifecycle.
- Record source implementation, checks, build/package identity, native tests and human acceptance separately. Existing binaries retain their original provenance after documentation/history edits; never silently relabel them.
- Keep private execution logs, PoCs, lab configuration and temporary tooling outside Git. Public inputs must help an external developer understand, build, modify or test Runtime/Platform/Navis independently. A caller in an internal script does not justify publication. Keep lab orchestration and historical acceptance ledgers outside the repositories, never mandatory build dependencies. Keep personal host and agent preferences out of public contracts.
- Keep third-party notices and corresponding-source access with the source.

Repository ownership is explicit: Runtime API/maintenance docs, preparation, source packaging and Runtime tests live in `../runtime`; UI/i18n/brand docs, generators and component tests live in `../platform`. This repository retains product contracts, browser composition and cross-repository/package gates. Do not restore old flat-layout aliases or import private work to make tests run.
