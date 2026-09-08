#!/usr/bin/env python3

"""Reject Navis spellcheck policy, projection and package drift."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent
DICTIONARY_ROOT = (
    WORKSPACE / "../runtime/gecko/extensions/spellcheck/locales/en-US/hunspell"
)
SOURCE_DICTIONARIES = {
    "en-US.aff": DICTIONARY_ROOT / "en-US.aff",
    "en-US.dic": DICTIONARY_ROOT / "en-US.dic",
}


def require(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def read(relative: str, failures: list[str]) -> str:
    try:
        return (WORKSPACE / relative).read_text(encoding="utf-8")
    except OSError as error:
        failures.append(f"cannot read {relative}: {error}")
        return ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_source() -> list[str]:
    failures: list[str] = []
    child = read("../runtime/embedder/components/DesktopContextMenuChild.sys.mjs", failures)
    engine = read("../runtime/embedder/modules/DesktopEngine.sys.mjs", failures)
    platform = read("../platform/gecko-chrome/chrome/content/main.mjs", failures)
    prefs = read("../platform/gecko-chrome/app/profile/navis.js", failures)
    manifest = read("../platform/gecko-chrome/installer/package-manifest.in", failures)
    locale_build = read("../runtime/gecko/extensions/spellcheck/locales/moz.build", failures)
    extension_build = read("../runtime/gecko/toolkit/mozapps/extensions/moz.build", failures)
    xpi_provider = read(
        "../runtime/gecko/toolkit/mozapps/extensions/internal/XPIProvider.sys.mjs", failures
    )

    for name, path in SOURCE_DICTIONARIES.items():
        require(failures, path.is_file(), f"source dictionary is missing: {name}")
        if not path.is_file():
            continue
        require(
            failures,
            path.stat().st_size >= (3000 if name.endswith(".aff") else 500000),
            f"source dictionary is unexpectedly small: {name}",
        )
    dictionary = SOURCE_DICTIONARIES["en-US.dic"]
    if dictionary.is_file():
        lines = dictionary.read_bytes().splitlines()
        try:
            declared_words = int(lines[0])
        except (IndexError, ValueError):
            failures.append("en-US.dic does not start with its word count")
        else:
            require(
                failures,
                declared_words == len(lines) - 1,
                "en-US.dic declared word count does not match its payload",
            )

    for marker in (
        '"en-US/hunspell/*.aff"',
        '"en-US/hunspell/*.dic"',
    ):
        require(
            failures,
            marker in locale_build,
            f"localized dictionary install lacks {marker}",
        )
    require(
        failures,
        "@RESPATH@/dictionaries/*" in manifest,
        "product package manifest omits dictionaries",
    )
    require(
        failures,
        "@RESPATH@/modules/*" in manifest
        and "@RESPATH@/built_in_addons.json" not in manifest,
        "product package manifest does not route the dictionary registry through OmniJar modules",
    )
    require(
        failures,
        'elif CONFIG["MOZ_DESKTOP_EMBEDDER"]:' in extension_build
        and 'FINAL_TARGET_FILES.modules += [' in extension_build
        and '"!%s" % built_in_addons' in extension_build,
        "desktop embedder does not install the generated built-in dictionary registry",
    )
    require(
        failures,
        "AppConstants.MOZ_DESKTOP_EMBEDDER" in xpi_provider
        and '"resource://gre/modules/built_in_addons.json"' in xpi_provider,
        "desktop embedder does not load its built-in registry from the resource-visible modules path",
    )
    require(
        failures,
        'pref("layout.spellcheckDefault", 1, locked);' in prefs,
        "product policy does not retain multiline inline spellchecking",
    )

    for marker in (
        "InlineSpellChecker",
        "SpellCheckHelper",
        "MAX_SPELL_SUGGESTIONS = 5",
        "MAX_SPELL_SUGGESTION_LENGTH = 128",
        "SpellCheckHelper.SPELLCHECKABLE",
        "checker.mInlineSpellChecker.spellChecker.suggest(",
        'group: "spelling"',
        "this.#spellChecker?.replaceMisspelling(suggestion)",
        "/[\\u0000-\\u001f\\u007f]/u.test(candidate)",
    ):
        require(failures, marker in child, f"content spellcheck binding lacks {marker}")

    for index in range(5):
        require(
            failures,
            f'"spell-replace-{index}": "spelling"' in engine,
            f"Core context-menu policy omits spelling slot {index}",
        )
    for marker in (
        'boundedContextMenuText(candidate.suggestion, 128)',
        'group === "spelling"',
        "suggestion ? { suggestion } : {}",
        "misspelled: Boolean(data.context?.misspelled)",
    ):
        require(failures, marker in engine, f"Core spelling projection lacks {marker}")
    require(
        failures,
        'item.group === "spelling" && item.suggestion' in platform
        and "label: item.suggestion" in platform,
        "Platform does not present the bounded dictionary suggestion",
    )


    return failures


def verify_runtime(runtime: Path) -> list[str]:
    failures: list[str] = []
    dictionary_root = runtime / "dictionaries"
    dictionaries: dict[str, list[tuple[str, bytes]]] = {}
    registries: list[tuple[str, bytes]] = []

    registry = runtime / "modules/built_in_addons.json"
    if registry.is_file():
        registries.append(("modules/built_in_addons.json", registry.read_bytes()))

    if dictionary_root.is_dir():
        for path in dictionary_root.rglob("*"):
            if not path.is_file():
                continue
            name = path.relative_to(dictionary_root).as_posix()
            dictionaries.setdefault(name, []).append(
                (f"dictionaries/{name}", path.read_bytes())
            )

    omni = runtime / "omni.ja"
    if omni.is_file():
        try:
            with zipfile.ZipFile(omni) as archive:
                for member in archive.infolist():
                    if member.filename == "modules/built_in_addons.json":
                        registries.append(
                            (
                                "omni.ja!/modules/built_in_addons.json",
                                archive.read(member),
                            )
                        )
                    if member.is_dir() or not member.filename.startswith("dictionaries/"):
                        continue
                    name = member.filename.removeprefix("dictionaries/")
                    dictionaries.setdefault(name, []).append(
                        (f"omni.ja!/{member.filename}", archive.read(member))
                    )
        except (OSError, zipfile.BadZipFile) as error:
            failures.append(f"cannot inspect runtime OmniJar dictionaries: {error}")

    require(
        failures,
        len(registries) == 1,
        "runtime must contain exactly one built-in dictionary registry; found "
        f"{[location for location, _ in registries]}",
    )
    if len(registries) == 1:
        location, payload = registries[0]
        try:
            registry_data = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            failures.append(f"runtime dictionary registry is invalid at {location}: {error}")
        else:
            require(
                failures,
                registry_data.get("dictionaries")
                == {"en-US": "dictionaries/en-US.dic"},
                "runtime dictionary registry does not expose the pinned en-US payload: "
                f"{registry_data!r}",
            )

    if not dictionaries:
        return failures + [
            "runtime dictionary payload is missing from dictionaries/ and omni.ja"
        ]

    actual = set(dictionaries)
    expected = set(SOURCE_DICTIONARIES)
    require(
        failures,
        actual == expected,
        "runtime dictionary inventory differs: "
        f"expected {sorted(expected)}, found {sorted(actual)}",
    )
    for name, source in SOURCE_DICTIONARIES.items():
        locations = dictionaries.get(name, [])
        require(
            failures,
            len(locations) <= 1,
            f"runtime dictionary is duplicated: {name} in "
            f"{[location for location, _ in locations]}",
        )
        require(failures, source.is_file(), f"source dictionary is missing: {name}")
        if len(locations) != 1 or not source.is_file():
            continue
        location, payload = locations[0]
        require(
            failures,
            sha256_bytes(payload) == sha256(source),
            "runtime dictionary bytes differ from pinned ESR source: "
            f"{name} at {location}",
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()

    failures = verify_source()
    if args.runtime is not None:
        failures.extend(verify_runtime(args.runtime.resolve()))
    if failures:
        print("Navis spellcheck verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    dictionary_hash = sha256(SOURCE_DICTIONARIES["en-US.dic"])
    print(
        "Navis spellcheck policy verified: en-US Hunspell, "
        f"5 bounded suggestions, dictionary sha256={dictionary_hash}"
    )
    if args.runtime is not None:
        print(
            "- exact runtime dictionary inventory and registration: "
            f"{args.runtime / 'omni.ja'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
