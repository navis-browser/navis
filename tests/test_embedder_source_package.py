from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


NAVIS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "embedder_source_package", NAVIS / "scripts/package-embedder-source.py"
)
assert SPEC is not None and SPEC.loader is not None
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class EmbedderSourcePackageTests(unittest.TestCase):
    def test_split_sources_without_compatibility_mounts(self) -> None:
        definition, _ = PACKAGE.validate_definition(PACKAGE.DEFAULT_DEFINITION)
        original = PACKAGE.collect_files(definition)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            # Copy only the declared inputs. No Gecko, Git, objects or legacy
            # symlinks exist in this isolated sibling layout.
            for entry in original:
                relative = entry["path"]
                owner = (
                    "runtime" if PACKAGE.source_root(relative) == PACKAGE.RUNTIME
                    else "navis"
                )
                target = workspace / owner / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PACKAGE.source_root(relative) / relative, target)
            self.assertFalse((workspace / "navis/core").exists())
            with patch.object(PACKAGE, "WORKSPACE", workspace / "navis"), patch.object(
                PACKAGE, "RUNTIME", workspace / "runtime"
            ):
                files = PACKAGE.collect_files(definition)
                self.assertEqual(files, original)
                manifest, contents = PACKAGE.prepare_source_manifest(definition, files)
                self.assertEqual(len(contents), len(files))
                self.assertEqual(manifest["aggregate_sha256"], PACKAGE.aggregate_hash(files))
                for entry in files:
                    self.assertEqual(
                        PACKAGE.sha256_bytes(contents[entry["path"]]), entry["sha256"]
                    )

    def test_owner_selection(self) -> None:
        for relative in (
            "core/rust/Cargo.lock",
            "embedder/modules/DesktopEngine.sys.mjs",
            "config/gecko-semantic-ports.json",
            "scripts/prepare-desktop-embedder.py",
        ):
            with self.subTest(path=relative):
                self.assertEqual(PACKAGE.source_root(relative), PACKAGE.RUNTIME)
        for relative in (
            "docs/desktop-embedder-api.md",
            "config/desktop-embedder-source-package.json",
            "scripts/package-embedder-source.py",
        ):
            with self.subTest(path=relative):
                self.assertEqual(PACKAGE.source_root(relative), PACKAGE.WORKSPACE)


if __name__ == "__main__":
    unittest.main()
