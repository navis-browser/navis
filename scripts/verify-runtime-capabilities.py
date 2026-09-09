#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Verify the build graph and package against the runtime capability ledger."""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import re
import sys
import zipfile
from pathlib import Path


FALSE_VALUES = {None, False, "", "0", 0}
SUPPORTED_CHECKS = {
    "config",
    "file_content_forbidden",
    "file_content_required",
    "files_forbidden",
    "files_required",
    "generated_forbidden",
    "generated_required",
    "graph_forbidden",
    "graph_required",
    "omni_content_forbidden",
    "omni_content_required",
    "omni_forbidden",
    "omni_required",
    "platform_checks",
    "rust_graph_forbidden",
    "rust_graph_required",
}


def parse_args() -> argparse.Namespace:
    workspace = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ledger",
        type=Path,
        default=workspace / "config/runtime-capabilities.json",
    )
    parser.add_argument(
        "--objdir", type=Path, default=workspace / "../runtime/gecko/obj-navis-runtime"
    )
    parser.add_argument("--runtime", type=Path)
    parser.add_argument(
        "--scope", choices=("all", "build", "package"), default="all"
    )
    parser.add_argument(
        "--profile",
        help="capability profile from the ledger (defaults to ledger.profile)",
    )
    return parser.parse_args()


def literal_assignment(path: Path, name: str) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            if not isinstance(node.value, ast.Dict):
                raise ValueError(f"{name} in {path} is not a dictionary")
            result: dict[str, object] = {}
            for key_node, value_node in zip(node.value.keys, node.value.values):
                try:
                    key = ast.literal_eval(key_node)
                    value = ast.literal_eval(value_node)
                except (TypeError, ValueError):
                    # config.status also contains typed configure objects. The
                    # ledger deliberately checks only scalar feature values.
                    continue
                if isinstance(key, str):
                    result[key] = value
            return result
    raise ValueError(f"{name} assignment is missing from {path}")


def enabled(value: object) -> bool:
    return value not in FALSE_VALUES


def regex_present(pattern: str, entries: list[str]) -> bool:
    expression = re.compile(pattern, re.MULTILINE)
    return any(expression.search(entry) for entry in entries)


def load_linked_rust_graph(
    objdir: Path, config: dict[str, object]
) -> list[str]:
    """Return Gecko-relative Rust sources from the final linked gkrust dep-info."""

    target = config.get("RUST_TARGET")
    os_target = config.get("OS_TARGET")
    top_srcdir = config.get("top_srcdir")
    required_values = (target, os_target, top_srcdir)
    if not all(isinstance(value, str) and value for value in required_values):
        raise ValueError(
            "RUST_TARGET, OS_TARGET, and top_srcdir are required to inspect "
            "the linked Rust graph"
        )

    filename = "gkrust.d" if os_target == "WINNT" else "libgkrust.d"
    dep_info = objdir / target / "release" / filename
    if not dep_info.is_file():
        raise FileNotFoundError(f"linked gkrust dep-info is missing: {dep_info}")

    # Keep the lexical mount path. prepare-desktop-embedder.py deliberately
    # exposes navis-core below the Gecko tree with a symlink; resolving it
    # would incorrectly make this Core-owned source look external.
    source_root = Path(os.path.abspath(top_srcdir))
    entries: set[str] = set()
    content = dep_info.read_text(errors="replace").replace("\\\n", " ")
    for token in content.split():
        token = token.rstrip(":")
        if token.startswith("env-dep:") or not token.endswith(".rs"):
            continue
        source = Path(token)
        if not source.is_absolute():
            source = source_root / source
        source = Path(os.path.abspath(source))
        try:
            entries.add(source.relative_to(source_root).as_posix())
        except ValueError:
            # Capability checks intentionally name sources owned by the Gecko
            # tree. External toolchain and platform crates remain the source-
            # domain auditor's responsibility.
            continue
    return sorted(entries)


def verify_config(
    capability: str,
    expected: dict[str, object],
    config: dict[str, object],
    failures: list[str],
) -> None:
    for key, wanted in expected.items():
        actual = config.get(key)
        if isinstance(wanted, bool):
            matches = enabled(actual) is wanted
        else:
            matches = actual == wanted
        if not matches:
            failures.append(
                f"{capability}: config {key} expected {wanted!r}, got {actual!r}"
            )


def require_patterns(
    capability: str,
    label: str,
    patterns: list[str],
    entries: list[str],
    failures: list[str],
) -> None:
    for pattern in patterns:
        if not regex_present(pattern, entries):
            failures.append(f"{capability}: required {label} pattern absent: {pattern}")


def forbid_patterns(
    capability: str,
    label: str,
    patterns: list[str],
    entries: list[str],
    failures: list[str],
) -> None:
    for pattern in patterns:
        if regex_present(pattern, entries):
            failures.append(f"{capability}: forbidden {label} pattern present: {pattern}")


def verify_generated_files(
    capability: str,
    checks: dict[str, object],
    objdir: Path,
    failures: list[str],
) -> None:
    required = checks.get("generated_required", {})
    forbidden = checks.get("generated_forbidden", {})
    paths = set(required) | set(forbidden)

    for relative in sorted(paths):
        path = objdir / relative
        if not path.is_file():
            failures.append(
                f"{capability}: generated build file is missing: {relative}"
            )
            continue
        entries = [path.read_text(encoding="utf-8")]
        require_patterns(
            capability,
            f"generated file {relative}",
            required.get(relative, []),
            entries,
            failures,
        )
        forbid_patterns(
            capability,
            f"generated file {relative}",
            forbidden.get(relative, []),
            entries,
            failures,
        )


def verify_runtime_file_contents(
    capability: str,
    checks: dict[str, object],
    runtime: Path,
    failures: list[str],
) -> None:
    required = checks.get("file_content_required", {})
    forbidden = checks.get("file_content_forbidden", {})
    paths = set(required) | set(forbidden)

    for relative in sorted(paths):
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            failures.append(
                f"{capability}: unsafe runtime content path: {relative}"
            )
            continue
        path = runtime / relative_path
        if not path.is_file():
            failures.append(
                f"{capability}: runtime content file is missing: {relative}"
            )
            continue
        entries = [path.read_text(encoding="utf-8")]
        require_patterns(
            capability,
            f"runtime file {relative}",
            required.get(relative, []),
            entries,
            failures,
        )
        forbid_patterns(
            capability,
            f"runtime file {relative}",
            forbidden.get(relative, []),
            entries,
            failures,
        )


def verify_omni_contents(
    capability: str,
    checks: dict[str, object],
    omni: Path,
    failures: list[str],
) -> None:
    required = checks.get("omni_content_required", {})
    forbidden = checks.get("omni_content_forbidden", {})
    paths = set(required) | set(forbidden)

    if not paths:
        return
    with zipfile.ZipFile(omni) as archive:
        for relative in sorted(paths):
            try:
                entries = [archive.read(relative).decode("utf-8")]
            except KeyError:
                failures.append(
                    f"{capability}: omni content file is missing: {relative}"
                )
                continue
            require_patterns(
                capability,
                f"omni file {relative}",
                required.get(relative, []),
                entries,
                failures,
            )
            forbid_patterns(
                capability,
                f"omni file {relative}",
                forbidden.get(relative, []),
                entries,
                failures,
            )


def resolve_profile(
    ledger: dict[str, object], requested: str | None
) -> tuple[str, list[dict[str, object]]]:
    default_profile = ledger.get("profile")
    profile_name = requested or default_profile
    profiles = ledger.get("profiles", {})
    if not isinstance(profile_name, str) or profile_name not in profiles:
        available = ", ".join(sorted(profiles))
        raise ValueError(
            f"unknown capability profile {profile_name!r}; available: {available}"
        )

    def collect_overrides(name: str, stack: tuple[str, ...] = ()) -> dict[str, object]:
        if name in stack:
            chain = " -> ".join((*stack, name))
            raise ValueError(f"capability profile inheritance cycle: {chain}")
        if name not in profiles:
            raise ValueError(f"unknown inherited capability profile {name!r}")
        profile = profiles[name]
        if not isinstance(profile, dict):
            raise ValueError(f"capability profile {name!r} is not an object")
        parent = profile.get("inherits")
        if parent is not None and not isinstance(parent, str):
            raise ValueError(f"capability profile {name!r} has invalid inheritance")
        combined = (
            collect_overrides(parent, (*stack, name)) if parent is not None else {}
        )
        overrides = profile.get("overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError(f"capability profile {name!r} overrides are invalid")
        combined.update(copy.deepcopy(overrides))
        return combined

    overrides = collect_overrides(profile_name)

    capabilities = ledger.get("capabilities")
    if not isinstance(capabilities, list):
        raise ValueError("capability ledger is missing its capabilities array")
    known_ids = {
        item.get("id") for item in capabilities if isinstance(item, dict)
    }
    unknown_ids = sorted(set(overrides) - known_ids)
    if unknown_ids:
        raise ValueError(
            f"capability profile {profile_name!r} overrides unknown IDs: "
            + ", ".join(unknown_ids)
        )

    resolved: list[dict[str, object]] = []
    for original in capabilities:
        if not isinstance(original, dict) or not isinstance(original.get("id"), str):
            raise ValueError("every capability must be an object with a string id")
        item = copy.deepcopy(original)
        override = overrides.get(item["id"], {})
        if not isinstance(override, dict):
            raise ValueError(
                f"override for capability {item['id']!r} is not an object"
            )
        item.update(copy.deepcopy(override))
        resolved.append(item)
    return profile_name, resolved


def infer_runtime_os(runtime: Path) -> str | None:
    if (runtime / "navis.exe").is_file():
        return "WINNT"
    if (runtime / "libxul.so").is_file():
        return "Linux"
    return None


def merge_check_values(base: object, override: object) -> object:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = copy.deepcopy(base)
        for key, value in override.items():
            merged[key] = (
                merge_check_values(merged[key], value)
                if key in merged
                else copy.deepcopy(value)
            )
        return merged
    if isinstance(base, list) and isinstance(override, list):
        return [*copy.deepcopy(base), *copy.deepcopy(override)]
    return copy.deepcopy(override)


def resolve_platform_checks(
    checks: dict[str, object], target_os: str | None
) -> dict[str, object]:
    resolved = copy.deepcopy(checks)
    platform_checks = resolved.pop("platform_checks", {})
    if not platform_checks:
        return resolved
    if not isinstance(platform_checks, dict):
        raise ValueError("platform_checks must be an object")
    if target_os is None:
        raise ValueError("cannot select platform_checks without a target OS")
    override = platform_checks.get(target_os, {})
    if not isinstance(override, dict):
        raise ValueError(f"platform_checks[{target_os!r}] must be an object")
    return merge_check_values(resolved, override)


def main() -> int:
    args = parse_args()
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    if ledger.get("schema_version") != 1:
        raise ValueError("unsupported runtime capability ledger schema")
    profile_name, capabilities = resolve_profile(ledger, args.profile)

    runtime = args.runtime or args.objdir / "dist/navis"
    failures: list[str] = []
    build_enabled = args.scope in ("all", "build")
    package_enabled = args.scope in ("all", "package")

    config: dict[str, object] = {}
    graph_entries: list[str] = []
    rust_graph_entries: list[str] = []
    if build_enabled:
        status = args.objdir / "config.status"
        backend = args.objdir / "backend.RecursiveMakeBackend"
        if not status.is_file() or not backend.is_file():
            raise FileNotFoundError(
                f"configured build metadata is missing below {args.objdir}"
            )
        config.update(literal_assignment(status, "substs"))
        config.update(literal_assignment(status, "defines"))
        graph_entries = backend.read_text(encoding="utf-8").splitlines()
        if any(
            key in item.get("checks", {})
            for item in capabilities
            for key in ("rust_graph_required", "rust_graph_forbidden")
        ):
            rust_graph_entries = load_linked_rust_graph(args.objdir, config)

    target_os = config.get("OS_TARGET")
    if not isinstance(target_os, str) or not target_os:
        target_os = infer_runtime_os(runtime) if package_enabled else None

    omni_entries: list[str] = []
    file_entries: list[str] = []
    if package_enabled:
        omni = runtime / "omni.ja"
        if not omni.is_file():
            raise FileNotFoundError(f"runtime package is missing {omni}")
        with zipfile.ZipFile(omni) as archive:
            omni_entries = archive.namelist()
        file_entries = [
            path.relative_to(runtime).as_posix()
            for path in runtime.rglob("*")
            if path.is_file() or path.is_symlink()
        ]

    checked = 0
    for item in capabilities:
        checks = item.get("checks")
        if not checks:
            continue
        if not isinstance(checks, dict):
            raise ValueError(f"checks for {item['id']!r} must be an object")
        unknown_checks = sorted(set(checks) - SUPPORTED_CHECKS)
        if unknown_checks:
            raise ValueError(
                f"checks for {item['id']!r} contain unsupported keys: "
                + ", ".join(unknown_checks)
            )
        checks = resolve_platform_checks(checks, target_os)
        checked += 1
        capability = item["id"]
        if build_enabled:
            verify_config(
                capability, checks.get("config", {}), config, failures
            )
            require_patterns(
                capability,
                "graph",
                checks.get("graph_required", []),
                graph_entries,
                failures,
            )
            forbid_patterns(
                capability,
                "graph",
                checks.get("graph_forbidden", []),
                graph_entries,
                failures,
            )
            require_patterns(
                capability,
                "linked Rust graph",
                checks.get("rust_graph_required", []),
                rust_graph_entries,
                failures,
            )
            forbid_patterns(
                capability,
                "linked Rust graph",
                checks.get("rust_graph_forbidden", []),
                rust_graph_entries,
                failures,
            )
            verify_generated_files(capability, checks, args.objdir, failures)
        if package_enabled:
            require_patterns(
                capability,
                "omni",
                [f"^{re.escape(path)}$" for path in checks.get("omni_required", [])],
                omni_entries,
                failures,
            )
            forbid_patterns(
                capability,
                "omni",
                checks.get("omni_forbidden", []),
                omni_entries,
                failures,
            )
            require_patterns(
                capability,
                "file",
                [f"^{re.escape(path)}$" for path in checks.get("files_required", [])],
                file_entries,
                failures,
            )
            forbid_patterns(
                capability,
                "file",
                checks.get("files_forbidden", []),
                file_entries,
                failures,
            )
            verify_runtime_file_contents(
                capability, checks, runtime, failures
            )
            verify_omni_contents(capability, checks, omni, failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print(
            f"Runtime capability verification failed: {len(failures)} violation(s)",
            file=sys.stderr,
        )
        return 1

    print(
        f"Runtime capability verification passed: {checked} checked capabilities "
        f"({args.scope}, {profile_name})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
