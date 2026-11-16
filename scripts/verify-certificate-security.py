#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Gate frozen certificate data, effective product policy and packaged identities."""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import importlib.util
import io
import json
import mmap
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UTC = dt.timezone.utc


class SecurityDataError(ValueError):
    pass


def check_expiry(expiration: str, now: dt.datetime, minimum_days: int = 42) -> float:
    try:
        if not isinstance(expiration, str):
            raise ValueError("expiration must be a UTC timestamp")
        expires = dt.datetime.fromisoformat(expiration.replace("Z", "+00:00"))
        if expires.utcoffset() != dt.timedelta(0) or now.utcoffset() != dt.timedelta(0):
            raise ValueError("timestamps must be UTC")
        remaining = (expires - now).total_seconds()
    except (TypeError, ValueError) as error:
        raise SecurityDataError("invalid CT expiration or check time") from error
    if remaining < minimum_days * 86400:
        raise SecurityDataError(f"CT metadata needs {minimum_days} days remaining; has {remaining / 86400:.2f}")
    return remaining / 86400


def generator():
    spec = importlib.util.spec_from_file_location(
        "certificate_generator", ROOT / "runtime/scripts/generate-certificate-security.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def locked_policy(text: str) -> None:
    for key, value in {
        "security.certerrors.mitm.priming.enabled": "false",
        "security.pki.certificate_transparency.mode": "2",
        "security.pki.certificate_transparency.disable_for_hosts": '""',
        "security.pki.certificate_transparency.disable_for_spki_hashes": '""',
        "security.remote_settings.intermediates.enabled": "false",
        "security.remote_settings.crlite_filters.enabled": "false",
        "security.pki.crlite_mode": "0",
    }.items():
        if not re.search(rf'pref\("{re.escape(key)}",\s*{re.escape(value)},\s*locked\);', text):
            raise SecurityDataError(f"missing locked certificate policy: {key}")


def desktop_error_defaults(text: str) -> None:
    # The upstream error-page action reads this Firefox-owned default without
    # a fallback. It controls persistence after explicit consent, not TLS trust.
    entries = re.findall(r'^pref\("security\.certerrors\.permanentOverride",\s*([^;]+)\);', text, re.M)
    if entries != ["true"]:
        raise SecurityDataError("missing desktop certificate-exception persistence default")


def verify_android_security_modules(source: str) -> None:
    required = {f"/desktop-embedder/modules/{name}.sys.mjs"
                for name in ("CertificateSecurity", "CertificateSecurityData")}
    root_modules = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AugAssign):
            continue
        target = node.target
        root = isinstance(target, ast.Name) and target.id == "EXTRA_JS_MODULES"
        namespace = (isinstance(target, ast.Attribute)
                     and isinstance(target.value, ast.Name)
                     and target.value.id == "EXTRA_JS_MODULES")
        if not (root or namespace):
            continue
        entries = ast.literal_eval(node.value)
        if entries != sorted(entries, key=str.lower):
            raise SecurityDataError("Android security module manifest violates moz.build ordering")
        if root:
            root_modules.update(entries)
        elif required.intersection(entries):
            raise SecurityDataError("Android security modules must use the startup import namespace")
    if not required.issubset(root_modules):
        raise SecurityDataError("Android security modules are absent from the startup import namespace")


def verify_baseline(manifest: dict, pin: dict, gecko: Path) -> None:
    if manifest.get("schema_version") != 2:
        raise SecurityDataError("certificate data needs a reviewed upstream baseline")
    baseline = manifest["gecko"]
    required = {"remote", "tag", "build_tag", "commit"}
    if (set(pin) != required or set(baseline) != required | {"nss_version"}
            or not re.fullmatch(r"[0-9a-f]{40}", pin["commit"])
            or {key: baseline.get(key) for key in required} != pin):
        raise SecurityDataError("certificate data does not match the selected Gecko baseline")
    nss = (gecko / "security/nss/lib/nss/nss.h").read_text()
    versions = re.findall(r'^#define\s+NSS_VERSION\s+"([^"]+)"', nss, re.M)
    if versions != [baseline["nss_version"]]:
        raise SecurityDataError("NSS implementation version differs from the reviewed baseline")
    trust = manifest["trust"]
    if (trust.get("scope") != "complete-upstream-snapshot"
            or "removed_roots" in trust
            or trust["sha256"] != trust["upstream_sha256"]):
        raise SecurityDataError("trust data must use the complete reviewed upstream snapshot")
    if trust["version_path"] != "security/nss/lib/ckfw/builtins/nssckbi.h":
        raise SecurityDataError("trust version header path differs")
    header = (gecko / trust["version_path"]).read_bytes()
    if hashlib.sha256(header).hexdigest() != trust["version_sha256"]:
        raise SecurityDataError("trust version header hash differs")
    version = re.fullmatch(r"([0-9]+)\.([0-9]+)", trust["library_version"])
    if not version:
        raise SecurityDataError("invalid trust library version")
    definitions = {
        "NSS_BUILTINS_LIBRARY_VERSION": '"' + version[0] + '"',
        "NSS_BUILTINS_LIBRARY_VERSION_MAJOR": version[1],
        "NSS_BUILTINS_LIBRARY_VERSION_MINOR": version[2],
    }
    for name, value in definitions.items():
        if not re.search(rf'^#define\s+{name}\s+{re.escape(value)}\s*$', header.decode(), re.M):
            raise SecurityDataError("trust list and header library versions differ")


def verify_source(now: dt.datetime) -> dict:
    manifest = json.loads((ROOT / "runtime/config/certificate-security-data.json").read_text())
    policy = json.loads((ROOT / "navis/config/certificate-security-policy.json").read_text())
    if policy != {"schema_version": 1, "ct_mode": 2, "minimum_ct_validity_days": 42,
                  "maximum_maintenance_interval_days": 28, "remote_updates": False}:
        raise SecurityDataError("certificate release policy differs")
    remaining = check_expiry(manifest["ct"]["expires_at"], now, policy["minimum_ct_validity_days"])
    gecko = ROOT / "runtime/gecko"
    pin = json.loads((ROOT / "runtime/vendor/gecko.json").read_text())
    verify_baseline(manifest, pin, gecko)
    for name in ("ct", "onecrl", "trust"):
        entry = manifest[name]
        actual = hashlib.sha256((gecko / entry["path"]).read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            raise SecurityDataError(f"reviewed {name} source hash differs")
    ct = (gecko / manifest["ct"]["path"]).read_text()
    micros = re.search(r'kCTExpirationTime\s*=\s*INT64_C\((\d+)\)', ct)
    if not micros or dt.datetime.fromtimestamp(int(micros[1]) / 1e6, UTC).isoformat().replace("+00:00", "Z") != manifest["ct"]["expires_at"]:
        raise SecurityDataError("compiled CT expiration differs from manifest")
    snapshot = json.loads((gecko / manifest["onecrl"]["path"]).read_text())
    if snapshot["timestamp"] != manifest["onecrl"]["timestamp"] or len(snapshot["data"]) != manifest["onecrl"]["records"]:
        raise SecurityDataError("OneCRL snapshot identity differs")
    gen = generator()
    if (gecko / "security/certverifier/NavisBundledRevocations.h").read_text() != gen.render_revocations(snapshot, manifest["onecrl"]["sha256"], gen.security_data_identity(manifest)):
        raise SecurityDataError("native OneCRL baseline differs")
    if (ROOT / "runtime/embedder/modules/CertificateSecurityData.sys.mjs").read_text() != gen.render_metadata(manifest):
        raise SecurityDataError("security diagnostics metadata differs")
    for path in (ROOT / "platform/gecko-chrome/app/profile/navis.js",
                 gecko / "mobile/android/app/navis-prefs.js"):
        locked_policy(path.read_text())
    desktop_error_defaults((ROOT / "platform/gecko-chrome/app/profile/navis.js").read_text())
    verify_android_security_modules(
        (gecko / "mobile/shared/modules/navis/moz.build").read_text())
    rs = json.loads((ROOT / "platform/gecko-chrome/config/navis-remote-settings-policy.json").read_text())
    if any(e["remote_sync"] or not e["packaged_bootstrap"] for e in rs["allowed_collections"]):
        raise SecurityDataError("Remote Settings has a nonlocal dependency")
    return {"manifest": manifest, "ct_remaining_days": remaining, "ct_mode": 2, "remote_updates": False}


def verify_omni(archive: zipfile.ZipFile, manifest: dict, android: bool = False) -> None:
    expected = generator().render_metadata(manifest).encode()
    if archive.read("modules/CertificateSecurityData.sys.mjs") != expected:
        raise SecurityDataError("packaged security metadata differs")
    if hashlib.sha256(archive.read("defaults/settings/security-state/onecrl.json")).hexdigest() != manifest["onecrl"]["sha256"]:
        raise SecurityDataError("packaged OneCRL differs")
    prefs = "defaults/pref/arm64-v8a/navis-prefs.js" if android else "defaults/pref/navis.js"
    text = archive.read(prefs).decode()
    locked_policy(text)
    if not android:
        desktop_error_defaults(text)
    for name in ("modules/services-settings/Utils.sys.mjs", "modules/services-settings/RemoteSettingsClient.sys.mjs"):
        if "isLocalOnly" not in archive.read(name).decode():
            raise SecurityDataError(f"packaged offline boundary is absent: {name}")


def verify_native(path: Path, manifest: dict) -> None:
    # Bind CT, OneCRL and trust provenance together, including releases where
    # only the CT list changes. OneCRL alone cannot identify the native data.
    with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as content:
        if content.find(generator().security_data_identity(manifest).encode()) < 0:
            raise SecurityDataError("native verifier lacks this certificate-security data identity")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--runtime", type=Path)
    target.add_argument("--apk", type=Path)
    args = parser.parse_args()
    try:
        result = verify_source(dt.datetime.now(UTC))
        manifest = result["manifest"]
        if args.runtime:
            with zipfile.ZipFile(args.runtime / "omni.ja") as omni:
                verify_omni(omni, manifest)
            verify_native(args.runtime / ("xul.dll" if (args.runtime / "navis.exe").exists() else "libxul.so"), manifest)
        if args.apk:
            with zipfile.ZipFile(args.apk) as apk:
                with zipfile.ZipFile(io.BytesIO(apk.read("assets/omni.ja"))) as omni:
                    verify_omni(omni, manifest, android=True)
                # Avoid extracting an APK-sized temporary file on a build host.
                if generator().security_data_identity(manifest).encode() not in apk.read("lib/arm64-v8a/libxul.so"):
                    raise SecurityDataError("Android native verifier lacks this certificate-security data identity")
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"Certificate security gate failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
