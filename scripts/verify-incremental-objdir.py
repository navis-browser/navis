#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


"""Fail closed unless a Navis candidate object directory is safe to reuse."""

from __future__ import annotations

import argparse
import ast
import json
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[2]


class ObjectDirectoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Profile:
    object_name: str
    mozconfig_name: str
    build_app: str
    widget: str
    cpu: str
    required_options: tuple[str, ...]
    forbidden_options: tuple[str, ...]
    required_substs: tuple[tuple[str, str], ...]


PROFILES = {
    "linux": Profile(
        object_name="obj-navis-runtime-release",
        mozconfig_name="mozconfig.runtime.release",
        build_app="navis",
        widget="gtk",
        cpu="x86_64",
        required_options=(
            "--enable-project=navis",
            "--host=x86_64-unknown-linux-gnu",
            "--target=x86_64-unknown-linux-gnu",
            "--disable-tests",
            "--enable-release",
            "--disable-cargo-incremental",
            "--with-ccache=sccache",
        ),
        forbidden_options=("--disable-release",),
        required_substs=(
            ("RUSTC_VERSION", "1.94.1"),
            ("MOZ_USING_SCCACHE", "1"),
            ("MOZ_DESKTOP_EMBEDDER", "1"),
            ("MOZ_DESKTOP_EMBEDDER_DEVTOOLS", "1"),
            ("MOZ_WEBEXTENSIONS_RUNTIME", "1"),
            ("MOZ_DEVTOOLS", "all"),
        ),
    ),
    "win64": Profile(
        object_name="obj-navis-win64-release",
        mozconfig_name="mozconfig.win64.release",
        build_app="navis",
        widget="windows",
        cpu="x86_64",
        required_options=(
            "--enable-project=navis",
            "--target=x86_64-pc-windows-msvc",
            "--disable-tests",
            "--enable-release",
            "--disable-cargo-incremental",
            "--with-ccache=sccache",
        ),
        forbidden_options=("--disable-release",),
        required_substs=(
            ("RUSTC_VERSION", "1.94.1"),
            ("MOZ_USING_SCCACHE", "1"),
            ("MOZ_DESKTOP_EMBEDDER", "1"),
            ("MOZ_DESKTOP_EMBEDDER_DEVTOOLS", "1"),
            ("MOZ_WEBEXTENSIONS_RUNTIME", "1"),
            ("MOZ_DEVTOOLS", "all"),
        ),
    ),
    "android": Profile(
        object_name="obj-navis-android-aarch64-sccache",
        mozconfig_name="mozconfig.android-aarch64.sccache",
        build_app="mobile/android",
        widget="android",
        cpu="aarch64",
        required_options=(
            "--enable-project=mobile/android",
            "--host=x86_64-unknown-linux-gnu",
            "--target=aarch64-linux-android",
            "--disable-tests",
            "--disable-release",
            "--disable-cargo-incremental",
            "--with-ccache=sccache",
            "--enable-android-subproject=navis",
            "--enable-navis-core",
        ),
        forbidden_options=("--enable-release",),
        required_substs=(
            ("RUSTC_VERSION", "1.94.1"),
            ("MOZ_USING_SCCACHE", "1"),
            ("MOZ_NAVIS_ANDROID_RUNTIME", "1"),
            ("MOZ_NAVIS_CORE", "1"),
            ("MOZ_WEBEXTENSIONS_RUNTIME", "1"),
        ),
    ),
}


def scalar(node: ast.expr) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CPU"
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value
    return None


def parse_config_status(path: Path) -> dict[str, Any]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise ObjectDirectoryError(f"cannot parse {path}: {error}") from error

    result: dict[str, Any] = {}
    substs: dict[str, Any] = {}
    for node in tree.body:
        if (
            not isinstance(node, ast.Assign)
            or len(node.targets) != 1
            or not isinstance(node.targets[0], ast.Name)
        ):
            continue
        name = node.targets[0].id
        if name in {"topobjdir", "topsrcdir", "mozconfig"}:
            value = scalar(node.value)
            if isinstance(value, str):
                result[name] = value
        elif name == "substs" and isinstance(node.value, ast.Dict):
            for key_node, value_node in zip(node.value.keys, node.value.values):
                key = scalar(key_node) if key_node is not None else None
                if isinstance(key, str):
                    substs[key] = scalar(value_node)
    result["substs"] = substs
    return result


def same_path(actual: Any, expected: Path) -> bool:
    if not isinstance(actual, str) or not Path(actual).is_absolute():
        return False
    return Path(actual).resolve() == expected.resolve()


def canonical_workspace(root: Path) -> Path:
    root = root.resolve()
    if root.name == "navis" and (root.parent / "runtime").is_dir():
        return root.parent
    return root


def recorded_release_option(root: Path, objdir: Path, mozconfig: Path) -> bool:
    record = objdir / ".mozconfig.json"
    try:
        if record.is_symlink() or not record.is_file():
            return False
        if not (
            mozconfig.stat().st_mtime_ns
            <= record.stat().st_mtime_ns
            <= (objdir / "config.status").stat().st_mtime_ns
        ):
            return False
        payload = json.loads(record.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
        inputs = payload.get("mozconfig")
        if not isinstance(inputs, dict):
            return False
        if not all(
            same_path(actual, expected)
            for actual, expected in (
                (payload.get("topsrcdir"), root / "runtime/gecko"),
                (payload.get("topobjdir"), objdir),
                (inputs.get("topobjdir"), objdir),
                (inputs.get("path"), mozconfig),
            )
        ):
            return False
        arguments = inputs.get("configure_args")
        if not isinstance(arguments, list) or not all(
            isinstance(value, str) for value in arguments
        ):
            return False
        return [
            value for value in arguments
            if value.split("=", 1)[0] in {"--enable-release", "--disable-release"}
        ] == ["--enable-release"]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def verify(platform: str, phase: str, workspace: Path = WORKSPACE) -> list[str]:
    root = canonical_workspace(workspace)
    profile = PROFILES[platform]
    gecko = root / "runtime/gecko"
    objdir = gecko / profile.object_name
    mozconfig = root / "runtime" / profile.mozconfig_name
    failures: list[str] = []

    if gecko.is_symlink() or not gecko.is_dir():
        return [f"Gecko source root is missing or is a symlink: {gecko}"]
    if mozconfig.is_symlink() or not mozconfig.is_file():
        failures.append(f"candidate mozconfig is missing or is a symlink: {mozconfig}")
    if objdir.is_symlink() or not objdir.is_dir():
        failures.append(f"candidate object directory is missing or is a symlink: {objdir}")
        if platform == "linux":
            development = gecko / "obj-navis-runtime" / "config.status"
            if development.is_file():
                failures.append(
                    "obj-navis-runtime is a development graph and cannot stand in for "
                    "the missing obj-navis-runtime-release graph"
                )
        return failures

    status = objdir / "config.status"
    required_files = (status, objdir / "Makefile", objdir / "backend.RecursiveMakeBackend")
    for path in required_files:
        if path.is_symlink() or not path.is_file():
            failures.append(f"incremental graph metadata is missing or is a symlink: {path}")
    if failures:
        return failures

    try:
        values = parse_config_status(status)
    except ObjectDirectoryError as error:
        return [str(error)]
    substs = values.get("substs", {})

    for key, expected in (
        ("topobjdir", objdir),
        ("topsrcdir", gecko),
        ("mozconfig", mozconfig),
    ):
        if not same_path(values.get(key), expected):
            failures.append(
                f"{status} {key} differs: expected {expected}, "
                f"found {values.get(key)!r}"
            )
    for key, expected in (
        ("MOZ_BUILD_APP", profile.build_app),
        ("MOZ_WIDGET_TOOLKIT", profile.widget),
        ("TARGET_CPU", profile.cpu),
    ):
        if substs.get(key) != expected:
            failures.append(
                f"{status} {key} differs: expected {expected!r}, "
                f"found {substs.get(key)!r}"
            )

    if phase == "configured":
        if mozconfig.is_file() and status.stat().st_mtime_ns < mozconfig.stat().st_mtime_ns:
            failures.append(
                f"{status} predates {mozconfig}; run the guarded configure phase"
            )
        configure_options = substs.get("MOZ_CONFIGURE_OPTIONS")
        if not isinstance(configure_options, str):
            failures.append(f"{status} has no structured MOZ_CONFIGURE_OPTIONS")
            option_set: set[str] = set()
        else:
            try:
                option_set = set(shlex.split(configure_options))
            except ValueError as error:
                failures.append(f"{status} has invalid MOZ_CONFIGURE_OPTIONS: {error}")
                option_set = set()
        for option in profile.required_options:
            if option not in option_set:
                # Gecko omits default-valued switches from its normalized options.
                if option == "--enable-release" and recorded_release_option(
                    root, objdir, mozconfig
                ):
                    continue
                failures.append(f"{status} lacks configured option {option}")
        if "--enable-release" in profile.required_options and substs.get(
            "DEVELOPER_OPTIONS"
        ):
            failures.append(f"{status} enables DEVELOPER_OPTIONS in a release graph")
        for option in profile.forbidden_options:
            if option in option_set:
                failures.append(f"{status} contains forbidden configured option {option}")
        for key, expected in profile.required_substs:
            if substs.get(key) != expected:
                failures.append(
                    f"{status} {key} differs: expected {expected!r}, "
                    f"found {substs.get(key)!r}"
                )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=tuple(PROFILES), required=True)
    parser.add_argument(
        "--phase",
        choices=("identity", "configured"),
        default="configured",
        help="identity permits an in-place reconfigure; configured is the build gate",
    )
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    args = parser.parse_args(argv)
    failures = verify(args.platform, args.phase, args.workspace)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    profile = PROFILES[args.platform]
    print(
        f"Navis {args.platform} incremental object-directory {args.phase} "
        f"verification passed: runtime/gecko/{profile.object_name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
