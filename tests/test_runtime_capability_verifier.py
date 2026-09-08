from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest
import zipfile


WORKSPACE = pathlib.Path(__file__).resolve().parents[1]
VERIFIER_PATH = WORKSPACE / "scripts" / "verify-runtime-capabilities.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_runtime_capabilities", VERIFIER_PATH
)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class RuntimeCapabilityVerifierTests(unittest.TestCase):
    def test_managed_storage_guard_retains_unconditional_desktop_rejection(self) -> None:
        ledger = json.loads((WORKSPACE / "config/runtime-capabilities.json").read_text())
        entry = "chrome/toolkit/content/extensions/parent/ext-storage.js"
        bodies = (
            ("if (AppConstants.MOZ_DESKTOP_EMBEDDER || navisDirectRuntime) {\n"
             "  return null;\n}", True),
            ("if (navisDirectRuntime) { return null; }", False),
            ("if (AppConstants.MOZ_DESKTOP_EMBEDDER && navisDirectRuntime) { return null; }", False),
            ("if (AppConstants.MOZ_DESKTOP_EMBEDDER || navisDirectRuntime) { return policy; }", False),
            ("if (AppConstants.MOZ_DESKTOP_EMBEDDER || navisDirectRuntime) {\n"
             "  NativeManifests.lookupManifest(); return null;\n}", False),
        )
        capabilities = [item for item in ledger["capabilities"] if item["id"] in {
            "webextensions-runtime", "local-extension-management",
        }]
        self.assertEqual(len(capabilities), 2)
        for capability in capabilities:
            patterns = capability["checks"]["omni_content_required"][entry]
            with tempfile.TemporaryDirectory() as temporary:
                omni = pathlib.Path(temporary) / "omni.ja"
                for body, accepted in bodies:
                    with self.subTest(capability=capability["id"], body=body):
                        with zipfile.ZipFile(omni, "w") as archive:
                            archive.writestr(entry, body)
                        failures: list[str] = []
                        VERIFIER.verify_omni_contents(
                            capability["id"], {"omni_content_required": {entry: patterns}},
                            omni, failures,
                        )
                        self.assertEqual(not failures, accepted)

    def test_omni_checks_support_multiline_and_anchored_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            omni = pathlib.Path(temporary) / "omni.ja"
            with zipfile.ZipFile(omni, "w") as archive:
                archive.writestr(
                    "guard.js",
                    "const ready = true;\n"
                    "if (AppConstants.MOZ_DESKTOP_EMBEDDER) {\n"
                    "  return null;\n"
                    "}\n"
                    "category exact-registration\n",
                )

            failures: list[str] = []
            VERIFIER.verify_omni_contents(
                "fixture",
                {
                    "omni_content_required": {
                        "guard.js": [
                            r"if \(AppConstants\.MOZ_DESKTOP_EMBEDDER\) "
                            r"\{[\s\S]*?return null;",
                            r"^category exact-registration$",
                        ]
                    }
                },
                omni,
                failures,
            )
            self.assertEqual(failures, [])

    def test_encrypted_media_policy_rejects_gmp_product_runtime(self) -> None:
        ledger = json.loads(
            (WORKSPACE / "config" / "runtime-capabilities.json").read_text(
                encoding="utf-8"
            )
        )
        encrypted_media = next(
            item for item in ledger["capabilities"] if item["id"] == "encrypted-media"
        )
        checks = encrypted_media["checks"]

        forbidden_entries = [
            "chrome/toolkit/content/global/gmp-sources/widevinecdm.json",
            "modules/GMPInstallManager.sys.mjs",
            "modules/addons/GMPProvider.sys.mjs",
        ]
        failures: list[str] = []
        VERIFIER.forbid_patterns(
            "encrypted-media",
            "omni",
            checks["omni_forbidden"],
            forbidden_entries,
            failures,
        )
        self.assertEqual(len(failures), len(forbidden_entries))

        with tempfile.TemporaryDirectory() as temporary:
            omni = pathlib.Path(temporary) / "omni.ja"
            with zipfile.ZipFile(omni, "w") as archive:
                archive.writestr(
                    "components/components.manifest",
                    "category addon-provider-module GMPProvider "
                    "resource://gre/modules/addons/GMPProvider.sys.mjs\n",
                )

            content_failures: list[str] = []
            VERIFIER.verify_omni_contents(
                "encrypted-media", checks, omni, content_failures
            )
            self.assertEqual(len(content_failures), 1)
            self.assertIn("forbidden omni file", content_failures[0])

    def test_observability_policy_rejects_the_complete_dap_closure(self) -> None:
        ledger = json.loads(
            (WORKSPACE / "config" / "runtime-capabilities.json").read_text(
                encoding="utf-8"
            )
        )
        observability = next(
            item
            for item in ledger["capabilities"]
            if item["id"] == "observability-runtime"
        )
        checks = observability["checks"]

        cases = (
            (
                "graph",
                checks["graph_forbidden"],
                ["toolkit/components/dap/Unified_cpp_components_dap0.cpp"],
            ),
            (
                "linked Rust graph",
                checks["rust_graph_forbidden"],
                ["toolkit/components/dap/ffi/src/lib.rs"],
            ),
            (
                "omni",
                checks["omni_forbidden"],
                ["modules/DAPSender.sys.mjs"],
            ),
        )
        for label, patterns, entries in cases:
            with self.subTest(label=label):
                failures: list[str] = []
                VERIFIER.forbid_patterns(
                    "observability-runtime",
                    label,
                    patterns,
                    entries,
                    failures,
                )
                self.assertEqual(len(failures), 1)


if __name__ == "__main__":
    unittest.main()
