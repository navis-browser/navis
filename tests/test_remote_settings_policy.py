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
    def test_repository_policy_is_consistent(self) -> None:
        policy, allowed, packaged = POLICY.validate_policy()
        POLICY.verify_product_declaration(policy, allowed)
        POLICY.verify_semantic_port()
        self.assertEqual(len(allowed), 10)
        self.assertEqual(len(packaged), 8)

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

    def test_runtime_inventory_rejects_an_extra_dump(self) -> None:
        policy, allowed, packaged = POLICY.validate_policy()
        expected_timestamps = {
            identifier
            for identifier in allowed
            if (
                WORKSPACE / f"gecko/services/settings/dumps/{identifier}.json"
            ).is_file()
        }
        expected = ",".join(allowed)
        prefs = "\n".join(
            (
                f'pref("{POLICY.RUNTIME_PREF}", "{expected}", locked);',
                f'pref("{POLICY.SERVER_PREF}", "{policy["provider"]["production_server"]}", locked);',
                f'pref("{POLICY.PREVIEW_PREF}", false, locked);',
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
                    for path, content in attachments.items():
                        archive.writestr(path, content)

            write_omni(extra_dump=False)
            POLICY.verify_runtime(runtime, policy, allowed, packaged)

            write_omni(extra_dump=True)
            with self.assertRaisesRegex(
                POLICY.PolicyError, "bootstrap dump inventory differs"
            ):
                POLICY.verify_runtime(runtime, policy, allowed, packaged)


if __name__ == "__main__":
    unittest.main()
