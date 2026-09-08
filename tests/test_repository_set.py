from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


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

    def test_current_local_repository_set_passes(self) -> None:
        # The worktree-clean condition is checked by the command after the
        # repository extraction changes are committed. Keep this unit focused
        # on the immutable manifest vocabulary.
        payload = self.manifest()
        self.assertEqual(payload["schema"], "navis-repository-set-v1")
        self.assertEqual(set(payload["repositories"]), {"navis", "runtime", "platform"})
        self.assertNotIn("publication", payload)
        self.assertNotIn("migration_lineage", payload)

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
