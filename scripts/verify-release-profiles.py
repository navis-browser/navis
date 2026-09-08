#!/usr/bin/env python3

"""Reject development-only settings in Navis shipping build profiles."""

from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent


def main() -> int:
    failures: list[str] = []
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
        "ac_add_options --enable-desktop-embedder",
        "ac_add_options --enable-webextensions-runtime",
        "ac_add_options --enable-webdriver",
        "ac_add_options --disable-observability-runtime",
        "ac_add_options --disable-webrtc",
    )
    forbidden = (
        "ac_add_options --disable-release",
        "--with-ccache",
        "--enable-tests",
    )
    for relative, (objdir, platform_required) in profiles.items():
        path = WORKSPACE / "../runtime" / relative
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
        if expected_objdir not in content:
            failures.append(f"{relative} does not own {objdir}")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("Navis release-profile verification passed.")
    print("- Linux x86_64: release options, cache-free object graph")
    print("- Windows x86_64: release options, cache-free object graph")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
