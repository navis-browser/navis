# SPDX-License-Identifier: MPL-2.0

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
    def test_maintained_profiles_follow_the_single_product_contract(self) -> None:
        ledger = json.loads((WORKSPACE / "config/runtime-capabilities.json").read_text())
        self.assertEqual(set(ledger["profiles"]), {ledger["profile"]})
        _, capabilities = VERIFIER.resolve_profile(ledger, None)
        selected = {item["id"]: item for item in capabilities}
        for capability in ("media-capture", "webrtc-peer-connection", "jpeg-xl-image-decoding"):
            self.assertEqual(selected[capability]["state"], "required", capability)
        for config in (WORKSPACE.parent / "runtime").glob("mozconfig.*"):
            self.assertNotIn("ac_add_options --disable-webrtc", config.read_text(), config.name)

    def test_default_profile_cuts_push_and_retains_media_graphs(self) -> None:
        ledger = json.loads(
            (WORKSPACE / "config/runtime-capabilities.json").read_text(
                encoding="utf-8"
            )
        )
        profile, capabilities = VERIFIER.resolve_profile(ledger, None)
        self.assertEqual(profile, "desktop-embedder-core")
        selected = {
            item["id"]: item
            for item in capabilities
            if item["id"] in {"web-push", "media-capture", "screen-capture", "webrtc-peer-connection"}
        }
        self.assertEqual(selected["screen-capture"]["state"], "excluded")
        self.assertEqual(selected["web-push"]["state"], "excluded")
        self.assertEqual(
            {selected[key]["state"] for key in ("media-capture", "webrtc-peer-connection")},
            {"required"},
        )
        self.assertEqual(selected["web-push"]["checks"]["config"], {"MOZ_WEB_PUSH": False})
        self.assertEqual(selected["web-push"]["checks"]["graph_forbidden"], ["^dom/push/"])
        self.assertEqual(
            selected["web-push"]["checks"]["omni_forbidden"],
            [
                "^modules/(?:ChromePushSubscription|Push(?:BroadcastService|Components|Crypto|DB|Record|Service|ServiceWebSocket)?)\\.sys\\.mjs$"
            ],
        )
        self.assertEqual(
            selected["media-capture"]["checks"]["config"], {"MOZ_WEBRTC": True}
        )
        self.assertEqual(
            selected["webrtc-peer-connection"]["checks"]["config"],
            {"MOZ_WEBRTC": True},
        )
        self.assertIn(
            "modules/DesktopEmbedderWebRTCParent.sys.mjs",
            selected["media-capture"]["checks"]["omni_required"],
        )
        self.assertEqual(
            selected["screen-capture"]["checks"]["omni_content_required"][
                "defaults/pref/navis.js"
            ],
            [
                'pref\\("media\\.getdisplaymedia\\.enabled", false, locked\\);',
                'pref\\("media\\.getusermedia\\.screensharing\\.enabled", false, locked\\);',
            ],
        )

    def test_unknown_profile_is_rejected(self) -> None:
        ledger = json.loads((WORKSPACE / "config/runtime-capabilities.json").read_text())
        with self.assertRaisesRegex(ValueError, "unknown capability profile"):
            VERIFIER.resolve_profile(ledger, "unknown-profile")

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
