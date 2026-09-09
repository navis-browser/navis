# SPDX-License-Identifier: MPL-2.0

"""Public packaging stays self-contained without a private lab workspace."""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile

NAVIS = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, NAVIS / 'scripts' / name)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class PublicInputsTest(unittest.TestCase):
    def test_build_script_dependencies_exist(self):
        for script in (NAVIS / 'scripts').glob('*.sh'):
            for target in re.findall(r'\$(?:navis_dir|workspace_dir)/scripts/([\w.-]+)',
                                     script.read_text()):
                self.assertTrue((NAVIS / 'scripts' / target).is_file(), (script.name, target))

    def test_artifact_security_gates_remain_required(self):
        for name in ('package-linux.sh', 'package-win64.sh'):
            source = (NAVIS / 'scripts' / name).read_text()
            for gate in ('verify-runtime-package.sh', 'verify-desktop-build-id.py',
                         'verify-builtin-extensions.py', 'verify-webdriver-boundary.py',
                         'verify-runtime-capabilities.py', 'audit-runtime-source-domains.py'):
                self.assertIn(gate, source, (name, gate))
        android = NAVIS / 'scripts/package-android-candidate.sh'
        if android.exists():
            for gate in ('verify-source-freeze.py', 'verify-android-runtime-build-graph.py',
                         'verify-android-package.py', 'verify-incremental-objdir.py'):
                self.assertIn(gate, android.read_text())

    def test_source_roots_are_real_repositories_not_compatibility_aliases(self):
        for alias in ('product', 'embedder', 'core', 'patches', 'vendor', 'android'):
            self.assertFalse((NAVIS / alias).exists(), alias)
        for relative in ('../runtime/scripts/package-embedder-source.py',
                         '../runtime/scripts/verify-core-abi.py',
                         '../platform/gecko-chrome/chrome/content/main.mjs'):
            self.assertTrue((NAVIS / relative).is_file(), relative)

    def test_spellcheck_fails_closed_without_pinned_dictionary(self):
        spell = module('verify-spellcheck.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root / 'omni.ja', 'w') as archive:
                archive.writestr('modules/built_in_addons.json',
                                 '{"dictionaries":{"en-US":"dictionaries/en-US.dic"}}')
                archive.writestr('dictionaries/en-US.dic', b'untrusted')
                archive.writestr('dictionaries/en-US.aff', b'untrusted')
            with patch.object(spell, 'SOURCE_DICTIONARIES', {
                    name: root / name for name in ('en-US.aff', 'en-US.dic')}):
                failures = spell.verify_runtime(root)
            self.assertTrue(any('source dictionary is missing' in f for f in failures))


if __name__ == '__main__':
    unittest.main()
