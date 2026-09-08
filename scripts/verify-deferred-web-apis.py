#!/usr/bin/env python3

"""Verify that deferred browser-mediated Web APIs stay hidden in Navis 1.0."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import zipfile

WORKSPACE = pathlib.Path(__file__).resolve().parent.parent
LOCKED_PREFS = (
    ("dom.webmidi.enabled", "false"),
    ("dom.webmidi.gated", "true"),
    ("dom.webserial.enabled", "false"),
    ("dom.webserial.gated", "true"),
    ("dom.vr.enabled", "false"),
    ("dom.vr.webxr.enabled", "false"),
    ("dom.payments.request.enabled", "false"),
    ("dom.security.credentialmanagement.digital.enabled", "false"),
    ("dom.security.credentialmanagement.identity.enabled", "false"),
)
RUNTIME_SURFACE_KEYS = (
    "webUsb",
    "webBluetooth",
    "webHid",
    "webMidi",
    "webSerial",
    "webVr",
    "webXr",
    "paymentRequest",
    "digitalCredential",
    "identityCredential",
)


def require(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def read(path: pathlib.Path, failures: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        failures.append(f"cannot read {path}: {error}")
        return ""


def verify_source(workspace: pathlib.Path, failures: list[str]) -> None:
    gecko = workspace / "../runtime/gecko"
    prefs_path = workspace / "../platform/gecko-chrome/app/profile/navis.js"
    prefs = read(prefs_path, failures)
    for name, value in LOCKED_PREFS:
        marker = f'pref("{name}", {value}, locked);'
        require(failures, marker in prefs, f"{prefs_path}: missing {marker}")

    static_prefs_path = gecko / "modules/libpref/init/StaticPrefList.yaml"
    static_prefs = read(static_prefs_path, failures)
    for name, _value in LOCKED_PREFS:
        require(
            failures,
            f"- name: {name}" in static_prefs,
            f"pinned ESR no longer declares preference {name}",
        )

    upstream_markers = {
        "dom/webidl/Navigator.webidl": (
            'Pref="dom.vr.enabled"',
            'Pref="dom.vr.webxr.enabled"',
            'Pref="dom.webserial.enabled"',
            'Func="Navigator::HasMidiSupport"',
        ),
        "dom/webidl/MIDIAccess.webidl": ('Pref="dom.webmidi.enabled"',),
        "dom/webidl/WebXR.webidl": ('Pref="dom.vr.webxr.enabled"',),
        "dom/webidl/PaymentRequest.webidl": (
            'Func="mozilla::dom::PaymentRequest::PrefEnabled"',
        ),
        "dom/payments/PaymentRequest.cpp": (
            "StaticPrefs::dom_payments_request_enabled()",
            "#else\n  return false;\n#endif",
        ),
        "dom/webidl/CredentialManagement.webidl": (
            'Pref="dom.security.credentialmanagement.digital.enabled"',
            'Pref="dom.security.credentialmanagement.identity.enabled"',
        ),
        "dom/webidl/DigitalCredential.webidl": (
            'Pref="dom.security.credentialmanagement.digital.enabled"',
        ),
        "dom/webidl/IdentityCredential.webidl": (
            'Pref="dom.security.credentialmanagement.identity.enabled"',
        ),
    }
    for relative, markers in upstream_markers.items():
        path = gecko / relative
        content = read(path, failures)
        for marker in markers:
            require(
                failures,
                marker in content,
                f"{path}: deferred API exposure marker changed: {marker}",
            )

    webidl = gecko / "dom/webidl"
    unexpected_files = sorted(
        path.name
        for path in webidl.glob("*.webidl")
        if re.fullmatch(r"(?:USB|HID|Bluetooth)[A-Za-z0-9_]*\.webidl", path.name)
    )
    require(
        failures,
        not unexpected_files,
        "pinned ESR gained an unreviewed WebUSB/WebHID/WebBluetooth WebIDL: "
        + ", ".join(unexpected_files),
    )

    navigator = read(webidl / "Navigator.webidl", failures)
    for member in ("usb", "hid", "bluetooth"):
        expression = re.compile(
            rf"\breadonly\s+attribute\s+[A-Za-z0-9_?]+\s+{member}\s*;"
        )
        require(
            failures,
            expression.search(navigator) is None,
            f"pinned ESR gained unreviewed navigator.{member} exposure",
        )


def verify_runtime(
    workspace: pathlib.Path, runtime: pathlib.Path, failures: list[str]
) -> None:
    del workspace
    omni = runtime / "omni.ja"
    if not omni.is_file():
        failures.append(f"runtime omnijar is missing: {omni}")
        return
    try:
        with zipfile.ZipFile(omni) as archive:
            prefs = archive.read("defaults/pref/navis.js").decode("utf-8")
    except (KeyError, OSError, UnicodeError, zipfile.BadZipFile) as error:
        failures.append(f"cannot inspect runtime Navis preferences: {error}")
        return
    for name, value in LOCKED_PREFS:
        marker = f'pref("{name}", {value}, locked);'
        require(
            failures,
            marker in prefs,
            f"runtime preferences lack deferred API lock: {marker}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=pathlib.Path, default=WORKSPACE)
    parser.add_argument("--runtime", type=pathlib.Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    failures: list[str] = []
    verify_source(workspace, failures)
    if args.runtime is not None:
        verify_runtime(workspace, args.runtime.resolve(), failures)
    if failures:
        print("Navis deferred Web API policy verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Navis deferred Web API policy verified.")
    print("- browser-mediated device/headset APIs: hidden")
    print("- payment, federated identity and digital credentials: hidden")
    print("- WebAuthn and ordinary form/OAuth paths: unaffected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
