# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("spellcheck_policy", ROOT / "scripts/verify-spellcheck.py")
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class SpellcheckPolicyTests(unittest.TestCase):
    def test_all_product_profiles_disable_builtin_spellchecking(self):
        profiles = sorted((ROOT.parent / "runtime").glob("mozconfig.*"))
        self.assertTrue(profiles)
        for profile in profiles:
            with self.subTest(profile=profile.name):
                self.assertIn("ac_add_options --disable-spellchecking", profile.read_text())
        self.assertEqual(POLICY.verify_source(), [])

    def test_runtime_without_spellchecking_resources_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(root / "omni.ja", "w") as archive:
                archive.writestr("modules/DesktopContextMenuChild.sys.mjs", "export class DesktopContextMenuChild {}")
            self.assertEqual(POLICY.verify_runtime(root), [])

    def test_packed_dictionary_payloads_are_rejected(self):
        for name in ("dictionaries/en-US.aff", "dictionaries/en-US.dic", "dictionaries/extra/other.dic"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with zipfile.ZipFile(root / "omni.ja", "w") as archive:
                    archive.writestr(name, "dictionary")
                failures = POLICY.verify_runtime(root)
                self.assertTrue(any("forbidden spellchecking resource" in failure for failure in failures), failures)

    def test_unpacked_dictionary_payloads_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "dictionaries").mkdir()
            (root / "dictionaries/other.aff").write_text("dictionary")
            self.assertTrue(any("forbidden spellchecking resource" in failure for failure in POLICY.verify_runtime(root)))

    def test_hyphenation_patterns_remain_available_for_line_breaking(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "hyphenation").mkdir()
            (root / "hyphenation/hyph_en_US.dic").write_text("UTF-8\nLEFTHYPHENMIN 2\n.ach4\n")
            with zipfile.ZipFile(root / "omni.ja", "w") as archive:
                archive.writestr("hyphenation/hyph_de-1996.dic", "UTF-8\n.ab3\n")
            self.assertEqual(POLICY.verify_runtime(root), [])

    def test_spelling_dictionaries_outside_the_hyphenation_namespace_are_rejected(self):
        for name in (
            "other/en-US.dic",
            "hyphenation/en-US.dic",
            "dictionaries/hyph_en_US.dic",
            "modules/hyphenation/hyph_en_US.dic",
            "hyphenation/sub/hyph_en_US.dic",
            "hyphenation/hyph_en_US.aff",
        ):
            with self.subTest(name=name):
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as archive:
                    archive.writestr(name, "spelling dictionary")
                failures = []
                with zipfile.ZipFile(data) as archive:
                    POLICY.verify_omni(archive, failures, "APK omni.ja")
                self.assertTrue(failures)

    def test_modules_actors_and_dictionary_registry_are_rejected(self):
        for name in (
            "modules/InlineSpellChecker.sys.mjs",
            "modules/InlineSpellCheckerContent.sys.mjs",
            "actors/InlineSpellCheckerParent.sys.mjs",
            "actors/InlineSpellCheckerChild.sys.mjs",
            "modules/built_in_addons.json",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with zipfile.ZipFile(root / "omni.ja", "w") as archive:
                    archive.writestr(name, "{}")
                failures = POLICY.verify_runtime(root)
                self.assertTrue(any("forbidden spellchecking resource" in failure for failure in failures), failures)

    def test_obsolete_suggestion_commands_are_rejected(self):
        for resource in ("modules/DesktopEngine.sys.mjs", "modules/DesktopContextMenuChild.sys.mjs", "chrome/navis/content/main.mjs"):
            with self.subTest(resource=resource), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with zipfile.ZipFile(root / "omni.ja", "w") as archive:
                    archive.writestr(resource, 'const command = "spell-replace-0";')
                failures = POLICY.verify_runtime(root)
                self.assertTrue(any("obsolete spelling command" in failure for failure in failures), failures)

    def test_corrupt_omnijar_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "omni.ja").write_bytes(b"not a zip archive")
            self.assertTrue(POLICY.verify_runtime(root))

    def test_android_omnijar_rejects_dictionary_and_removed_selection_dependency(self):
        for name, source in (
            ("dictionaries/en-US.dic", "dictionary"),
            ("actors/ContentDelegateChild.sys.mjs", 'const module = "resource://gre/modules/InlineSpellChecker.sys.mjs";'),
        ):
            with self.subTest(name=name):
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as archive:
                    archive.writestr(name, source)
                failures = []
                with zipfile.ZipFile(data) as archive:
                    POLICY.verify_omni(archive, failures, "APK omni.ja")
                self.assertTrue(failures)


if __name__ == "__main__":
    unittest.main()
