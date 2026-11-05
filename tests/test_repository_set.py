# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


NAVIS = Path(__file__).resolve().parents[1]
SCRIPT = NAVIS / "scripts/verify-repository-set.py"
SPEC = importlib.util.spec_from_file_location("verify_repository_set", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RepositorySetManifestTests(unittest.TestCase):
    def manifest(self) -> dict:
        return json.loads(
            (NAVIS / "config/navis-repository-set.json").read_text(encoding="utf-8")
        )

    def test_manifest_vocabulary(self) -> None:
        # The worktree-clean condition is checked by the command after the
        # repository extraction changes are committed. Keep this unit focused
        # on the immutable manifest vocabulary.
        payload = self.manifest()
        self.assertEqual(payload["schema"], "navis-repository-set-v1")
        self.assertEqual(set(payload["repositories"]), {"navis", "runtime", "platform"})
        self.assertNotIn("publication", payload)
        self.assertNotIn("migration_lineage", payload)

    def verify_with_checkout(self, payload: dict, *, android_present: bool) -> dict:
        # Isolate Git state while exercising the real manifest and platform
        # validation. An uncommitted developer checkout is not a test fixture.
        def cat_file(arguments, **_kwargs):
            present = android_present if arguments[-1] == "HEAD:android" else False
            return subprocess.CompletedProcess(arguments, 0 if present else 1)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config/navis-repository-set.json"
            path.parent.mkdir()
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(MODULE, "verify_repository", return_value={}), \
                    mock.patch.object(MODULE.subprocess, "run", side_effect=cat_file):
                return MODULE.verify(path)

    def test_supported_product_versions_pass_verification(self) -> None:
        for version in ("0.1.0", "0.2.0-dev", "0.3.0-dev"):
            with self.subTest(version=version):
                payload = self.manifest()
                payload["product_version"] = version
                result = self.verify_with_checkout(
                    payload, android_present=version != "0.1.0"
                )
                self.assertEqual(set(result), {"navis", "runtime", "platform"})

    def test_unsupported_product_versions_are_rejected(self) -> None:
        for version in (None, "", "0.3.0", "0.4.0-dev"):
            with self.subTest(version=version):
                payload = self.manifest()
                payload["product_version"] = version
                with self.assertRaisesRegex(
                    MODULE.RepositorySetError, "product version differs"
                ):
                    self.verify_with_checkout(payload, android_present=True)

    def test_android_presence_must_match_product_version(self) -> None:
        for version in ("0.1.0", "0.2.0-dev", "0.3.0-dev"):
            with self.subTest(version=version):
                payload = self.manifest()
                payload["product_version"] = version
                with self.assertRaisesRegex(
                    MODULE.RepositorySetError, "presence differs"
                ):
                    self.verify_with_checkout(
                        payload, android_present=version == "0.1.0"
                    )

    def test_duplicate_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "set.json"
            path.write_text('{"schema":"one","schema":"two"}\n', encoding="utf-8")
            with self.assertRaisesRegex(MODULE.RepositorySetError, "duplicate JSON key"):
                MODULE.load_manifest(path)

    def test_repository_paths_are_fixed(self) -> None:
        payload = self.manifest()
        self.assertEqual(payload["repositories"]["navis"]["path"], ".")
        self.assertEqual(payload["repositories"]["runtime"]["path"], "../runtime")
        self.assertEqual(payload["repositories"]["platform"]["path"], "../platform")


if __name__ == "__main__":
    unittest.main()
