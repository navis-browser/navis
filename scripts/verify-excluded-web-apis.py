#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Audit deliberate Web API exclusions in source, configured bindings and packages."""
from __future__ import annotations
import argparse
import ast
import io
import json
from pathlib import Path
import re
import sys
import zipfile

WORKSPACE = Path(__file__).resolve().parents[2]
FLAGS = {
    'MOZ_WEB_DEVICE_APIS': 'web-device-apis',
    'MOZ_WEB_IDENTITY': 'web-identity',
    'MOZ_WEB_PAYMENTS': 'web-payments',
    'MOZ_WEBXR': 'webxr',
}
PROFILES = ('runtime', 'runtime.release', 'win64', 'win64.release',
            'android-aarch64.sccache', 'linux-test-fixtures')
BINDINGS = re.compile(r'^(?:MIDI\w*|Serial(?:Port)?|HID\w*|USB\w*|Bluetooth\w*|'
                      r'VR\w*|WebXR|XR\w*|BasicCard\w*|MerchantValidationEvent|Payment\w*|'
                      r'IdentityCredential|DigitalCredential|LoginStatus)\.webidl$')
MODULES = re.compile(r'(?:^|/)(?:DesktopIdentityPrompt|DesktopDeviceUse|WorkerDeviceUse(?:State)?|'
                     r'NavisAndroid(?:IdentityPrompt|HIDPrompt|DigitalCredentialService|WorkerDeviceUse)|'
                     r'GeckoViewIdentityCredential|IdentityCredentialPromptService|CredentialChooserService|'
                     r'XR(?:Parent|Child)|(?:HID|USB|Bluetooth)(?:Parent|Child))\.sys\.mjs$|'
                     r'web-bluetooth-registries\.txt$|background-device-activity\.mjs$')
MEMBERS = re.compile(r'\b(?:requestMIDIAccess|getVRDisplays|activeVRDisplays|Serial|XRSystem|'
                     r'IdentityCredentialRequestOptions|DigitalCredentialRequestOptions|'
                     r'DigitalCredentialCreationOptions|NavigatorLogin|makeXRCompatible|xrCompatible|'
                     r'onvrdisplay\w*|MidiPermissionDescriptor)\b')
CONDITIONAL_PROTOCOLS = {
    'ipc/glue/moz.build': ('PBackground.ipdl',),
    'dom/ipc/moz.build': ('PBrowser.ipdl', 'PContent.ipdl', 'PWindowGlobal.ipdl'),
    'gfx/ipc/moz.build': ('PGPU.ipdl',),
    'gfx/layers/moz.build': ('ipc/PCompositorBridge.ipdl',),
}

def require(failures, condition, message):
    if not condition:
        failures.append(message)


def selected_literals(source: str) -> list[str]:
    """Conservatively include both unknown branches; evaluate only our four gates."""
    def condition(node):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'CONFIG':
            if isinstance(node.slice, ast.Constant) and node.slice.value in FLAGS:
                return False
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            value = condition(node.operand)
            return not value if value is not None else None
        return None
    def walk(node):
        if isinstance(node, ast.If):
            value = condition(node.test)
            for child in (node.body if value is True else node.orelse if value is False else node.body + node.orelse):
                yield from walk(child)
        elif isinstance(node, ast.With) and any(isinstance(item.context_expr, ast.Call) and isinstance(item.context_expr.func, ast.Name) and item.context_expr.func.id == "Files" for item in node.items):
            return
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        else:
            for child in ast.iter_child_nodes(node):
                yield from walk(child)
    return list(walk(ast.parse(source)))


def preprocess(path: Path, gecko: Path) -> str:
    sys.path.insert(0, str(gecko / 'python/mozbuild'))
    from mozbuild.preprocessor import Preprocessor
    output = io.StringIO()
    pp = Preprocessor({'MOZ_WEBRTC': 1, 'MOZ_DESKTOP_EMBEDDER': 1})
    with path.open() as source:
        pp.processFile(source, output)
    return output.getvalue()


def declared_files(path: Path, names: tuple[str, ...]) -> dict[str, set[str]]:
    declarations = {name: set() for name in names}
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in declarations:
                declarations[target.id].update(value.value for value in ast.walk(node.value)
                                               if isinstance(value, ast.Constant) and isinstance(value.value, str))
    return declarations


def verify_protocol_preprocessing(gecko: Path, failures: list[str]) -> None:
    for relative, protocols in CONDITIONAL_PROTOCOLS.items():
        declarations = declared_files(gecko / relative, ('IPDL_SOURCES', 'PREPROCESSED_IPDL_SOURCES'))
        for protocol in protocols:
            require(failures, protocol in declarations['PREPROCESSED_IPDL_SOURCES'] and
                    protocol not in declarations['IPDL_SOURCES'],
                    f'{relative}: conditional protocol must be preprocessed: {protocol}')


def verify_source(workspace: Path, failures: list[str]) -> None:
    gecko = workspace / 'runtime/gecko'
    verify_protocol_preprocessing(gecko, failures)
    windows = declared_files(gecko / 'widget/windows/moz.build', ('SOURCES', 'UNIFIED_SOURCES'))
    require(failures, 'WinWindowOcclusionTracker.cpp' in windows['SOURCES'] and
            'WinWindowOcclusionTracker.cpp' not in windows['UNIFIED_SOURCES'],
            'widget/windows/moz.build: occlusion shutdown state requires separate compilation')
    for profile in PROFILES:
        path = workspace / 'runtime' / ('mozconfig.' + profile)
        content = path.read_text()
        for option in FLAGS.values():
            require(failures, content.splitlines().count('ac_add_options --disable-' + option) == 1,
                    f'{path}: missing unique graph exclusion {option}')
            require(failures, '--enable-' + option not in content, f'{path}: re-enables {option}')
    for relative, forbidden in {
        'dom/moz.build': {'midi', 'webserial', 'webusb', 'webhid', 'webbluetooth', 'vr', 'payments'},
        'gfx/moz.build': {'vr'},
        'dom/credentialmanagement/moz.build': {'digital', 'identity'},
        'toolkit/components/moz.build': {'credentialmanagement'},
    }.items():
        selected = selected_literals((gecko / relative).read_text())
        require(failures, not forbidden.intersection(selected), f'{relative}: excluded directory remains selected')
    selected = selected_literals((gecko / 'dom/webidl/moz.build').read_text())
    require(failures, not any(BINDINGS.fullmatch(Path(s).name) for s in selected),
            'excluded WebIDL remains in disabled build declarations')
    require(failures, all(name in selected for name in ('WebAuthentication.webidl', 'MediaDevices.webidl', 'Worker.webidl')),
            'retained WebAuthn/media/Worker bindings missing')
    for name in ('Navigator', 'WorkerNavigator', 'CredentialManagement', 'Permissions', 'Window', 'WebGLRenderingContext'):
        text = preprocess(gecko / f'dom/webidl/{name}.webidl', gecko)
        require(failures, not MEMBERS.search(text), f'{name}: excluded member survives preprocessing')
    clear_data = preprocess(gecko / 'toolkit/components/cleardata/ClearDataService.sys.mjs', gecko)
    require(failures, 'IdentityCredentialStorageService' not in clear_data,
            'site data clearing retains the excluded identity storage service')
    for kind in ('HID', 'USB', 'Bluetooth'):
        require(failures, not list((gecko / 'dom/webidl').glob(kind + '*.webidl')),
                f'custom {kind} bindings remain in generated source')
    configure = (gecko / 'toolkit/moz.configure').read_text()
    for flag, option in FLAGS.items():
        require(failures, f'"--enable-{option}"' in configure and f'set_config("{flag}",' in configure,
                f'configure declaration missing: {flag}')
    for folder in ('runtime/embedder', 'platform/gecko-chrome', 'platform/android/src/main'):
        for path in (workspace / folder).rglob('*'):
            if path.is_file() and MODULES.search(path.as_posix()):
                failures.append(f'product input contains excluded module: {path}')


def verify_objdir(objdir: Path, failures: list[str]) -> None:
    config = json.loads((objdir / 'config.status.json').read_text())['substs']
    for flag in (*FLAGS, 'MOZ_MIDIR'):
        require(failures, not config.get(flag), f'configured capability remains enabled: {flag}')
    files = json.loads((objdir / 'dom/bindings/file-lists.json').read_text())['webidls']
    require(failures, not any(BINDINGS.fullmatch(Path(s).name) for s in files),
            'excluded WebIDL in configured generator input')
    names = {Path(s).name for s in files}
    require(failures, {'WebAuthentication.webidl', 'MediaDevices.webidl', 'Worker.webidl'} <= names,
            'configured retained bindings missing')


def verify_omni(bundle: zipfile.ZipFile, failures: list[str], label: str) -> None:
    for name in bundle.namelist():
        require(failures, not MODULES.search(name), f'{label}: excluded packaged module {name}')
    try:
        constants = bundle.read('modules/AppConstants.sys.mjs').decode()
    except KeyError:
        failures.append(f'{label}: packaged capability constants missing')
        return
    # Gecko inserts standalone //@line comments between a property and its value.
    constants = re.sub(r'(?m)^[ \t]*//[^\n]*(?:\n|$)', '', constants)
    for flag in FLAGS:
        require(failures, bool(re.search(r'\b' + flag + r'\s*:\s*false\b', constants)),
                f'{label}: missing false AppConstants.{flag}')


def verify_runtime(runtime: Path, failures: list[str]) -> None:
    archives = list(runtime.rglob('omni.ja'))
    require(failures, bool(archives), 'packaged omni.ja missing')
    constants_found = False
    for archive in archives:
        with zipfile.ZipFile(archive) as bundle:
            if 'modules/AppConstants.sys.mjs' in bundle.namelist():
                constants_found = True
                verify_omni(bundle, failures, str(archive))
            else:
                for name in bundle.namelist():
                    require(failures, not MODULES.search(name), f'{archive}: excluded module {name}')
    require(failures, constants_found,
            'packaged capability constants missing')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=WORKSPACE)
    parser.add_argument('--objdir', type=Path)
    parser.add_argument('--runtime', type=Path)
    args = parser.parse_args()
    failures = []
    try:
        verify_source(args.workspace, failures)
        if args.objdir:
            verify_objdir(args.objdir, failures)
        if args.runtime:
            verify_runtime(args.runtime, failures)
    except (OSError, ValueError, KeyError, SyntaxError, zipfile.BadZipFile) as error:
        failures.append(str(error))
    for error in failures:
        print(error, file=sys.stderr)
    if not failures:
        print('Excluded Web APIs: source/configured-bindings/package checks passed for selected inputs')
    return bool(failures)

if __name__ == '__main__':
    raise SystemExit(main())
