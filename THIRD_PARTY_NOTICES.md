# Third-party materials

Navis-owned source is MPL-2.0 unless marked otherwise; see LICENSE. This does not relicense third-party files. Gecko source/ports and product-bundled archives are owned by the sibling Runtime/Platform repositories and their notices.

Gecko-derived tests or copied snippets retain their per-file upstream license headers. Product extensions and their signed archives remain in Platform with their corresponding source and notices; private lab fixtures are not distributed by this repository.

Windows binary packages also carry Microsoft Visual C++ Redistributable DLLs from the reviewed Microsoft toolchain. These files retain Microsoft's copyright and distribution terms; they are not covered by Navis's MPL license. The reviewed inventory is `config/windows-runtime.json`; deployment and update requirements are documented in `docs/windows-build.md`. Microsoft's redistribution documentation is https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files.
