#!/usr/bin/env python3

"""Unit tests for the executable Navis Remote Settings policy."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent
TOOL = WORKSPACE / "scripts/verify-remote-settings-policy.py"
SPEC = importlib.util.spec_from_file_location("remote_settings_policy", TOOL)
assert SPEC and SPEC.loader
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class RemoteSettingsPolicyTest(unittest.TestCase):
    def test_packaged_appconstant_accepts_line_directives_but_requires_true(self) -> None:
        plain = f"{POLICY.CAPABILITY_NAME}:\n  true,"
        packaged = (
            f"  {POLICY.CAPABILITY_NAME}:\n"
            '//@line 159 "$SRCDIR/toolkit/modules/AppConstants.sys.mjs"\n'
            "  true,"
        )
        disabled = packaged.replace("  true,", "  false,")
        self.assertTrue(
            POLICY.has_enabled_app_constant(plain, POLICY.CAPABILITY_NAME)
        )
        self.assertTrue(
            POLICY.has_enabled_app_constant(packaged, POLICY.CAPABILITY_NAME)
        )
        self.assertFalse(
            POLICY.has_enabled_app_constant(disabled, POLICY.CAPABILITY_NAME)
        )

    def test_repository_policy_is_consistent(self) -> None:
        policy, allowed, packaged, syncable = POLICY.validate_policy()
        POLICY.verify_product_declaration(policy, allowed, syncable)
        POLICY.verify_semantic_port()
        self.assertEqual(len(allowed), 12)
        self.assertEqual(len(packaged), 10)
        self.assertEqual(len(syncable), 10)
        self.assertEqual(
            packaged - syncable,
            {
                "main/devtools-compatibility-browsers",
                "main/devtools-devices",
            },
        )

    def test_unsorted_allowlist_fails_closed(self) -> None:
        payload = json.loads(POLICY.POLICY_PATH.read_text(encoding="utf-8"))
        payload["allowed_collections"][0], payload["allowed_collections"][1] = (
            payload["allowed_collections"][1],
            payload["allowed_collections"][0],
        )
        with tempfile.TemporaryDirectory(prefix="navis-rs-policy-test.") as root:
            mutated = Path(root) / "policy.json"
            mutated.write_text(json.dumps(payload), encoding="utf-8")
            original = POLICY.POLICY_PATH
            POLICY.POLICY_PATH = mutated
            try:
                with self.assertRaisesRegex(POLICY.PolicyError, "sorted and unique"):
                    POLICY.validate_policy()
            finally:
                POLICY.POLICY_PATH = original

    def test_android_must_select_the_same_owned_policy(self) -> None:
        policy, allowed, _, syncable = POLICY.validate_policy()
        with tempfile.TemporaryDirectory(prefix="navis-rs-android-test.") as root:
            mutated = Path(root) / "mozconfig"
            mutated.write_text(
                POLICY.ANDROID_MOZCONFIG.read_text(encoding="utf-8").replace(
                    f"ac_add_options {POLICY.OPTION_NAME}={POLICY.POLICY_RELATIVE_PATH}\n",
                    "",
                ),
                encoding="utf-8",
            )
            original = POLICY.ANDROID_MOZCONFIG
            POLICY.ANDROID_MOZCONFIG = mutated
            try:
                with self.assertRaisesRegex(
                    POLICY.PolicyError,
                    "Android product does not consume",
                ):
                    POLICY.verify_product_declaration(policy, allowed, syncable)
            finally:
                POLICY.ANDROID_MOZCONFIG = original

    def test_appconstants_csv_must_enter_preprocessor_defines(self) -> None:
        policy, allowed, _, syncable = POLICY.validate_policy()
        with tempfile.TemporaryDirectory(prefix="navis-rs-defines-test.") as root:
            mutated = Path(root) / "moz.build"
            mutated.write_text(
                POLICY.TOOLKIT_MODULES_BUILD.read_text(encoding="utf-8").replace(
                    f'    "{POLICY.CSV_CONFIG_NAME}",\n',
                    "",
                    1,
                ),
                encoding="utf-8",
            )
            original = POLICY.TOOLKIT_MODULES_BUILD
            POLICY.TOOLKIT_MODULES_BUILD = mutated
            try:
                with self.assertRaisesRegex(
                    POLICY.PolicyError,
                    "AppConstants preprocessing does not project",
                ):
                    POLICY.verify_product_declaration(policy, allowed, syncable)
            finally:
                POLICY.TOOLKIT_MODULES_BUILD = original

    def test_bootstrap_membership_must_be_boolean(self) -> None:
        payload = json.loads(POLICY.POLICY_PATH.read_text(encoding="utf-8"))
        payload["allowed_collections"][0]["packaged_bootstrap"] = 1
        with tempfile.TemporaryDirectory(prefix="navis-rs-bootstrap-test.") as root:
            mutated = Path(root) / "policy.json"
            mutated.write_text(json.dumps(payload), encoding="utf-8")
            original = POLICY.POLICY_PATH
            POLICY.POLICY_PATH = mutated
            try:
                with self.assertRaisesRegex(
                    POLICY.PolicyError,
                    "packaged_bootstrap is not boolean",
                ):
                    POLICY.validate_policy()
            finally:
                POLICY.POLICY_PATH = original

    def test_remote_sync_membership_must_be_boolean(self) -> None:
        payload = json.loads(POLICY.POLICY_PATH.read_text(encoding="utf-8"))
        payload["allowed_collections"][0]["remote_sync"] = 1
        with tempfile.TemporaryDirectory(prefix="navis-rs-sync-test.") as root:
            mutated = Path(root) / "policy.json"
            mutated.write_text(json.dumps(payload), encoding="utf-8")
            original = POLICY.POLICY_PATH
            POLICY.POLICY_PATH = mutated
            try:
                with self.assertRaisesRegex(
                    POLICY.PolicyError,
                    "remote_sync is not boolean",
                ):
                    POLICY.validate_policy()
            finally:
                POLICY.POLICY_PATH = original

    def test_offline_collection_requires_a_packaged_bootstrap(self) -> None:
        payload = json.loads(POLICY.POLICY_PATH.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in payload["allowed_collections"]
            if item["identifier"] == "main/devtools-devices"
        )
        entry["packaged_bootstrap"] = False
        with tempfile.TemporaryDirectory(prefix="navis-rs-offline-test.") as root:
            mutated = Path(root) / "policy.json"
            mutated.write_text(json.dumps(payload), encoding="utf-8")
            original = POLICY.POLICY_PATH
            POLICY.POLICY_PATH = mutated
            try:
                with self.assertRaisesRegex(
                    POLICY.PolicyError,
                    "without a packaged bootstrap",
                ):
                    POLICY.validate_policy()
            finally:
                POLICY.POLICY_PATH = original

    def test_runtime_inventory_rejects_an_extra_dump(self) -> None:
        policy, allowed, packaged, syncable = POLICY.validate_policy()
        expected_timestamps = {
            identifier
            for identifier in allowed
            if (
                WORKSPACE / f"../runtime/gecko/services/settings/dumps/{identifier}.json"
            ).is_file()
        }
        prefs = "\n".join(
            (
                f'pref("{POLICY.SERVER_PREF}", "{policy["provider"]["production_server"]}", locked);',
                f'pref("{POLICY.PREVIEW_PREF}", false, locked);',
            )
        )
        expected_csv = ",".join(allowed)
        expected_sync_csv = ",".join(sorted(syncable))
        app_constants = "\n".join(
            (
                f"{POLICY.CAPABILITY_NAME}:\n  true,",
                "NAVIS_REMOTE_SETTINGS_ALLOWED_COLLECTIONS:",
                f'Object.freeze("{expected_csv}".split(",")),',
                "NAVIS_REMOTE_SETTINGS_SYNC_COLLECTIONS:",
                f'Object.freeze("{expected_sync_csv}".split(",")),',
            )
        )
        attachments = {
            "defaults/settings/blocklists/addons-bloomfilters/addons-mlbf.bin": b"a",
            "defaults/settings/blocklists/addons-bloomfilters/addons-mlbf.bin.meta.json": b"{}",
            "defaults/settings/blocklists/addons-bloomfilters/softblocks-addons-mlbf.bin": b"b",
            "defaults/settings/blocklists/addons-bloomfilters/softblocks-addons-mlbf.bin.meta.json": b"{}",
        }

        with tempfile.TemporaryDirectory(prefix="navis-rs-runtime-test.") as root:
            runtime = Path(root) / "runtime"
            runtime.mkdir()

            def write_omni(extra_dump: bool) -> None:
                with zipfile.ZipFile(runtime / "omni.ja", "w") as archive:
                    for identifier in packaged:
                        archive.writestr(
                            f"defaults/settings/{identifier}.json", "{}"
                        )
                    if extra_dump:
                        archive.writestr(
                            "defaults/settings/main/example.json", "{}"
                        )
                    archive.writestr(
                        "defaults/settings/last_modified.json",
                        json.dumps(
                            {identifier: 1 for identifier in expected_timestamps}
                        ),
                    )
                    archive.writestr("defaults/pref/navis.js", prefs)
                    archive.writestr("modules/AppConstants.sys.mjs", app_constants)
                    for path, content in attachments.items():
                        archive.writestr(path, content)

            write_omni(extra_dump=False)
            POLICY.verify_runtime(runtime, policy, allowed, packaged, syncable)

            write_omni(extra_dump=True)
            with self.assertRaisesRegex(
                POLICY.PolicyError, "bootstrap dump inventory differs"
            ):
                POLICY.verify_runtime(
                    runtime, policy, allowed, packaged, syncable
                )


if __name__ == "__main__":
    unittest.main()
