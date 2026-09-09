# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / "scripts/verify-spellcheck.py"
SOURCE = ROOT / "../runtime/gecko/extensions/spellcheck/locales/en-US/hunspell"
REGISTRY = b'{"dictionaries":{"en-US":"dictionaries/en-US.dic"}}'


class SpellcheckPolicyTests(unittest.TestCase):
    def run_verifier(self, runtime: Path | None = None) -> subprocess.CompletedProcess[str]:
        command = ["python3", str(VERIFIER)]
        if runtime is not None:
            command.extend(["--runtime", str(runtime)])
        return subprocess.run(command, cwd=ROOT, text=True, capture_output=True)

    def make_runtime(self, root: Path) -> Path:
        dictionaries = root / "dictionaries"
        dictionaries.mkdir()
        for name in ("en-US.aff", "en-US.dic"):
            shutil.copyfile(SOURCE / name, dictionaries / name)
        modules = root / "modules"
        modules.mkdir()
        (modules / "built_in_addons.json").write_bytes(REGISTRY)
        return root

    def make_omni_runtime(self, root: Path) -> Path:
        with zipfile.ZipFile(root / "omni.ja", "w") as archive:
            archive.writestr("modules/built_in_addons.json", REGISTRY)
            for name in ("en-US.aff", "en-US.dic"):
                archive.write(SOURCE / name, f"dictionaries/{name}")
        return root

    def test_source_policy_is_self_consistent(self) -> None:
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_pinned_runtime_dictionary_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.make_runtime(Path(temporary))
            result = self.run_verifier(runtime)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_pinned_omnijar_dictionary_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.make_omni_runtime(Path(temporary))
            result = self.run_verifier(runtime)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_tampered_or_extra_runtime_dictionary_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.make_runtime(Path(temporary))
            (runtime / "dictionaries/en-US.dic").write_bytes(b"1\ntampered\n")
            (runtime / "dictionaries/extra.dic").write_bytes(b"0\n")
            result = self.run_verifier(runtime)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("runtime dictionary inventory differs", result.stderr)
            self.assertIn("runtime dictionary bytes differ", result.stderr)

    def test_missing_runtime_dictionary_registry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.make_runtime(Path(temporary))
            (runtime / "modules/built_in_addons.json").unlink()
            result = self.run_verifier(runtime)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "runtime must contain exactly one built-in dictionary registry",
                result.stderr,
            )

    def test_duplicate_external_and_omnijar_dictionary_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.make_runtime(Path(temporary))
            self.make_omni_runtime(runtime)
            result = self.run_verifier(runtime)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "runtime must contain exactly one built-in dictionary registry",
                result.stderr,
            )
            self.assertIn("runtime dictionary is duplicated", result.stderr)


if __name__ == "__main__":
    unittest.main()
