#!/usr/bin/env python3

"""Verify Navis's native geolocation and notification product boundary."""

from __future__ import annotations

import argparse
import pathlib
import struct
import sys
import zipfile


WORKSPACE = pathlib.Path(__file__).resolve().parent.parent
GECKO = WORKSPACE / "../runtime/gecko"
PREFS = WORKSPACE / "../platform/gecko-chrome/app/profile/navis.js"
PATCH = WORKSPACE / "../runtime/patches/gecko/0017-enforce-native-only-geolocation-policy.patch"
NOTIFICATION_PATCH = (
    WORKSPACE
    / "../runtime/patches/gecko/0033-enable-native-notification-activation-for-desktop-embedder.patch"
)


def require(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def require_markers(
    failures: list[str], path: pathlib.Path, markers: tuple[str, ...]
) -> None:
    content = path.read_text(encoding="utf-8")
    for marker in markers:
        require(failures, marker in content, f"{path}: missing {marker}")


def verify_source(failures: list[str]) -> None:
    require_markers(
        failures,
        GECKO / "modules/libpref/init/StaticPrefList.yaml",
        ("- name: geo.provider.native_only", "value: false"),
    )
    require_markers(
        failures,
        PREFS,
        (
            'pref("geo.provider.native_only", true, locked);',
            'pref("geo.provider.use_mls", false, locked);',
            'pref("geo.provider.use_geoclue", true, locked);',
            'pref("geo.provider.ms-windows-location", true, locked);',
            'pref("geo.provider.use_winrt", true, locked);',
            'pref("alerts.useSystemBackend", true, locked);',
        ),
    )
    require_markers(
        failures,
        GECKO / "dom/geolocation/Geolocation.cpp",
        (
            '!StaticPrefs::geo_provider_native_only() &&\n'
            '      Preferences::GetBool("geo.provider.use_mls", false)',
            '!StaticPrefs::geo_provider_native_only() &&\n'
            '      (!mProvider || Preferences::GetBool("geo.provider.testing", false))',
        ),
    )
    require_markers(
        failures,
        GECKO / "dom/geolocation/MLSFallback.cpp",
        (
            "StaticPrefs::geo_provider_native_only() && mDelayMs == 0",
            "if (mozilla::StaticPrefs::geo_provider_native_only())",
            "POSITION_UNAVAILABLE",
        ),
    )
    require_markers(
        failures,
        GECKO / "dom/system/linux/GeoclueLocationProvider.cpp",
        (
            "nsresult GCLocProviderPriv::FallbackToMLS",
            "if (StaticPrefs::geo_provider_native_only())",
            "NotifyError(GeolocationPositionError_Binding::POSITION_UNAVAILABLE)",
        ),
    )
    require_markers(
        failures,
        GECKO / "dom/system/linux/PortalLocationProvider.cpp",
        (
            "!mMLSProvider && !StaticPrefs::geo_provider_native_only()",
            "StaticPrefs::geo_provider_native_only() ? NS_ERROR_FAILURE",
        ),
    )
    require_markers(
        failures,
        PATCH,
        (
            "geo.provider.native_only",
            "geo_provider_native_only",
            "POSITION_UNAVAILABLE",
            "PortalLocationProvider.cpp",
            "GeoclueLocationProvider.cpp",
        ),
    )
    require_markers(
        failures,
        GECKO / "build/moz.configure/update-programs.configure",
        (
            '@depends(target, build_project, "--enable-desktop-embedder")',
            "def notification_server_default(target, build_project, desktop_embedder):",
            "desktop_embedder\n        or build_project",
            'set_config("MOZ_NOTIFICATION_SERVER", True, when="--enable-notification-server")',
        ),
    )
    require_markers(
        failures,
        NOTIFICATION_PATCH,
        (
            "--enable-desktop-embedder",
            "notification_server_default",
            "desktop_embedder",
            "comm/mail",
        ),
    )
    require_markers(
        failures,
        WORKSPACE / "../platform/gecko-chrome/branding/moz.build",
        (
            'CONFIG["MOZ_WIDGET_TOOLKIT"] == "windows"',
            'GeneratedFile(\n        "VisualElements_70.png"',
            'FINAL_TARGET_FILES.VisualElements += ["!VisualElements_70.png"]',
        ),
    )
    require_markers(
        failures,
        WORKSPACE / "../platform/gecko-chrome/branding/generate_visual_elements.py",
        (
            "SIZE = 70",
            "ACCENT = (11, 87, 208)",
            'b"\\x89PNG\\r\\n\\x1a\\n"',
            "def main(output) -> None:",
        ),
    )
    require_markers(
        failures,
        WORKSPACE / "../platform/gecko-chrome/installer/Makefile.in",
        (
            "ifdef MOZ_NOTIFICATION_SERVER",
            "DEFINES += -DMOZ_NOTIFICATION_SERVER=1",
        ),
    )
    require_markers(
        failures,
        WORKSPACE / "../platform/gecko-chrome/installer/package-manifest.in",
        (
            "#ifdef MOZ_NOTIFICATION_SERVER",
            "@BINPATH@/@DLL_PREFIX@notificationserver@DLL_SUFFIX@",
            "@BINPATH@/VisualElements/VisualElements_70.png",
        ),
    )
    require_markers(
        failures,
        WORKSPACE / "config/runtime-source-domains.json",
        (
            '"id": "native-notification-activation"',
            '"state": "required-on-windows"',
            '"^toolkit/mozapps/notificationserver(?:/|$)"',
        ),
    )

def verify_runtime(runtime: pathlib.Path, failures: list[str]) -> None:
    omni = runtime / "omni.ja"
    if not omni.is_file():
        failures.append(f"runtime omnijar is missing: {omni}")
        return
    with zipfile.ZipFile(omni) as archive:
        name = "defaults/pref/navis.js"
        if name not in archive.namelist():
            failures.append(f"runtime preference file is missing: {name}")
            return
        prefs = archive.read(name).decode("utf-8")
    for marker in (
        'pref("geo.provider.native_only", true, locked);',
        'pref("geo.provider.use_mls", false, locked);',
        'pref("alerts.useSystemBackend", true, locked);',
    ):
        require(failures, marker in prefs, f"runtime preferences lack {marker}")

    executable = runtime / "navis.exe"
    if not executable.is_file():
        return
    notification_server = runtime / "notificationserver.dll"
    icon = runtime / "VisualElements/VisualElements_70.png"
    require(
        failures,
        notification_server.is_file(),
        f"Windows notification activation server is missing: {notification_server}",
    )
    require(
        failures,
        icon.is_file(),
        f"Windows notification identity icon is missing: {icon}",
    )
    if notification_server.is_file():
        require(
            failures,
            notification_server.read_bytes()[:2] == b"MZ",
            f"Windows notification activation server is not PE: {notification_server}",
        )
    if icon.is_file():
        data = icon.read_bytes()
        require(
            failures,
            data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 33,
            f"Windows notification identity icon is not PNG: {icon}",
        )
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 33:
            width, height, depth, color_type = struct.unpack(">IIBB", data[16:26])
            require(
                failures,
                (width, height, depth, color_type) == (70, 70, 8, 6),
                f"Windows notification icon has the wrong IHDR: {icon}",
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=pathlib.Path)
    args = parser.parse_args()
    failures: list[str] = []
    verify_source(failures)
    if args.runtime is not None:
        verify_runtime(args.runtime.resolve(), failures)
    if failures:
        print("Navis native services boundary verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Navis native services boundary verified.")
    print("- native-only geolocation policy: locked")
    print("- Linux GeoClue/Portal network fallback: unreachable")
    print("- native notification backend: locked")
    print("- Windows notification activation package: source-verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
