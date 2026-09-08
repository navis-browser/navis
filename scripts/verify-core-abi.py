#!/usr/bin/env python3

"""Verify that the shared Navis Core contract is 64-bit architecture neutral."""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys

WORKSPACE = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = WORKSPACE / "core/rust/Cargo.toml"
HEADER_DIR = WORKSPACE / "core/rust/ffi/include"
HEADER = HEADER_DIR / "NavisCoreFFI.h"
RUST_FFI = WORKSPACE / "core/rust/ffi/src/lib.rs"
RUST_TARGETS = (
    "aarch64-unknown-linux-gnu",
    "aarch64-pc-windows-msvc",
)
ABI_ENTRYPOINTS = (
    "navis_core_runtime_activate_session",
    "navis_core_runtime_attach",
    "navis_core_runtime_begin_navigation",
    "navis_core_runtime_can_attach",
    "navis_core_runtime_check_invariants",
    "navis_core_runtime_close_session",
    "navis_core_runtime_close_window",
    "navis_core_runtime_create_session",
    "navis_core_runtime_create_session_with_mode",
    "navis_core_runtime_detach",
    "navis_core_runtime_finish_navigation",
    "navis_core_runtime_free",
    "navis_core_runtime_is_closed",
    "navis_core_runtime_navigation_snapshot",
    "navis_core_runtime_new",
    "navis_core_runtime_observe_navigation_start",
    "navis_core_runtime_register_view",
    "navis_core_runtime_register_window",
    "navis_core_runtime_session_count",
    "navis_core_runtime_session_is_private",
    "navis_core_runtime_set_crashed",
    "navis_core_runtime_set_navigation_history",
    "navis_core_runtime_set_navigation_identity",
    "navis_core_runtime_set_navigation_location",
    "navis_core_runtime_set_navigation_security",
    "navis_core_runtime_set_navigation_title",
    "navis_core_runtime_shutdown",
    "navis_core_runtime_stop_navigation",
    "navis_core_runtime_window_count",
)


class VerificationError(RuntimeError):
    pass


def run(command: list[str], *, input_text: str | None = None) -> None:
    print("+", " ".join(command))
    result = subprocess.run(
        command,
        cwd=WORKSPACE,
        input=input_text,
        text=True,
        check=False,
    )
    if result.returncode:
        raise VerificationError(
            f"command exited with status {result.returncode}: {' '.join(command)}"
        )


def installed_rust_targets() -> set[str]:
    result = subprocess.run(
        ["rustup", "target", "list", "--installed"],
        cwd=WORKSPACE,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise VerificationError("rustup could not enumerate installed targets")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def verify_header_source() -> None:
    try:
        source = HEADER.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise VerificationError(f"cannot read {HEADER}: {error}") from error

    required = (
        "#include <stdbool.h>",
        "#include <stddef.h>",
        "#include <stdint.h>",
        "enum NavisCoreStatus : uint32_t",
        "enum NavisCoreNavigationCommand : uint32_t",
        "enum NavisCoreNavigationStartKind : uint32_t",
        "enum NavisCoreNavigationActivity : uint32_t",
        "enum NavisCoreNavigationSecurity : uint32_t",
        "enum NavisCoreNavigationIdentity : uint32_t",
        "size_t url_length;",
        "uint64_t navigation_id;",
    )
    missing = [marker for marker in required if marker not in source]
    if missing:
        raise VerificationError(
            "Core ABI header lost fixed-width/target-width declarations: "
            + ", ".join(repr(marker) for marker in missing)
        )

    forbidden = ("unsigned long", "signed long", "__int64", "DWORD", "ULONG_PTR")
    present = [token for token in forbidden if token in source]
    if present:
        raise VerificationError(
            "Core ABI header encodes a platform data model: " + ", ".join(present)
        )

    try:
        rust_source = RUST_FFI.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise VerificationError(f"cannot read {RUST_FFI}: {error}") from error
    expected = set(ABI_ENTRYPOINTS)
    header_entrypoints = set(re.findall(r"\b(navis_core_[a-z0-9_]+)\s*\(", source))
    rust_entrypoints = set(
        re.findall(
            r'pub\s+(?:unsafe\s+)?extern\s+"C"\s+fn\s+'
            r"(navis_core_[a-z0-9_]+)\s*\(",
            rust_source,
        )
    )
    for label, actual in (
        ("C++ header", header_entrypoints),
        ("Rust exports", rust_entrypoints),
    ):
        if actual != expected:
            raise VerificationError(
                f"{label} entry-point inventory differs; missing="
                f"{sorted(expected - actual)!r}, unexpected="
                f"{sorted(actual - expected)!r}"
            )
    probe_source = cpp_probe_source()
    if "std::is_same_v<decltype(&name), type>" not in probe_source:
        raise VerificationError(
            "Core ABI C++ probe no longer compares exported function types"
        )
    probe_entrypoints = set(
        re.findall(r"ASSERT_ABI\(\s*(navis_core_[a-z0-9_]+)\s*,", probe_source)
    )
    if probe_entrypoints != expected:
        raise VerificationError(
            "C++ probe entry-point inventory differs; missing="
            f"{sorted(expected - probe_entrypoints)!r}, unexpected="
            f"{sorted(probe_entrypoints - expected)!r}"
        )
    missing_rust_contracts = [
        name
        for name in ABI_ENTRYPOINTS
        if not re.search(rf"=\s*{re.escape(name)}\s*;", rust_source)
    ]
    if missing_rust_contracts:
        raise VerificationError(
            "Rust ABI type contract is incomplete: " + ", ".join(missing_rust_contracts)
        )


def cpp_probe_source() -> str:
    return r"""
#include "NavisCoreFFI.h"

#include <cstddef>
#include <cstdint>
#include <type_traits>

static_assert(sizeof(void *) == 8);
static_assert(sizeof(size_t) == 8);
static_assert(sizeof(bool) == 1);
static_assert(sizeof(NavisCoreStatus) == sizeof(uint32_t));
static_assert(sizeof(NavisCoreNavigationCommand) == sizeof(uint32_t));
static_assert(sizeof(NavisCoreNavigationStartKind) == sizeof(uint32_t));
static_assert(sizeof(NavisCoreNavigationActivity) == sizeof(uint32_t));
static_assert(sizeof(NavisCoreNavigationSecurity) == sizeof(uint32_t));
static_assert(sizeof(NavisCoreNavigationIdentity) == sizeof(uint32_t));

static_assert(std::is_standard_layout_v<NavisCoreNavigationSnapshot>);
static_assert(alignof(NavisCoreNavigationSnapshot) == 8);
static_assert(sizeof(NavisCoreNavigationSnapshot) == 88);
static_assert(offsetof(NavisCoreNavigationSnapshot, revision) == 0);
static_assert(offsetof(NavisCoreNavigationSnapshot, navigation_id) == 8);
static_assert(offsetof(NavisCoreNavigationSnapshot, url) == 16);
static_assert(offsetof(NavisCoreNavigationSnapshot, url_length) == 24);
static_assert(offsetof(NavisCoreNavigationSnapshot, title) == 32);
static_assert(offsetof(NavisCoreNavigationSnapshot, title_length) == 40);
static_assert(offsetof(NavisCoreNavigationSnapshot, activity) == 48);
static_assert(offsetof(NavisCoreNavigationSnapshot, security) == 52);
static_assert(offsetof(NavisCoreNavigationSnapshot, identity) == 56);
static_assert(offsetof(NavisCoreNavigationSnapshot, identity_key) == 64);
static_assert(offsetof(NavisCoreNavigationSnapshot, identity_key_length) == 72);
static_assert(offsetof(NavisCoreNavigationSnapshot, can_go_back) == 80);
static_assert(offsetof(NavisCoreNavigationSnapshot, can_go_forward) == 81);
static_assert(offsetof(NavisCoreNavigationSnapshot, has_failure) == 82);
static_assert(offsetof(NavisCoreNavigationSnapshot, failure_code) == 84);

static_assert(NAVIS_CORE_OK == 0);
static_assert(NAVIS_CORE_NULL_POINTER == 1);
static_assert(NAVIS_CORE_CLOSED == 2);
static_assert(NAVIS_CORE_UNKNOWN_WINDOW == 3);
static_assert(NAVIS_CORE_UNKNOWN_VIEW == 4);
static_assert(NAVIS_CORE_UNKNOWN_SESSION == 5);
static_assert(NAVIS_CORE_CONFLICT == 6);
static_assert(NAVIS_CORE_INVARIANT_VIOLATION == 7);
static_assert(NAVIS_CORE_IDENTIFIER_EXHAUSTED == 8);
static_assert(NAVIS_CORE_INVALID_ARGUMENT == 9);
static_assert(NAVIS_CORE_NAVIGATION_LOAD == 0);
static_assert(NAVIS_CORE_NAVIGATION_RELOAD == 1);
static_assert(NAVIS_CORE_NAVIGATION_BACK == 2);
static_assert(NAVIS_CORE_NAVIGATION_FORWARD == 3);
static_assert(NAVIS_CORE_NAVIGATION_RESTORE == 4);
static_assert(NAVIS_CORE_NAVIGATION_START_STANDARD == 0);
static_assert(NAVIS_CORE_NAVIGATION_START_INTERNAL_PAGE == 1);
static_assert(NAVIS_CORE_NAVIGATION_ACTIVITY_IDLE == 0);
static_assert(NAVIS_CORE_NAVIGATION_ACTIVITY_PENDING == 1);
static_assert(NAVIS_CORE_NAVIGATION_ACTIVITY_VISIBLE == 2);
static_assert(NAVIS_CORE_NAVIGATION_ACTIVITY_SILENT == 3);
static_assert(NAVIS_CORE_SECURITY_UNKNOWN == 0);
static_assert(NAVIS_CORE_SECURITY_INSECURE == 1);
static_assert(NAVIS_CORE_SECURITY_BROKEN == 2);
static_assert(NAVIS_CORE_SECURITY_SECURE == 3);
static_assert(NAVIS_CORE_IDENTITY_UNKNOWN == 0);
static_assert(NAVIS_CORE_IDENTITY_WEB == 1);
static_assert(NAVIS_CORE_IDENTITY_INTERNAL_PAGE == 2);
static_assert(NAVIS_CORE_IDENTITY_BUILT_IN_EXTENSION == 3);
static_assert(NAVIS_CORE_IDENTITY_INTERNAL_ERROR == 4);

#define ASSERT_ABI(name, type) \
  static_assert(std::is_same_v<decltype(&name), type>)
ASSERT_ABI(navis_core_runtime_new, NavisCoreRuntime *(*)());
ASSERT_ABI(navis_core_runtime_free, void (*)(NavisCoreRuntime *));
ASSERT_ABI(navis_core_runtime_is_closed,
           NavisCoreStatus (*)(const NavisCoreRuntime *, bool *));
ASSERT_ABI(navis_core_runtime_window_count,
           NavisCoreStatus (*)(const NavisCoreRuntime *, uint64_t *));
ASSERT_ABI(navis_core_runtime_session_count,
           NavisCoreStatus (*)(const NavisCoreRuntime *, uint64_t *));
ASSERT_ABI(navis_core_runtime_register_window,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t *));
ASSERT_ABI(navis_core_runtime_close_window,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t));
ASSERT_ABI(navis_core_runtime_register_view,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t *));
ASSERT_ABI(navis_core_runtime_create_session,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t *));
ASSERT_ABI(navis_core_runtime_create_session_with_mode,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, bool,
                               uint64_t *));
ASSERT_ABI(navis_core_runtime_session_is_private,
           NavisCoreStatus (*)(const NavisCoreRuntime *, uint64_t, bool *));
ASSERT_ABI(navis_core_runtime_activate_session,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, bool *));
ASSERT_ABI(navis_core_runtime_close_session,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t));
ASSERT_ABI(navis_core_runtime_can_attach,
           NavisCoreStatus (*)(const NavisCoreRuntime *, uint64_t, uint64_t,
                               bool *));
ASSERT_ABI(navis_core_runtime_attach,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t));
ASSERT_ABI(navis_core_runtime_detach,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t));
ASSERT_ABI(navis_core_runtime_set_crashed,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, bool));
ASSERT_ABI(navis_core_runtime_begin_navigation,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint32_t,
                               const uint8_t *, size_t, bool, uint64_t *));
ASSERT_ABI(navis_core_runtime_observe_navigation_start,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, const uint8_t *,
                               size_t, bool, uint32_t, uint64_t *));
ASSERT_ABI(navis_core_runtime_set_navigation_location,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t,
                               const uint8_t *, size_t, bool *));
ASSERT_ABI(navis_core_runtime_set_navigation_title,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t,
                               const uint8_t *, size_t, bool *));
ASSERT_ABI(navis_core_runtime_set_navigation_security,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t,
                               uint32_t, bool *));
ASSERT_ABI(navis_core_runtime_set_navigation_identity,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t,
                               uint32_t, const uint8_t *, size_t, bool *));
ASSERT_ABI(navis_core_runtime_set_navigation_history,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t, bool,
                               bool, bool *));
ASSERT_ABI(navis_core_runtime_finish_navigation,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t, bool,
                               uint32_t, bool *));
ASSERT_ABI(navis_core_runtime_stop_navigation,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t, uint64_t,
                               bool *));
ASSERT_ABI(navis_core_runtime_navigation_snapshot,
           NavisCoreStatus (*)(NavisCoreRuntime *, uint64_t,
                               NavisCoreNavigationSnapshot *));
ASSERT_ABI(navis_core_runtime_check_invariants,
           NavisCoreStatus (*)(const NavisCoreRuntime *));
ASSERT_ABI(navis_core_runtime_shutdown,
           NavisCoreStatus (*)(NavisCoreRuntime *, bool *));
#undef ASSERT_ABI
"""


def verify_cpp_layout() -> None:
    compiler = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("c++")
    if not compiler:
        raise VerificationError("a C++17 compiler is required for the ABI layout gate")

    run(
        [
            compiler,
            "-std=c++17",
            "-fsyntax-only",
            "-Werror",
            "-I",
            str(HEADER_DIR),
            "-x",
            "c++",
            "-",
        ],
        input_text=cpp_probe_source(),
    )


def verify_rust_targets() -> None:
    installed = installed_rust_targets()
    missing = [target for target in RUST_TARGETS if target not in installed]
    if missing:
        install = ["rustup", "target", "add"]
        toolchain = os.environ.get("RUSTUP_TOOLCHAIN")
        if toolchain:
            install.extend(("--toolchain", toolchain))
        install.extend(missing)
        raise VerificationError(
            "missing Rust target(s): "
            + ", ".join(missing)
            + "; install with: "
            + " ".join(install)
        )

    run(
        [
            "cargo",
            "test",
            "--manifest-path",
            str(MANIFEST),
            "--workspace",
            "--locked",
        ]
    )
    for target in RUST_TARGETS:
        run(
            [
                "cargo",
                "check",
                "--manifest-path",
                str(MANIFEST),
                "--workspace",
                "--locked",
                "--target",
                target,
            ]
        )


def main() -> int:
    try:
        verify_header_source()
        verify_cpp_layout()
        verify_rust_targets()
    except VerificationError as error:
        print(f"Navis Core ABI verification failed: {error}", file=sys.stderr)
        return 1

    print("Navis Core ABI verification passed.")
    print("- x86_64 C++17 complete field/enum/entry-point ABI: verified")
    print("- host Rust Core/FFI behavior: verified")
    print("- Linux aarch64 Rust Core/FFI source target: verified")
    print("- Windows aarch64 Rust Core/FFI source target: verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
