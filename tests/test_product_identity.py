#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "verify_product_identity", SOURCE_ROOT / "scripts/verify-product-identity.py"
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)

FIXTURE_FILES = (
    "product/config/version.txt",
    "product/config/version_display.txt",
    "mozconfig.android-aarch64.sccache",
    "android/build.gradle",
    "gecko/netwerk/protocol/http/moz.build",
    "gecko/netwerk/protocol/http/nsHttpHandler.cpp",
    "gecko/mobile/android/geckoview/build.gradle",
    "gecko/toolkit/modules/AppConstants.sys.mjs",
    "gecko/toolkit/modules/moz.build",
    "gecko/toolkit/moz.configure",
    "gecko/toolkit/components/extensions/parent/ext-runtime.js",
    VERIFY.PORT_PATH,
    "config/gecko-semantic-ports.json",
)


class ProductIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in FIXTURE_FILES:
            source = SOURCE_ROOT / relative
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def replace(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        content = path.read_text(encoding="utf-8")
        self.assertIn(old, content)
        path.write_text(content.replace(old, new, 1), encoding="utf-8")

    def assert_failure(self, fragment: str) -> None:
        failures = VERIFY.verify(self.root)
        self.assertTrue(
            any(fragment in failure for failure in failures),
            f"expected {fragment!r} in {failures!r}",
        )

    def test_current_identity_closure_passes(self) -> None:
        self.assertEqual([], VERIFY.verify(self.root))

    def test_desktop_product_version_cannot_drift(self) -> None:
        (self.root / "product/config/version_display.txt").write_text(
            "0.2.0-preview\n", encoding="utf-8"
        )
        self.assert_failure("display product version")

    def test_android_version_must_come_from_product_config(self) -> None:
        self.replace(
            "android/build.gradle",
            "versionName navisDisplayVersion",
            "versionName '0.2.0-dev'",
        )
        self.assert_failure("hard-coded")

    def test_android_version_cannot_return_to_transition_product_path(self) -> None:
        self.replace(
            "android/build.gradle",
            "platform/gecko-chrome/config/${fileName}",
            "product/config/${fileName}",
        )
        self.assert_failure("transition view")

    def test_android_gecko_must_select_product_version_files(self) -> None:
        self.replace(
            "mozconfig.android-aarch64.sccache",
            "ac_add_options --with-navis-product-version-file-path=navis/config\n",
            "",
        )
        self.assert_failure("mounted product/config")

    def test_page_user_agent_must_remain_android_and_navis_gated(self) -> None:
        self.replace(
            "gecko/netwerk/protocol/http/moz.build",
            'if CONFIG["MOZ_NAVIS_CORE"] and CONFIG["MOZ_WIDGET_TOOLKIT"] == "android":',
            "if True:",
        )
        self.assert_failure("page user-agent projection")

    def test_extension_identity_must_remain_android_and_navis_gated(self) -> None:
        self.replace(
            "gecko/toolkit/components/extensions/parent/ext-runtime.js",
            'AppConstants.MOZ_NAVIS_CORE &&\n            AppConstants.platform === "android"',
            "true",
        )
        self.assert_failure("guarded exactly once")

    def test_semantic_port_hash_must_cover_identity_changes(self) -> None:
        path = self.root / VERIFY.PORT_PATH
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assert_failure("hash does not match")

    def test_identity_port_remains_valid_when_later_ports_are_added(self) -> None:
        path = self.root / "config/gecko-semantic-ports.json"
        ledger = json.loads(path.read_text(encoding="utf-8"))
        ledger["ports"].append(
            {
                "order": 45,
                "id": "future-port",
                "patch": "patches/gecko/0045-future.patch",
                "sha256": "0" * 64,
            }
        )
        path.write_text(json.dumps(ledger), encoding="utf-8")
        self.assertEqual([], VERIFY.verify(self.root))

    def test_default_version_code_is_second_monotonic(self) -> None:
        epoch = VERIFY.ANDROID_VERSION_CODE_EPOCH
        self.assertEqual(
            VERIFY.ANDROID_VERSION_CODE_BASE,
            VERIFY.derive_android_version_code(epoch),
        )
        self.assertEqual(
            VERIFY.derive_android_version_code(epoch) + 1,
            VERIFY.derive_android_version_code(epoch + 1),
        )

    def test_default_version_code_rejects_out_of_range_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            VERIFY.derive_android_version_code(0)


if __name__ == "__main__":
    unittest.main()
