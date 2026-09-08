#!/usr/bin/env python3

"""Verify the cross-platform Navis product identity closure."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


BASE_VERSION = "0.2.0"
DISPLAY_VERSION = "0.2.0-dev"
ANDROID_VERSION_CODE_EPOCH = 1_577_836_800
ANDROID_VERSION_CODE_BASE = 100_000_000
ANDROID_VERSION_CODE_MAX = 2_100_000_000
PORT_ID = "navis-android-product-identity-projection"
PORT_PATH = "patches/gecko/0044-project-navis-android-product-identity.patch"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    return parser.parse_args()


def read(root: Path, relative: str, failures: list[str]) -> str:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        failures.append(f"cannot read {relative}: {error}")
        return ""


def require(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def require_markers(
    failures: list[str], description: str, content: str, markers: tuple[str, ...]
) -> None:
    missing = [marker for marker in markers if marker not in content]
    require(
        failures,
        not missing,
        f"{description} is missing: {', '.join(missing)}",
    )


def derive_android_version_code(epoch_seconds: int) -> int:
    value = ANDROID_VERSION_CODE_BASE + epoch_seconds - ANDROID_VERSION_CODE_EPOCH
    if not 1 <= value <= ANDROID_VERSION_CODE_MAX:
        raise ValueError("derived Android versionCode is outside the supported range")
    return value


def verify(root: Path) -> list[str]:
    failures: list[str] = []
    base_version = read(root, "product/config/version.txt", failures).strip()
    display_version = read(root, "product/config/version_display.txt", failures).strip()
    require(
        failures,
        base_version == BASE_VERSION,
        f"base product version must be {BASE_VERSION}, found {base_version!r}",
    )
    require(
        failures,
        display_version == DISPLAY_VERSION,
        f"display product version must be {DISPLAY_VERSION}, found {display_version!r}",
    )

    android_mozconfig = read(root, "mozconfig.android-aarch64.sccache", failures)
    require(
        failures,
        android_mozconfig.count(
            "ac_add_options --with-navis-product-version-file-path=navis/config"
        )
        == 1,
        "Android Gecko does not select the mounted product/config version source once",
    )
    require(
        failures,
        "ac_add_options --with-version-file-path=navis/config"
        not in android_mozconfig,
        "Android product identity incorrectly replaces Gecko's engine version",
    )

    gradle = read(root, "android/build.gradle", failures)
    require_markers(
        failures,
        "Android product version derivation",
        gradle,
        (
            "def navisWorkspaceRoot = project.projectDir.canonicalFile.parentFile.parentFile",
            "platform/gecko-chrome/config/${fileName}",
            "def navisBaseVersion = readProductVersion('version.txt')",
            "def navisDisplayVersion = readProductVersion('version_display.txt')",
            "versionName navisDisplayVersion",
            "versionCode navisVersionCode as int",
            "'NAVIS_PRODUCT_VERSION'",
            "'NAVIS_PRODUCT_VERSION_DISPLAY'",
        ),
    )
    require_markers(
        failures,
        "Android monotonic versionCode policy",
        gradle,
        (
            "NAVIS_ANDROID_VERSION_CODE",
            "SOURCE_DATE_EPOCH",
            "100_000_000L + buildEpochSeconds - 1_577_836_800L",
            "2_100_000_000L",
        ),
    )
    require(
        failures,
        "versionNameSuffix" not in gradle,
        "Android debug build adds an ambiguous versionName suffix",
    )
    require(
        failures,
        not re.search(r"\bversionName\s+['\"]", gradle),
        "Android versionName is hard-coded instead of using the shared product config",
    )
    require(
        failures,
        "product/config/${fileName}" not in gradle,
        "Android product identity still reads version files through the transition view",
    )

    http_build = read(root, "gecko/netwerk/protocol/http/moz.build", failures)
    http_handler = read(root, "gecko/netwerk/protocol/http/nsHttpHandler.cpp", failures)
    geckoview_gradle = read(
        root, "gecko/mobile/android/geckoview/build.gradle", failures
    )
    app_constants = read(root, "gecko/toolkit/modules/AppConstants.sys.mjs", failures)
    app_constants_build = read(root, "gecko/toolkit/modules/moz.build", failures)
    toolkit_configure = read(root, "gecko/toolkit/moz.configure", failures)
    extension_runtime = read(
        root, "gecko/toolkit/components/extensions/parent/ext-runtime.js", failures
    )
    require_markers(
        failures,
        "Android page user-agent projection",
        http_build + http_handler,
        (
            'if CONFIG["MOZ_NAVIS_CORE"] and CONFIG["MOZ_WIDGET_TOOLKIT"] == "android":',
            '"Navis/%s"',
            'CONFIG["MOZ_NAVIS_PRODUCT_VERSION"]',
            "#ifdef MOZ_NAVIS_PRODUCT_UA_TOKEN",
            "mUserAgent += MOZ_NAVIS_PRODUCT_UA_TOKEN;",
        ),
    )
    require_markers(
        failures,
        "GeckoView generated user-agent projection",
        geckoview_gradle,
        (
            "def navisProductUserAgentSuffix = mozconfig.substs.MOZ_NAVIS_CORE",
            'Navis/${mozconfig.substs.MOZ_NAVIS_PRODUCT_VERSION}',
            '"USER_AGENT_GECKOVIEW_MOBILE"',
            '"USER_AGENT_GECKOVIEW_MOBILE_ANDROID_10"',
            " Firefox/",
            "${navisProductUserAgentSuffix}",
        ),
    )
    require_markers(
        failures,
        "Android Gecko product-version namespace",
        toolkit_configure + app_constants + app_constants_build,
        (
            '"--with-navis-product-version-file-path"',
            'if has_navis_core and target.os == "Android"',
            'if not has_navis_core or target.os != "Android"',
            '("version.txt", "version_display.txt")',
            'set_config("MOZ_NAVIS_PRODUCT_VERSION", navis_product_versions.version)',
            '"MOZ_NAVIS_PRODUCT_VERSION_DISPLAY", navis_product_versions.display_version',
            'MOZ_NAVIS_PRODUCT_VERSION: "@MOZ_NAVIS_PRODUCT_VERSION@"',
            'MOZ_NAVIS_PRODUCT_VERSION_DISPLAY: "@MOZ_NAVIS_PRODUCT_VERSION_DISPLAY@"',
            '"MOZ_NAVIS_PRODUCT_VERSION"',
            '"MOZ_NAVIS_PRODUCT_VERSION_DISPLAY"',
        ),
    )
    require_markers(
        failures,
        "Android WebExtension browser identity projection",
        app_constants + extension_runtime,
        (
            "MOZ_NAVIS_CORE:",
            "#ifdef MOZ_NAVIS_CORE",
            "AppConstants.MOZ_NAVIS_CORE",
            'AppConstants.platform === "android"',
            'name: "Navis"',
            'vendor: "Navis"',
            "version: AppConstants.MOZ_NAVIS_PRODUCT_VERSION_DISPLAY",
        ),
    )
    require(
        failures,
        extension_runtime.count(
            'AppConstants.MOZ_NAVIS_CORE &&\n            AppConstants.platform === "android"'
        )
        == 1,
        "WebExtension product identity is not guarded exactly once by Navis Core and Android",
    )
    require(
        failures,
        "const { name, vendor, version, appBuildID } = Services.appinfo;"
        in extension_runtime,
        "upstream WebExtension browser identity fallback was removed",
    )

    patch = read(root, PORT_PATH, failures)
    require_markers(
        failures,
        "product identity semantic port",
        patch,
        (
            "MOZ_NAVIS_PRODUCT_UA_TOKEN",
            "navisProductUserAgentSuffix",
            "AppConstants.MOZ_NAVIS_CORE",
            "MOZ_NAVIS_PRODUCT_VERSION_DISPLAY",
        ),
    )
    allowed_paths = {
        "mobile/android/geckoview/build.gradle",
        "netwerk/protocol/http/moz.build",
        "netwerk/protocol/http/nsHttpHandler.cpp",
        "toolkit/components/extensions/parent/ext-runtime.js",
        "toolkit/modules/AppConstants.sys.mjs",
        "toolkit/modules/moz.build",
        "toolkit/moz.configure",
    }
    touched_paths = {
        match.group(1)
        for match in re.finditer(r"^diff --git a/(\S+) b/(\S+)$", patch, re.MULTILINE)
        if match.group(1) == match.group(2)
    }
    require(
        failures,
        touched_paths == allowed_paths,
        "product identity semantic port touches an unexpected Gecko path",
    )

    ledger_text = read(root, "config/gecko-semantic-ports.json", failures)
    try:
        ledger = json.loads(ledger_text)
    except json.JSONDecodeError as error:
        failures.append(f"cannot parse semantic-port ledger: {error}")
        ledger = {}
    ports = ledger.get("ports", []) if isinstance(ledger, dict) else []
    matches = [entry for entry in ports if entry.get("id") == PORT_ID]
    require(failures, len(matches) == 1, "product identity semantic port is not unique")
    if len(matches) == 1:
        entry = matches[0]
        require(
            failures,
            entry.get("order") == 44
            and len(ports) >= 44
            and ports[43].get("id") == PORT_ID,
            "product identity semantic port is not the contiguous order-44 entry",
        )
        require(
            failures,
            entry.get("patch") == PORT_PATH,
            "product identity semantic port selects the wrong patch",
        )
        actual_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        require(
            failures,
            entry.get("sha256") == actual_hash,
            "product identity semantic-port hash does not match its patch",
        )

    try:
        first_code = derive_android_version_code(ANDROID_VERSION_CODE_EPOCH)
        next_code = derive_android_version_code(ANDROID_VERSION_CODE_EPOCH + 1)
        require(
            failures,
            first_code == ANDROID_VERSION_CODE_BASE and next_code == first_code + 1,
            "default Android versionCode derivation is not monotonic",
        )
    except ValueError as error:
        failures.append(str(error))

    return failures


def main() -> int:
    args = parse_args()
    failures = verify(args.source_root.resolve())
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(
        "Navis product identity passes: 0.2.0-dev, Android version policy, "
        "Firefox-compatible Navis UA and WebExtension projection"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
