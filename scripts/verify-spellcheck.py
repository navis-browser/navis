#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Verify exclusion of built-in browser spellchecking without requiring generated Gecko."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent
RESOURCE_PATTERN = re.compile(
    r"(?:^|/)dictionaries(?:/|$)|\.(?:aff|dic)$|"
    r"(?:^|/)InlineSpellChecker(?:Content|Parent|Child)?\.sys\.mjs$|"
    r"(?:^|/)built_in_addons\.json$"
)
COMMAND_PATTERN = re.compile(r"spell-replace-[0-4]|InlineSpellChecker\.sys\.mjs")
BINDING_RESOURCES = (
    "modules/DesktopContextMenuChild.sys.mjs",
    "modules/DesktopEngine.sys.mjs",
    "chrome/navis/content/main.mjs",
    "chrome/navis/content/content/main.mjs",
    "actors/ContentDelegateChild.sys.mjs",
)


def is_spellchecking_resource(name: str) -> bool:
    # These libhyphen patterns belong to text layout, not spelling detection.
    if re.fullmatch(r"hyphenation/hyph_[A-Za-z0-9_-]+\.dic", name):
        return False
    return RESOURCE_PATTERN.search(name) is not None


def verify_source() -> list[str]:
    failures = []
    root = WORKSPACE.parent
    profiles = sorted((root / "runtime").glob("mozconfig.*"))
    if not profiles:
        failures.append("product build profiles are missing")
    for path in profiles:
        if "ac_add_options --disable-spellchecking" not in path.read_text():
            failures.append(f"product profile enables built-in spellchecking: {path.name}")
    prefs = (root / "platform/gecko-chrome/app/profile/navis.js").read_text()
    if 'pref("layout.spellcheckDefault", 0, locked);' not in prefs:
        failures.append("existing Profiles can request inline spellchecking")
    for relative in (
        "runtime/embedder/components/DesktopContextMenuChild.sys.mjs",
        "runtime/embedder/modules/DesktopEngine.sys.mjs",
        "platform/gecko-chrome/chrome/content/main.mjs",
    ):
        if COMMAND_PATTERN.search((root / relative).read_text()):
            failures.append(f"obsolete spelling command in {relative}")
    ledger = json.loads((root / "runtime/config/gecko-semantic-ports.json").read_text())
    ports = [port for port in ledger["ports"] if port["id"] == "spellchecking-capability-gate"]
    if len(ports) != 1:
        failures.append("native spellchecking exclusion gate is missing or duplicated")
    else:
        patch = (root / "runtime" / ports[0]["patch"]).read_text()
        for marker in (
            '"--disable-spellchecking"',
            'set_config("MOZ_SPELLCHECK", spellchecking)',
            'set_define("MOZ_SPELLCHECK", spellchecking)',
            "PRemoteSpellcheckEngine",
            "NS_ERROR_NOT_AVAILABLE",
            "delete JSWINDOWACTORS.InlineSpellChecker",
        ):
            if marker not in patch:
                failures.append(f"native spellchecking gate lacks {marker}")
    capabilities = json.loads((WORKSPACE / "config/runtime-capabilities.json").read_text())
    if not any(row["id"] == "builtin-spellchecking" and row["state"] == "excluded"
               for row in capabilities["capabilities"]):
        failures.append("runtime capability ledger does not exclude built-in spellchecking")
    return failures


def verify_omni(archive: zipfile.ZipFile, failures: list[str], label: str) -> None:
    for name in archive.namelist():
        if is_spellchecking_resource(name):
            failures.append(f"forbidden spellchecking resource: {label}!/{name}")
        if name in BINDING_RESOURCES and COMMAND_PATTERN.search(
            archive.read(name).decode("utf-8", errors="replace")
        ):
            failures.append(f"obsolete spelling command: {label}!/{name}")


def verify_runtime(runtime: Path) -> list[str]:
    failures = []
    for path in runtime.rglob("*"):
        if not path.is_file():
            continue
        name = path.relative_to(runtime).as_posix()
        if is_spellchecking_resource(name):
            failures.append(f"forbidden spellchecking resource: {name}")
    omni = runtime / "omni.ja"
    if not omni.is_file():
        return failures
    try:
        with zipfile.ZipFile(omni) as archive:
            verify_omni(archive, failures, "omni.ja")
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        failures.append(f"cannot inspect packaged spellchecking exclusion: {error}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()
    failures = verify_source()
    if args.runtime:
        failures.extend(verify_runtime(args.runtime.resolve()))
    if failures:
        print("Navis spellchecking exclusion verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Navis built-in spellchecking excluded: native gate, profiles and menu projections")
    if args.runtime:
        print(f"- no dictionary, helper, actor, registry or suggestion payload in {args.runtime}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
