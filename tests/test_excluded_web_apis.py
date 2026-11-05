# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('excluded', ROOT / 'navis/scripts/verify-excluded-web-apis.py')
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)
sys.path.insert(0, str(ROOT / 'runtime/gecko/python/mozbuild'))

class ExcludedWebAPIs(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='navis-excluded-apis-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        paths = ['runtime/mozconfig.' + name for name in POLICY.PROFILES]
        paths += ['runtime/gecko/' + name for name in (
            'dom/moz.build', 'gfx/moz.build', 'dom/credentialmanagement/moz.build',
            'toolkit/components/moz.build', 'dom/webidl/moz.build', 'toolkit/moz.configure',
            'toolkit/components/cleardata/ClearDataService.sys.mjs', 'widget/windows/moz.build')]
        paths += ['runtime/gecko/' + name for name in POLICY.CONDITIONAL_PROTOCOLS]
        paths += ['runtime/gecko/dom/webidl/' + name + '.webidl' for name in (
            'Navigator', 'WorkerNavigator', 'CredentialManagement', 'Permissions', 'Window', 'WebGLRenderingContext')]
        for name in paths:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)

    def check(self):
        failures = []
        POLICY.verify_source(self.root, failures)
        return failures

    def test_selected_source_and_retained_bindings(self):
        self.assertEqual(self.check(), [])

    def test_windows_occlusion_cannot_rejoin_unified_shutdown_state(self):
        path = self.root / 'runtime/gecko/widget/windows/moz.build'
        source = path.read_text().replace('    "WinWindowOcclusionTracker.cpp",\n', '')
        path.write_text(source + '\nUNIFIED_SOURCES += ["WinWindowOcclusionTracker.cpp"]\n')
        self.assertTrue(any('occlusion shutdown state' in error for error in self.check()))

    def test_clear_site_data_keeps_valid_private_fields_after_preprocessing(self):
        path = self.root / 'runtime/gecko/toolkit/components/cleardata/ClearDataService.sys.mjs'
        text = POLICY.preprocess(path, ROOT / 'runtime/gecko')
        self.assertNotIn('IdentityCredentialStorageService', text)
        subprocess.run(['node', '--input-type=module', '--check'], input=text,
                       text=True, capture_output=True, check=True)

    def test_conditional_protocols_cannot_bypass_preprocessing(self):
        for relative, protocols in POLICY.CONDITIONAL_PROTOCOLS.items():
            path = self.root / 'runtime/gecko' / relative
            original = path.read_text()
            for protocol in protocols:
                with self.subTest(protocol=protocol):
                    path.write_text(original.replace(f'    "{protocol}",\n', '') +
                                    f'\nIPDL_SOURCES += ["{protocol}"]\n')
                    self.assertTrue(any('conditional protocol' in error for error in self.check()))
            path.write_text(original)

    def test_each_profile_must_remove_each_graph(self):
        for profile in POLICY.PROFILES:
            path = self.root / ('runtime/mozconfig.' + profile)
            original = path.read_text()
            for option in POLICY.FLAGS.values():
                with self.subTest(profile=profile, option=option):
                    path.write_text(original.replace('--disable-' + option, '--enable-' + option))
                    self.assertTrue(self.check())
            path.write_text(original)

    def test_unconditional_directory_and_binding_fail(self):
        for name, addition in [('dom/moz.build', '\nDIRS += ["midi"]\n'),
                               ('dom/webidl/moz.build', '\nWEBIDL_FILES += ["PaymentRequest.webidl"]\n')]:
            path = self.root / 'runtime/gecko' / name
            original = path.read_text()
            path.write_text(original + addition)
            self.assertTrue(self.check())
            path.write_text(original)

    def test_navigator_member_cannot_survive_or_be_replaced_by_custom_engine(self):
        path = self.root / 'runtime/gecko/dom/webidl/Navigator.webidl'
        path.write_text(path.read_text() + '\npartial interface Navigator { readonly attribute Serial serial; };\n')
        self.assertTrue(self.check())
        path = path.with_name('USB.webidl')
        path.write_text('[Exposed=Window] interface USB {};')
        self.assertTrue(any('custom USB' in e for e in self.check()))

    def test_package_checks_real_archive_inventory_and_capability_facts(self):
        package = self.root / 'package'
        package.mkdir()
        constants = '\n'.join(name + ': false,' for name in POLICY.FLAGS)
        def write(extra=None, values=constants):
            with zipfile.ZipFile(package / 'omni.ja', 'w') as archive:
                archive.writestr('modules/AppConstants.sys.mjs', values)
                if extra:
                    archive.writestr(extra, '')
        def check():
            failures = []
            POLICY.verify_runtime(package, failures)
            return failures
        write()
        self.assertEqual(check(), [])
        write('modules/DesktopIdentityPrompt.sys.mjs')
        self.assertTrue(check())
        write(values=constants.replace('MOZ_WEBXR: false', 'MOZ_WEBXR: true'))
        self.assertTrue(check())

    def test_package_accepts_gecko_line_markers_between_capability_and_value(self):
        path = self.root / 'AppConstants.sys.mjs'
        path.write_text('export var AppConstants = {\n' + ''.join(
            f'{name}:\n#ifdef {name}\ntrue,\n#else\nfalse,\n#endif\n'
            for name in POLICY.FLAGS) + '};\n')
        constants = POLICY.preprocess(path, ROOT / 'runtime/gecko')
        self.assertIn('//@line', constants)
        package = self.root / 'package'
        package.mkdir()
        for values, valid in ((constants, True),
                              (constants.replace('false,', 'true,', 1), False)):
            with self.subTest(valid=valid):
                with zipfile.ZipFile(package / 'omni.ja', 'w') as archive:
                    archive.writestr('modules/AppConstants.sys.mjs', values)
                failures = []
                POLICY.verify_runtime(package, failures)
                self.assertEqual(failures == [], valid, failures)

    def test_configured_generator_input_is_authoritative(self):
        obj = self.root / 'obj'
        (obj / 'dom/bindings').mkdir(parents=True)
        (obj / 'config.status.json').write_text(json.dumps({'substs': {}}))
        path = obj / 'dom/bindings/file-lists.json'
        names = ['WebAuthentication.webidl', 'MediaDevices.webidl', 'Worker.webidl']
        def check():
            failures = []
            POLICY.verify_objdir(obj, failures)
            return failures
        path.write_text(json.dumps({'webidls': names}))
        self.assertEqual(check(), [])
        path.write_text(json.dumps({'webidls': names + ['WebXR.webidl']}))
        self.assertTrue(check())
        path.write_text(json.dumps({'webidls': names}))
        (obj / 'config.status.json').write_text(json.dumps({'substs': {'MOZ_WEB_PAYMENTS': True}}))
        self.assertTrue(check())

if __name__ == '__main__':
    unittest.main()
