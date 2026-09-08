#!/usr/bin/env python3

"""Create or verify the exact Navis project-source snapshot used for a release."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORIES = (
    "navis/config",
    "navis/docs",
    "navis/scripts",
    "navis/tests",
    "runtime/core",
    "runtime/android",
    "runtime/embedder",
    "runtime/config",
    "runtime/patches",
    "runtime/scripts",
    "runtime/vendor",
    "platform",
)
TOP_LEVEL_FILES = (
    "navis/.gitignore",
    "navis/AGENTS.md",
    "navis/LICENSE",
    "navis/README.md",
    "runtime/.gitignore",
    "runtime/AGENTS.md",
    "runtime/LICENSE",
    "runtime/README.md",
    "platform/.gitignore",
    "platform/AGENTS.md",
    "platform/LICENSE",
    "platform/README.md",
)
EXCLUDED_PREFIXES = (
    "platform/android/.cxx/",
    "platform/android/.gradle/",
    "platform/android/build/",
    "runtime/core/rust/target/",
)
EXCLUDED_PARTS = {"__pycache__", ".ruff_cache"}
EXCLUDED_TRANSITION_PATHS = {
    "navis/config/gecko-esr-review-routes.json",
    "navis/config/gecko-semantic-ports.json",
    "navis/scripts/prepare-desktop-embedder.py",
    "navis/scripts/review-gecko-esr-update.py",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FreezeError(RuntimeError):
    pass


def canonical_workspace(root: Path) -> Path:
    root = root.resolve()
    required = ("navis", "runtime", "platform")
    if all((root / name).is_dir() for name in required):
        return root
    parent = root.parent
    if root.name == "navis" and all((parent / name).is_dir() for name in required):
        return parent
    return root


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise FreezeError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FreezeError(f"cannot read JSON {path}: {error}") from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def excluded(relative: str) -> bool:
    path = Path(relative)
    return (
        any(relative.startswith(prefix) for prefix in EXCLUDED_PREFIXES)
        or any(part in EXCLUDED_PARTS for part in path.parts)
        or path.suffix in {".pyc", ".pyo"}
        or path.name == ".DS_Store"
    )


def source_paths(root: Path) -> list[Path]:
    missing = [name for name in TOP_LEVEL_FILES if not (root / name).is_file()]
    missing.extend(name for name in SOURCE_DIRECTORIES if not (root / name).is_dir())
    mozconfigs = sorted((root / "runtime").glob("mozconfig.*"))
    if not mozconfigs:
        missing.append("mozconfig.*")
    if missing:
        raise FreezeError(f"source-freeze scope is incomplete: {missing}")

    paths = [root / name for name in TOP_LEVEL_FILES]
    paths.extend(path for path in mozconfigs if path.is_file())
    for directory in SOURCE_DIRECTORIES:
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root).as_posix()
            if relative in EXCLUDED_TRANSITION_PATHS:
                continue
            if excluded(relative):
                continue
            if path.is_symlink():
                raise FreezeError(f"source-freeze scope contains a symlink: {relative}")
            if path.is_file():
                paths.append(path)
            elif not path.is_dir():
                raise FreezeError(f"source-freeze scope contains a special file: {relative}")
    relative_paths = [path.relative_to(root).as_posix() for path in paths]
    if len(relative_paths) != len(set(relative_paths)):
        raise FreezeError("source-freeze scope contains duplicate paths")
    return [root / relative for relative in sorted(relative_paths)]


def file_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in source_paths(root):
        metadata = path.stat()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
                "size": metadata.st_size,
                "sha256": sha256_file(path),
            }
        )
    return entries


def aggregate(entries: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def current_identity(root: Path) -> tuple[dict[str, Any], int]:
    upstream = load_json(root / "runtime/vendor/gecko.json")
    ports = load_json(root / "runtime/config/gecko-semantic-ports.json")
    if not isinstance(upstream, dict):
        raise FreezeError("vendor/gecko.json is not an object")
    if not isinstance(ports, dict) or not isinstance(ports.get("ports"), list):
        raise FreezeError("semantic-port ledger has no ports array")
    return upstream, len(ports["ports"])


def build_manifest(root: Path, created_at: str) -> dict[str, Any]:
    root = canonical_workspace(root)
    entries = file_entries(root)
    upstream, port_count = current_identity(root)
    return {
        "schema_version": 1,
        "kind": "navis-1.0-project-source-freeze",
        "created_at": created_at,
        "upstream": upstream,
        "semantic_port_count": port_count,
        "file_count": len(entries),
        "aggregate_sha256": aggregate(entries),
        "files": entries,
    }


def validate_manifest_shape(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise FreezeError("source-freeze manifest is not an object")
    expected_keys = {
        "schema_version",
        "kind",
        "created_at",
        "upstream",
        "semantic_port_count",
        "file_count",
        "aggregate_sha256",
        "files",
    }
    if set(manifest) != expected_keys:
        raise FreezeError("source-freeze manifest keys differ")
    if manifest["schema_version"] != 1 or manifest["kind"] != (
        "navis-1.0-project-source-freeze"
    ):
        raise FreezeError("unsupported source-freeze identity")
    created_at = manifest["created_at"]
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise FreezeError("source-freeze timestamp is not UTC")
    try:
        dt.datetime.fromisoformat(created_at.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise FreezeError("source-freeze timestamp is invalid") from error
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise FreezeError("source-freeze manifest has no files")
    if manifest["file_count"] != len(files):
        raise FreezeError("source-freeze file count differs")
    paths: list[str] = []
    for index, entry in enumerate(files):
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "mode",
            "size",
            "sha256",
        }:
            raise FreezeError(f"source-freeze file entry {index} is invalid")
        path = entry["path"]
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or "\\" in path
            or Path(path).as_posix() != path
            or ".." in Path(path).parts
        ):
            raise FreezeError(f"source-freeze path is unsafe: {path!r}")
        if not isinstance(entry["mode"], str) or not re.fullmatch(
            r"0[0-7]{3}", entry["mode"]
        ):
            raise FreezeError(f"source-freeze mode is invalid: {path}")
        if not isinstance(entry["size"], int) or entry["size"] < 0:
            raise FreezeError(f"source-freeze size is invalid: {path}")
        if not isinstance(entry["sha256"], str) or not SHA256.fullmatch(
            entry["sha256"]
        ):
            raise FreezeError(f"source-freeze hash is invalid: {path}")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise FreezeError("source-freeze paths are not unique and sorted")
    if not isinstance(manifest["aggregate_sha256"], str) or not SHA256.fullmatch(
        manifest["aggregate_sha256"]
    ):
        raise FreezeError("source-freeze aggregate hash is invalid")
    if manifest["aggregate_sha256"] != aggregate(files):
        raise FreezeError("source-freeze aggregate hash does not match its inventory")
    return manifest


def verify(root: Path, manifest_path: Path) -> dict[str, Any]:
    root = canonical_workspace(root)
    manifest = validate_manifest_shape(load_json(manifest_path))
    current = build_manifest(root, manifest["created_at"])
    for key in (
        "upstream",
        "semantic_port_count",
        "file_count",
        "aggregate_sha256",
        "files",
    ):
        if manifest[key] != current[key]:
            raise FreezeError(f"current project source differs at {key}")
    return manifest


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", type=Path)
    action.add_argument("--manifest", type=Path)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    args = parser.parse_args()
    root = canonical_workspace(args.workspace)
    try:
        if args.create:
            output = args.create.resolve()
            for directory in SOURCE_DIRECTORIES:
                try:
                    output.relative_to(root / directory)
                except ValueError:
                    continue
                raise FreezeError("source-freeze manifest cannot be written inside its scope")
            created_at = (
                dt.datetime.now(dt.timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            manifest = build_manifest(root, created_at)
            atomic_write(output, manifest)
            print(f"Navis source freeze created: {output}")
        else:
            manifest = verify(root, args.manifest.resolve())
            print(f"Navis source freeze verified: {args.manifest.resolve()}")
        print(
            f"- files: {manifest['file_count']}\n"
            f"- aggregate SHA-256: {manifest['aggregate_sha256']}\n"
            f"- Gecko commit: {manifest['upstream'].get('commit')}\n"
            f"- semantic ports: {manifest['semantic_port_count']}"
        )
        return 0
    except FreezeError as error:
        print(f"Navis source-freeze verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
