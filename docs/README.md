# Navis documentation

These documents describe the source at the selected revision, not an assurance that every declared capability has passed a final release matrix. Maintained contract filenames, headings and scope statements are version-neutral; Git commits and tags supply version identity. Explicit versions remain appropriate for release history, protocol/API semantics and pinned dependencies/references.

Do not add handwritten maintenance dates or stage-progress labels to public contracts. Git records document history; execution progress and native/human acceptance records belong outside the public source tree. Retain dates only when they identify evidence or provenance, such as a retrieved rule snapshot, an external publication or a copyright notice. Verification requirements stay in the public contract and are not replaced by historical pass/fail labels.

Personal host configuration and agent-operation preferences are not contributor requirements. Public standards describe reusable architecture, code quality, verification and safety rules, independent of an individual developer's setup.

- [Architecture](navis-architecture.md): Runtime/Core and Platform ownership.
- [Capability matrix](navis-capability-matrix.md): requirements, retained substrate and deliberate exclusions; it takes precedence over historical tests.
- [Extension platform](navis-extension-platform.md): local extension compatibility and security boundaries.
- [Desktop API](https://github.com/navis-browser/runtime/blob/main/docs/desktop-embedder-api.md): Runtime, Session, View and Delegates.
- [UI standard](https://github.com/navis-browser/platform/blob/main/docs/navis-ui-standard.md) and [localization](https://github.com/navis-browser/platform/blob/main/docs/navis-i18n.md).
- [Development standards](development-standards.md): code, testing and safety.
- [Licensing and distribution](licensing.md): MPL scope, corresponding source and third-party notices.
- [Contributor inputs](source-layout.md): public scripts/tests/config versus private experiments and evidence.
- [Source packaging](https://github.com/navis-browser/runtime/blob/main/docs/desktop-embedder-source-package.md) and [upstream maintenance](https://github.com/navis-browser/runtime/blob/main/docs/gecko-esr-update-review.md).
- [Runtime validation](https://github.com/navis-browser/runtime/blob/main/docs/runtime-validation.md): reproducible checks and evidence boundaries, not private host logs or a machine-specific acceptance record.

Additional policy/API documents in this directory apply when the selected version implements that capability. Android is absent at the 0.1.0 baseline; desktop DevTools and Android enter on the 0.2.0 development line.

Internal execution briefs, incident reports, experimental PoCs and lab evidence are kept outside the repositories. Public builds and source checks must not depend on them. Historical acceptance reports are not product configuration or independently reproducible release certificates.
