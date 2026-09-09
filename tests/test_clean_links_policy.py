#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


"""Unit tests for the executable Navis Clean Links policy."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent
TOOL = WORKSPACE / "scripts/verify-clean-links-policy.py"
SPEC = importlib.util.spec_from_file_location("clean_links_policy", TOOL)
assert SPEC and SPEC.loader
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class CleanLinksPolicyTest(unittest.TestCase):
    def test_repository_policy_is_consistent(self) -> None:
        policy = POLICY.validate_policy()
        POLICY.verify_consumers()
        POLICY.verify_semantic_port()
        self.assertEqual(policy["schemaVersion"], 1)
        self.assertEqual(len(POLICY.MOZILLA_SNAPSHOT_PARAMETERS), 22)
        self.assertEqual(len(POLICY.ALIBABA_PRODUCT_DOMAINS), 33)

    def test_global_spm_fails_closed(self) -> None:
        payload = json.loads(POLICY.POLICY_PATH.read_text(encoding="utf-8"))
        payload["rules"][0]["queryParameters"].append("spm")
        payload["rules"][0]["queryParameters"].sort()
        with tempfile.TemporaryDirectory(prefix="navis-clean-policy-test.") as root:
            mutated = Path(root) / "policy.json"
            mutated.write_text(json.dumps(payload), encoding="utf-8")
            original = POLICY.POLICY_PATH
            POLICY.POLICY_PATH = mutated
            try:
                with self.assertRaisesRegex(
                    POLICY.PolicyError, "snapshot changed|never be stripped globally"
                ):
                    POLICY.validate_policy()
            finally:
                POLICY.POLICY_PATH = original

    def test_runtime_rejects_live_query_stripping_data(self) -> None:
        policy = POLICY.validate_policy()
        prefs = "\n".join(
            (
                'pref("privacy.query_stripping.product_policy_uri", "chrome://navis/content/clean-links-policy.json", locked);',
                'pref("privacy.query_stripping.use_unified_rules", true, locked);',
                'pref("privacy.query_stripping.remote_settings.enabled", false, locked);',
            )
        )
        with tempfile.TemporaryDirectory(prefix="navis-clean-runtime-test.") as root:
            runtime = Path(root) / "runtime"
            runtime.mkdir()

            def write_omni(include_remote_data: bool) -> None:
                with zipfile.ZipFile(runtime / "omni.ja", "w") as archive:
                    archive.writestr(
                        "chrome/navis/content/clean-links-policy.json",
                        json.dumps(policy),
                    )
                    archive.writestr("defaults/pref/navis.js", prefs)
                    last_modified = {}
                    if include_remote_data:
                        last_modified["main/query-stripping"] = 1
                        archive.writestr(
                            "defaults/settings/main/query-stripping.json", "{}"
                        )
                    archive.writestr(
                        "defaults/settings/last_modified.json",
                        json.dumps(last_modified),
                    )

            write_omni(include_remote_data=False)
            POLICY.verify_runtime(runtime, policy)

            write_omni(include_remote_data=True)
            with self.assertRaisesRegex(
                POLICY.PolicyError, "contains main/query-stripping"
            ):
                POLICY.verify_runtime(runtime, policy)


if __name__ == "__main__":
    unittest.main()
