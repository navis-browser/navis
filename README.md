# Navis

Navis is a Gecko-derived browser organized as independent Runtime and Platform sources, with this repository owning composition, contributor documentation, build/packaging entry points and ongoing regression checks.

```text
navis-browser/
  navis/       integration, public contracts, scripts, tests and configuration
  runtime/     shared Rust control Core, private bindings, Gecko pin and ports
  platform/    desktop chrome, native host integration and Android UI
```

Select matching revisions in sibling directories. Where present, `config/navis-repository-set.json` locks peer commits; do not mix arbitrary branch heads. The 0.1.0 baseline contains Linux/Windows desktop; the 0.2.0 line adds desktop Gecko DevTools and a native Android Platform sharing the Core. macOS and HarmonyOS descriptors are not tested browser products.

Start with [the documentation index](docs/README.md), [architecture](docs/navis-architecture.md), and [source preparation and packaging](https://github.com/navis-browser/runtime/blob/main/docs/desktop-embedder-source-package.md). Gecko is prepared from the exact upstream pin and hash-owned semantic ports, not committed here and not a submodule. Object directories, toolchains, caches, profiles, raw evidence and local experiments are not source-distribution inputs.

On a prepared Linux workspace, `scripts/mach.sh build` builds the selected development profile and `scripts/run.sh` launches it. Candidate wrappers require a separately reviewed source freeze, matching configured graph and explicit target/BuildID. Never silently clobber a valid graph. Consult [development standards](docs/development-standards.md) before builds or native automation; source publication is not a complete binary release certification.

Navis-owned source uses MPL-2.0; see LICENSE. Third-party files retain their own licenses and notices. Where present, THIRD_PARTY_NOTICES.md and the notices beside bundled extension/locale archives identify corresponding source.

## Build and check

From matching sibling `navis`, `runtime` and `platform` checkouts:

```sh
# Fetch/apply the pinned Gecko tree only when preparing a native build.
bash navis/scripts/prepare-gecko.sh
bash navis/scripts/mach.sh build
bash navis/scripts/run.sh
```

Install the pinned Gecko release's host/target toolchain first. `mach.sh` uses `NAVIS_PYTHON` if set, otherwise supported Python on PATH. No private toolchain path or legacy source aliases are required. Existing configured graphs/caches are not interchangeable across pins, targets or source locations.

For source-only changes, select relevant checks without preparing Gecko:

```sh
node runtime/tests/address-input.mjs
node platform/tests/omnibox-edit-state.mjs
node navis/tests/internal-pages.mjs
python3 runtime/scripts/package-embedder-source.py --check
python3 -m unittest discover -s navis/tests -p test_source_freeze.py
```

Tests reading `runtime/gecko/` require the exact prepared engine; missing source is not a pass. Runtime owns its API fixtures/behavior tests and Platform owns UI component tests. Navis tests exercise their integration and reject invalid packages, policies and source identities. Run only tests available at the selected tag. Native/browser matrices are separate evidence, not implied by these checks. See [source ownership](docs/source-layout.md).

Both tags include `config/navis-repository-set.json`. After selecting matching sibling revisions, `python3 navis/scripts/verify-repository-set.py` checks their exact commits, clean worktrees, baseline ancestry and version-specific Android presence. A detached version checkout is supported. Generated Gecko is separate from this source check and must match the selected version before building.
