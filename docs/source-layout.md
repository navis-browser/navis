# Source ownership

Start at each repository's README, then its implementation:

| Repository | Implementation | Supporting public inputs |
| --- | --- | --- |
| Runtime | Core, private backends, pin and semantic ports | API/maintenance docs, standalone preparation/source packaging, Runtime behavior/ABI tests and independent API shell |
| Platform | Gecko chrome, native host integration, Android UI where present | UI/i18n/brand docs, resource generators, UI component tests |
| Navis | Browser composition | Product capability/security contracts, target build/package wrappers, cross-repository integration and artifact-policy checks |

Source paths name their owners directly. Navis has no `product`, `embedder`, `core`, `gecko` or mozconfig aliases and needs no mount-compatibility helper. Preparing Gecko still mounts the actual Runtime/Platform inputs into the engine build tree; those generated engine mounts are not a second source layout.

`docs`, `scripts` and `tests` are not publication categories in themselves. Keep maintained contracts, build inputs and meaningful executable checks beside their owner. Product integration/security gates remain public and mandatory where applicable. Do not replace Runtime unit tests with copies in Navis or publish lab orchestration merely because another internal script calls it.

Private work contains one-off migration tools, PoCs, screenshots, incident logs and historical acceptance evidence. It is never a public source dependency. Generated outputs may default to `../work/artifacts`, but contributors can set `NAVIS_ARTIFACT_DIR`; an output destination does not require a private input. Do not publish the enclosing workspace or private backups.

See the [project README](../README.md) for composition and focused test commands. Version tags retain their own capabilities: v0.1.0 is desktop-only; Android and desktop page DevTools enter the v0.2.0 line. Source reorganization does not change previous binary provenance or replace native/human release acceptance.
