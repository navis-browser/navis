#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Release-time freshness and fail-closed certificate-policy regression tests."""

import copy
import datetime as dt
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "certificate_security", Path(__file__).resolve().parents[1] / "scripts/verify-certificate-security.py")
SECURITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SECURITY)


class CertificateSecurityTests(unittest.TestCase):
    def test_upstream_baseline_and_complete_roots_are_required(self):
        pin = {"remote": "https://github.com/mozilla-firefox/firefox.git",
               "tag": "FIREFOX_153_5_1esr_RELEASE",
               "build_tag": "FIREFOX_153_5_1esr_BUILD1", "commit": "a" * 40}
        header = ('#define NSS_BUILTINS_LIBRARY_VERSION_MAJOR 2\n'
                  '#define NSS_BUILTINS_LIBRARY_VERSION_MINOR 92\n'
                  '#define NSS_BUILTINS_LIBRARY_VERSION "2.92"\n')
        manifest = {
            "schema_version": 2, "gecko": {**pin, "nss_version": "3.125"},
            "trust": {"scope": "complete-upstream-snapshot", "sha256": "b" * 64,
                      "upstream_sha256": "b" * 64,
                      "version_path": "security/nss/lib/ckfw/builtins/nssckbi.h",
                      "version_sha256": hashlib.sha256(header.encode()).hexdigest(),
                      "library_version": "2.92"},
        }
        with tempfile.TemporaryDirectory() as directory:
            gecko = Path(directory)
            nss = gecko / "security/nss/lib/nss/nss.h"
            nss.parent.mkdir(parents=True)
            nss.write_text('#define NSS_VERSION "3.125"\n')
            version = gecko / manifest["trust"]["version_path"]
            version.parent.mkdir(parents=True)
            version.write_text(header)
            SECURITY.verify_baseline(manifest, pin, gecko)
            with self.assertRaises(SECURITY.SecurityDataError):
                SECURITY.verify_baseline(manifest, {}, gecko)
            changes = [
                ("schema_version", None, 1),
                ("gecko", "commit", "c" * 40),
                ("gecko", "tag", "FIREFOX_153_1esr_RELEASE"),
                ("gecko", "nss_version", "3.126"),
                ("trust", "scope", "selected-root-removal"),
                ("trust", "sha256", "c" * 64),
                ("trust", "removed_roots", ["selected root"]),
                ("trust", "version_sha256", "c" * 64),
                ("trust", "library_version", "2.90"),
                ("trust", "version_path", "security/nss/other.h"),
            ]
            for field, key, value in changes:
                changed = copy.deepcopy(manifest)
                if key is None:
                    changed[field] = value
                else:
                    changed[field][key] = value
                with self.subTest(field=field, key=key), self.assertRaises(SECURITY.SecurityDataError):
                    SECURITY.verify_baseline(changed, pin, gecko)
            # A self-consistent hash must not hide a header/list version mismatch.
            altered = header.replace('MINOR 92', 'MINOR 90')
            version.write_text(altered)
            changed = copy.deepcopy(manifest)
            changed["trust"]["version_sha256"] = hashlib.sha256(altered.encode()).hexdigest()
            with self.assertRaises(SECURITY.SecurityDataError):
                SECURITY.verify_baseline(changed, pin, gecko)

    def test_source_meets_current_security_contract(self):
        SECURITY.verify_source(dt.datetime.now(dt.timezone.utc))

    def test_freshness_threshold_is_inclusive_and_time_sensitive(self):
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        expiration = (now + dt.timedelta(days=42)).isoformat()
        self.assertEqual(SECURITY.check_expiry(expiration, now), 42)
        with self.assertRaises(SECURITY.SecurityDataError):
            SECURITY.check_expiry(expiration, now + dt.timedelta(microseconds=1))

    def test_expired_or_malformed_metadata_cannot_be_released(self):
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        for expiration in (None, "2025-12-31T23:59:59Z", "not-a-date", "2026-10-01T00:00:00"):
            with self.subTest(expiration=expiration), self.assertRaises(SECURITY.SecurityDataError):
                SECURITY.check_expiry(expiration, now)

    def test_legacy_disabled_ct_or_exception_policy_cannot_pass(self):
        with self.assertRaises(SECURITY.SecurityDataError):
            SECURITY.locked_policy('pref("security.pki.certificate_transparency.mode", 0);')

    def test_android_security_modules_resolve_startup_imports(self):
        source = (SECURITY.ROOT / "runtime/gecko/mobile/shared/modules/navis/moz.build").read_text()
        SECURITY.verify_android_security_modules(source)
        with self.assertRaises(SECURITY.SecurityDataError):
            SECURITY.verify_android_security_modules(source.replace(
                "EXTRA_JS_MODULES += [", "EXTRA_JS_MODULES.navis += [", 1))

    def test_android_security_module_order_is_buildable(self):
        source = (SECURITY.ROOT / "runtime/gecko/mobile/shared/modules/navis/moz.build").read_text()
        SECURITY.verify_android_security_modules(source)
        modules = ('        "/desktop-embedder/modules/CertificateSecurity.sys.mjs",\n'
                   '        "/desktop-embedder/modules/CertificateSecurityData.sys.mjs",\n')
        self.assertIn(modules, source)
        with self.assertRaises(SECURITY.SecurityDataError):
            SECURITY.verify_android_security_modules(source.replace(
                modules, "".join(reversed(modules.splitlines(keepends=True)))))

    def test_error_pages_do_not_depend_on_a_firefox_mitm_probe(self):
        key = "security.certerrors.mitm.priming.enabled"
        required = f'pref("{key}", false, locked);'
        for path in (SECURITY.ROOT / "platform/gecko-chrome/app/profile/navis.js",
                     SECURITY.ROOT / "runtime/gecko/mobile/android/app/navis-prefs.js"):
            original = path.read_text()
            for replacement in ("", f'pref("{key}", false);', f'pref("{key}", true, locked);'):
                with self.subTest(path=path, replacement=replacement):
                    with self.assertRaises(SECURITY.SecurityDataError):
                        SECURITY.locked_policy(original.replace(required, replacement))

    def test_new_metadata_beside_a_stale_native_engine_is_rejected(self):
        digest = "abcdef01" * 8
        manifest = {"onecrl": {"sha256": digest}}
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / "xul"
            identity = SECURITY.generator().security_data_identity(manifest)
            native.write_bytes(b"\0native-verifier\0" + identity.encode() + b"\0")
            SECURITY.verify_native(native, manifest)
            for field in ("ct", "trust"):
                changed = {**manifest, field: {"sha256": "d" * 64}}
                with self.subTest(field=field), self.assertRaises(SECURITY.SecurityDataError):
                    SECURITY.verify_native(native, changed)
            with self.assertRaises(SECURITY.SecurityDataError):
                SECURITY.verify_native(native, {**manifest, "gecko": {"commit": "a" * 40}})
            native.write_bytes(b"\0native-verifier\0" + b"abcdef02" * 8 + b"\0")
            with self.assertRaises(SECURITY.SecurityDataError):
                SECURITY.verify_native(native, manifest)

    def test_onecrl_marker_alone_cannot_certify_new_ct_or_trust_data(self):
        manifest = {"onecrl": {"sha256": "a" * 64},
                    "ct": {"sha256": "b" * 64}, "trust": {"sha256": "c" * 64}}
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / "xul"
            native.write_bytes(manifest["onecrl"]["sha256"].encode())
            with self.assertRaises(SECURITY.SecurityDataError):
                SECURITY.verify_native(native, manifest)


if __name__ == "__main__":
    unittest.main()
