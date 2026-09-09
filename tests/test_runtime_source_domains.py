# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
AUDITOR_PATH = WORKSPACE / "scripts" / "audit-runtime-source-domains.py"
SPEC = importlib.util.spec_from_file_location("audit_runtime_source_domains", AUDITOR_PATH)
assert SPEC is not None and SPEC.loader is not None
AUDITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDITOR)


class RuntimeSourceDomainTests(unittest.TestCase):
    def configured_objdir(self, **substitutions: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        objdir = Path(temporary.name)
        (objdir / "config.status.json").write_text(
            json.dumps({"substs": substitutions}), encoding="utf-8"
        )
        (objdir / "config.status").write_text(
            "substs = " + repr(substitutions) + "\n", encoding="utf-8"
        )
        return objdir

    def manifest(self) -> dict:
        return {
            "schema_version": 1,
            "forbidden_patterns": [],
            "domains": [
                {
                    "id": "engine",
                    "classification": "runtime-core",
                    "state": "required",
                    "patterns": [r"^dom/"],
                },
                {
                    "id": "page-developer-tools",
                    "classification": "product-page-debugging",
                    "state": "profile-dependent",
                    "requires_config": {
                        "MOZ_DESKTOP_EMBEDDER_DEVTOOLS": "1",
                        "MOZ_DEVTOOLS": "all",
                    },
                    "patterns": [
                        r"^devtools/(?!platform(?:/|$))",
                        r"^toolkit/components/viewsource(?:/|$)",
                    ],
                },
            ],
        }

    def test_page_devtools_domain_requires_both_exact_build_facts(self) -> None:
        domains, _ = AUDITOR.compile_manifest(self.manifest())
        for substitutions in (
            {},
            {"MOZ_DESKTOP_EMBEDDER_DEVTOOLS": "1"},
            {
                "MOZ_DESKTOP_EMBEDDER_DEVTOOLS": "1",
                "MOZ_DEVTOOLS": "none",
            },
        ):
            with self.subTest(substitutions=substitutions):
                selected = AUDITOR.configured_domains(
                    domains, self.configured_objdir(**substitutions)
                )
                self.assertEqual([item["id"] for item in selected], ["engine"])

        selected = AUDITOR.configured_domains(
            domains,
            self.configured_objdir(
                MOZ_DESKTOP_EMBEDDER_DEVTOOLS="1", MOZ_DEVTOOLS="all"
            ),
        )
        self.assertEqual(
            [item["id"] for item in selected],
            ["engine", "page-developer-tools"],
        )

    def test_inactive_page_devtools_sources_remain_unclassified(self) -> None:
        domains, forbidden = AUDITOR.compile_manifest(self.manifest())
        domains = AUDITOR.configured_domains(domains, self.configured_objdir())
        result = AUDITOR.audit_lens(
            {
                "devtools/client/Makefile": None,
                "toolkit/components/viewsource/Makefile": None,
            },
            domains,
            forbidden,
        )
        self.assertEqual(
            result["unclassified"],
            [
                "devtools/client/Makefile",
                "toolkit/components/viewsource/Makefile",
            ],
        )

    def test_active_page_devtools_classifies_viewsource_backend(self) -> None:
        domains, forbidden = AUDITOR.compile_manifest(self.manifest())
        domains = AUDITOR.configured_domains(
            domains,
            self.configured_objdir(
                MOZ_DESKTOP_EMBEDDER_DEVTOOLS="1", MOZ_DEVTOOLS="all"
            ),
        )
        result = AUDITOR.audit_lens(
            {
                "toolkit/components/viewsource/Makefile": None,
                "toolkit/components/viewsource/backend.mk": None,
            },
            domains,
            forbidden,
        )
        self.assertEqual(result["unclassified"], [])

    def test_requires_config_rejects_non_string_contracts(self) -> None:
        manifest = self.manifest()
        manifest["domains"][1]["requires_config"] = {
            "MOZ_DESKTOP_EMBEDDER_DEVTOOLS": True
        }
        with self.assertRaisesRegex(ValueError, "invalid requires_config"):
            AUDITOR.compile_manifest(manifest)

    def test_product_search_owns_only_retained_js_backend_metadata(self) -> None:
        manifest = json.loads((Path(__file__).resolve().parents[1] /
                               "config/runtime-source-domains.json").read_text())
        domains, forbidden = AUDITOR.compile_manifest(manifest)
        backend = ["toolkit/components/search/Makefile", "toolkit/components/search/backend.mk"]
        unretained = ["toolkit/components/search/SearchService.cpp",
                      "toolkit/components/search/selector/backend.mk",
                      "toolkit/components/search-extra/Makefile"]
        report = AUDITOR.audit_lens(dict.fromkeys(backend + unretained), domains, forbidden)
        self.assertEqual(report["unclassified"], sorted(unretained))


if __name__ == "__main__":
    unittest.main()
