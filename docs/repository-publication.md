# Source repository composition

Publish the independent `navis`, `runtime` and `platform` repositories, not the enclosing workspace. Runtime owns Core, private backends, Gecko pin and semantic ports; Platform owns desktop chrome and native applications; Navis owns the matching composition, public contracts and integration gates.

Only reviewed branches and version tags are publication inputs. The workspace's private `work/` directory and ignored caches, profiles, generated Gecko and experimental tooling are not publication inputs. Publishing source and distributing validated binaries are separate release steps.

Use sibling checkouts and select the same version family. Where present, `config/navis-repository-set.json` pins exact Runtime/Platform commits. `scripts/verify-repository-set.py` checks exact revisions, clean sources and baseline ancestry; a detached tag checkout and a normal clone remote are both supported.

Read [source preparation](https://github.com/navis-browser/runtime/blob/main/docs/desktop-embedder-source-package.md) and [development standards](development-standards.md). A tag does not include toolchains or prepared native objects. The historical 0.1.0 baseline is desktop only; 0.2.0 adds Android and desktop DevTools. Native binary acceptance remains bound to the recorded source/BuildID/archive identity, not a renamed tag alone.

Preserve third-party notices and corresponding-source access, and recheck it when publishing signed test inputs or application-bundled extensions. Public source readiness does not settle production signing, updater policy or a final Navis binary release matrix.

Follow [licensing and distribution](licensing.md) before offering public downloads. A repository tag is not a replacement for source directions accompanying the binary.
