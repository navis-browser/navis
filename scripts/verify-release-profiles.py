#!/usr/bin/env python3

"""Reject development-only settings in Navis shipping build profiles."""

from __future__ import annotations

import re
import sys
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent


def main() -> int:
    failures: list[str] = []
    expected_versions = {
        "product/config/version.txt": "0.2.0",
        "product/config/version_display.txt": "0.2.0-dev",
    }
    for relative, expected in expected_versions.items():
        try:
            value = (WORKSPACE / relative).read_text(encoding="utf-8").strip()
        except OSError as error:
            failures.append(f"cannot read {relative}: {error}")
            continue
        if value != expected:
            failures.append(f"{relative} must be {expected}, found {value!r}")
    profiles = {
        "mozconfig.runtime.release": (
            "obj-navis-runtime-release",
            (
                "ac_add_options --host=x86_64-unknown-linux-gnu",
                "ac_add_options --target=x86_64-unknown-linux-gnu",
            ),
        ),
        "mozconfig.win64.release": (
            "obj-navis-win64-release",
            (
                "ac_add_options --target=x86_64-pc-windows-msvc",
                "ac_add_options --enable-bootstrap",
                "ac_add_options --disable-bits-download",
            ),
        ),
    }
    common_required = (
        "export MOZ_REQUIRE_SIGNING=1",
        "ac_add_options --enable-project=navis",
        "ac_add_options --disable-tests",
        "ac_add_options --enable-release",
        "ac_add_options --disable-cargo-incremental",
        "ac_add_options --with-ccache=sccache",
        "ac_add_options --enable-desktop-embedder",
        "ac_add_options --enable-webextensions-runtime",
        "ac_add_options --enable-webdriver",
        "ac_add_options --disable-observability-runtime",
        "ac_add_options --disable-webrtc",
    )
    forbidden = (
        "ac_add_options --disable-release",
        "--enable-tests",
    )
    for relative, (objdir, platform_required) in profiles.items():
        path = WORKSPACE / relative
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            failures.append(f"cannot read {relative}: {error}")
            continue
        for marker in common_required + platform_required:
            if marker not in content:
                failures.append(f"{relative} lacks {marker}")
        for marker in forbidden:
            if marker in content:
                failures.append(f"{relative} contains {marker}")
        expected_objdir = f"MOZ_OBJDIR=@TOPSRCDIR@/{objdir}"
        objdir_lines = re.findall(
            r"(?m)^mk_add_options (MOZ_OBJDIR=\S+)\s*$", content
        )
        if objdir_lines != [expected_objdir]:
            failures.append(
                f"{relative} must own exactly {expected_objdir}, "
                f"found {objdir_lines}"
            )
        if not re.search(r"(?m)^mk_add_options AUTOCLOBBER=\s*$", content):
            failures.append(
                f"{relative} must disable automatic object-directory clobbering"
            )
        if re.search(r"(?m)^mk_add_options AUTOCLOBBER=\S+", content):
            failures.append(f"{relative} enables automatic clobbering")

    android_relative = "mozconfig.android-aarch64.sccache"
    android_required = (
        "export MOZ_REQUIRE_SIGNING=1",
        "ac_add_options --enable-project=mobile/android",
        "ac_add_options --host=x86_64-unknown-linux-gnu",
        "ac_add_options --target=aarch64-linux-android",
        "ac_add_options --with-navis-product-version-file-path=navis/config",
        "ac_add_options --enable-android-subproject=navis",
        "ac_add_options --enable-navis-core",
        "ac_add_options --disable-tests",
        "ac_add_options --disable-release",
        "ac_add_options --disable-cargo-incremental",
        "ac_add_options --enable-webextensions-runtime",
        "ac_add_options --with-ccache=sccache",
        "MOZ_OBJDIR=@TOPSRCDIR@/obj-navis-android-aarch64-sccache",
    )
    try:
        android_content = (WORKSPACE / android_relative).read_text(encoding="utf-8")
    except OSError as error:
        failures.append(f"cannot read {android_relative}: {error}")
    else:
        for marker in android_required:
            if marker not in android_content:
                failures.append(f"{android_relative} lacks {marker}")
        for marker in (
            "ac_add_options --target=x86_64-linux-android",
            "ac_add_options --enable-release",
            "ac_add_options --enable-tests",
        ):
            if marker in android_content:
                failures.append(f"{android_relative} contains {marker}")
        expected_android_objdir = (
            "MOZ_OBJDIR=@TOPSRCDIR@/obj-navis-android-aarch64-sccache"
        )
        android_objdir_lines = re.findall(
            r"(?m)^mk_add_options (MOZ_OBJDIR=\S+)\s*$", android_content
        )
        if android_objdir_lines != [expected_android_objdir]:
            failures.append(
                f"{android_relative} must own exactly {expected_android_objdir}, "
                f"found {android_objdir_lines}"
            )
        if not re.search(r"(?m)^mk_add_options AUTOCLOBBER=\s*$", android_content):
            failures.append(
                f"{android_relative} must disable automatic object-directory clobbering"
            )
        if re.search(r"(?m)^mk_add_options AUTOCLOBBER=\S+", android_content):
            failures.append(f"{android_relative} enables automatic clobbering")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("Navis release-profile verification passed.")
    print("- Linux x86_64: release options, dedicated sccache-backed graph")
    print("- Windows x86_64: release options, dedicated sccache-backed graph")
    print("- Android aarch64: signed-source debug-candidate profile")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
