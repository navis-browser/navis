from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


NAVIS_ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = NAVIS_ROOT / "scripts/verify-source-freeze.py"
SPEC = importlib.util.spec_from_file_location("verify_source_freeze", VERIFIER_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class SourceFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for directory in VERIFIER.SOURCE_DIRECTORIES:
            (self.root / directory).mkdir(parents=True)
        for name in VERIFIER.TOP_LEVEL_FILES:
            (self.root / name).write_text(f"{name}\n", encoding="utf-8")
        (self.root / "runtime/mozconfig.runtime").write_text(
            "runtime\n", encoding="utf-8"
        )
        (self.root / "runtime/vendor/gecko.json").write_text(
            json.dumps({"commit": "a" * 40}) + "\n", encoding="utf-8"
        )
        (self.root / "navis/config/gecko-semantic-ports.json").write_text(
            json.dumps({"ports": [{"order": 1}]}) + "\n", encoding="utf-8"
        )
        desktop_source = self.root / "platform/gecko-chrome/source.txt"
        desktop_source.parent.mkdir(parents=True)
        desktop_source.write_text(
            "source\n", encoding="utf-8"
        )
        android_source = self.root / "platform/android/source.kt"
        android_source.parent.mkdir(parents=True)
        android_source.write_text(
            "source\n", encoding="utf-8"
        )
        ignored = self.root / "runtime/core/rust/target"
        ignored.mkdir(parents=True)
        (ignored / "derived.bin").write_bytes(b"derived")

    def manifest(self) -> dict:
        return VERIFIER.build_manifest(self.root, "2026-09-02T00:00:00Z")

    def write_manifest(self, payload: dict) -> Path:
        path = self.root / "work/source-freeze.json"
        VERIFIER.atomic_write(path, payload)
        return path

    def test_round_trip_accepts_exact_source(self) -> None:
        manifest = self.manifest()
        path = self.write_manifest(manifest)
        verified = VERIFIER.verify(self.root, path)
        self.assertEqual(verified["aggregate_sha256"], manifest["aggregate_sha256"])

    def test_transition_navis_root_resolves_to_canonical_workspace(self) -> None:
        manifest = self.manifest()
        path = self.write_manifest(manifest)
        verified = VERIFIER.verify(self.root / "navis", path)
        self.assertEqual(verified["aggregate_sha256"], manifest["aggregate_sha256"])

    def test_changed_source_is_rejected(self) -> None:
        path = self.write_manifest(self.manifest())
        (self.root / "platform/gecko-chrome/source.txt").write_text(
            "changed\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(VERIFIER.FreezeError, "differs"):
            VERIFIER.verify(self.root, path)

    def test_extra_source_is_rejected(self) -> None:
        path = self.write_manifest(self.manifest())
        (self.root / "navis/scripts/unreviewed.py").write_text(
            "pass\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(VERIFIER.FreezeError, "differs"):
            VERIFIER.verify(self.root, path)

    def test_changed_android_source_is_rejected(self) -> None:
        path = self.write_manifest(self.manifest())
        (self.root / "platform/android/source.kt").write_text(
            "changed\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(VERIFIER.FreezeError, "differs"):
            VERIFIER.verify(self.root, path)

    def test_generated_android_output_does_not_change_identity(self) -> None:
        manifest = self.manifest()
        path = self.write_manifest(manifest)
        generated = self.root / "platform/android/build/generated.bin"
        generated.parent.mkdir(parents=True)
        generated.write_bytes(b"derived")
        verified = VERIFIER.verify(self.root, path)
        self.assertEqual(verified["aggregate_sha256"], manifest["aggregate_sha256"])

    def test_derived_target_does_not_change_source_identity(self) -> None:
        manifest = self.manifest()
        path = self.write_manifest(manifest)
        (self.root / "runtime/core/rust/target/derived.bin").write_bytes(
            b"new-derived"
        )
        verified = VERIFIER.verify(self.root, path)
        self.assertEqual(verified["aggregate_sha256"], manifest["aggregate_sha256"])


if __name__ == "__main__":
    unittest.main()
