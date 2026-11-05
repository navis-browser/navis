#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Verify pinned app-local VC Runtime and Windows PE import dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


POLICY = Path(__file__).resolve().parents[1] / "config/windows-runtime.json"
REDIST_VARIABLES = {
    "MSVC_C_RUNTIME_DLL": "vcruntime140.dll",
    "MSVC_C_RUNTIME_1_DLL": "vcruntime140_1.dll",
    "MSVC_CXX_RUNTIME_DLL": "msvcp140.dll",
    "MSVC_CXX_RUNTIME_ATOMIC_WAIT_DLL": "msvcp140_atomic_wait.dll",
}


class VerificationError(RuntimeError):
    pass


def readobj_path() -> str:
    selected = os.environ.get("NAVIS_LLVM_READOBJ")
    candidates = [selected] if selected else [
        shutil.which("llvm-readobj"),
        str(Path.home() / ".mozbuild/clang/bin/llvm-readobj"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise VerificationError("llvm-readobj is required; set NAVIS_LLVM_READOBJ")


def parse_imports(output: str) -> list[dict[str, str]]:
    if not re.search(r"^Format: COFF-x86-64$", output, re.M) or not re.search(
        r"^  Machine: IMAGE_FILE_MACHINE_AMD64 \(0x8664\)$", output, re.M
    ):
        raise VerificationError("expected a PE32+ AMD64 executable or DLL")
    blocks = re.findall(r"^(Import|DelayImport) \{\n(.*?)^\}", output, re.M | re.S)
    if len(blocks) != len(re.findall(r"^(?:Import|DelayImport) \{$", output, re.M)):
        raise VerificationError("cannot parse PE import descriptors")
    imports = []
    for kind, body in blocks:
        names = re.findall(r"^  Name: (.+)$", body, re.M)
        if len(names) != 1 or not re.fullmatch(r"[A-Za-z0-9_.-]+", names[0]):
            raise VerificationError("invalid PE import library name")
        imports.append({"kind": kind, "name": names[0].lower()})
    return imports


def inspect_pe(path: Path, tool: str) -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            [tool, "--file-headers", "--coff-imports", str(path)],
            check=True, capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise VerificationError(f"cannot inspect {path}: {error}") from error
    return parse_imports(result.stdout)


def verify(root: Path, policy: dict, tool: str, *, redist_only: bool = False) -> dict:
    if not root.is_dir() or root.is_symlink():
        raise VerificationError(f"invalid runtime directory: {root}")
    if not redist_only and not (root / "navis.exe").is_file():
        raise VerificationError(f"missing Navis executable: {root}")
    if policy["schema_version"] != 1 or policy["architecture"] != "x86_64":
        raise VerificationError("unsupported Windows runtime policy")
    pinned = policy["redist"]["files"]
    system = set(policy["system_libraries"])
    if system.intersection(pinned):
        raise VerificationError("VC Runtime must not be classified as a system library")
    for name, expected in pinned.items():
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise VerificationError(f"missing app-local runtime: {path}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != expected:
            raise VerificationError(f"unreviewed Redist bytes: {path}")
    paths = [root / name for name in pinned] if redist_only else [
        path for path in sorted(root.rglob("*"))
        if path.suffix.lower() in {".exe", ".dll"}
    ]
    # The current portable package has one executable/library search root.
    # New plugin layouts need an explicit loader-policy review.
    libraries = {}
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise VerificationError(f"invalid PE file: {path}")
        if path.parent != root:
            raise VerificationError(f"PE file outside application directory: {path}")
        key = (path.parent, path.name.lower())
        if key in libraries:
            raise VerificationError(f"case-colliding PE files: {path}")
        if path.name.lower() in system:
            raise VerificationError(f"bundled DLL shadows Windows: {path}")
        if re.match(r"(?:vcruntime|msvcp|concrt)\d", path.name.lower()) and path.name.lower() not in pinned:
            raise VerificationError(f"unpinned VC Runtime file: {path}")
        libraries[key] = path
    graph = {}
    failures = []
    for path in paths:
        try:
            imports = inspect_pe(path, tool)
        except VerificationError as error:
            raise VerificationError(f"{path}: {error}") from error
        graph[path.relative_to(root).as_posix()] = imports
        for dependency in imports:
            name = dependency["name"]
            if name in system or (root, name) in libraries:
                continue
            failures.append(f"{path.name}: unresolved {dependency['kind']} {name}")
    if failures:
        raise VerificationError("; ".join(failures))
    return {"passed": True, "redist_version": policy["redist"]["version"],
            "redist_sha256": pinned, "pe_count": len(paths), "imports": graph}


def configured_redist(objdir: Path) -> Path:
    config = json.loads((objdir / "config.status.json").read_text(encoding="utf-8"))["substs"]
    if config.get("OS_TARGET") != "WINNT" or config.get("MOZ_NO_DEBUG_RTL") != "1":
        raise VerificationError("expected a Windows build using release CRT")
    value = config.get("WIN32_REDIST_DIR")
    if not value or not Path(value).is_absolute():
        raise VerificationError("configure did not enable WIN32_REDIST_DIR")
    for key, expected in REDIST_VARIABLES.items():
        if config.get(key) != expected:
            raise VerificationError(f"unexpected configured {key}: {config.get(key)!r}")
    return Path(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--runtime", type=Path)
    group.add_argument("--objdir", type=Path)
    parser.add_argument("--json", action="store_true", help="print full dependency evidence")
    args = parser.parse_args()
    try:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        root = configured_redist(args.objdir) if args.objdir else args.runtime
        report = verify(root, policy, readobj_path(), redist_only=bool(args.objdir))
    except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"Windows runtime verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2) if args.json else
          f"Windows runtime verified: {report['pe_count']} x64 PE files, "
          f"pinned app-local Redist {report['redist_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
