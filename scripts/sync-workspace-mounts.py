#!/usr/bin/env python3

"""Check or create the manifest-owned pre-split compatibility mounts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


class MountError(RuntimeError):
    pass


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def expected_mounts(workspace: Path) -> dict[Path, Path]:
    manifest_path = workspace / "navis/config/navis-path-resolution.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        view = manifest["legacy_project_view"]
        raw = view["directory_mounts"] | view["file_mounts"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise MountError(f"cannot read mount contract: {error}") from error

    mounts: dict[Path, Path] = {}
    for mount_text, target_text in raw.items():
        if not isinstance(mount_text, str) or not isinstance(target_text, str):
            raise MountError("mount paths must be strings")
        mount_rel = Path(mount_text)
        target_rel = Path(target_text)
        if mount_rel.is_absolute() or target_rel.is_absolute() or ".." in (
            mount_rel.parts + target_rel.parts
        ):
            raise MountError(f"mount contract is not workspace-relative: {mount_text}")
        mount = workspace / mount_rel
        target = workspace / target_rel
        if not inside(mount.parent.resolve(), workspace):
            raise MountError(f"mount parent escapes workspace: {mount}")
        # Source-only checks need no Gecko download. Preparation followed by
        # this command installs the generated-tree mount when it exists.
        if target_text == "runtime/gecko" and not target.exists():
            continue
        if not target.exists() or target.is_symlink():
            raise MountError(f"canonical mount target is missing or indirect: {target}")
        mounts[mount] = target
    return mounts


def sync(workspace: Path, *, write: bool) -> int:
    failures: list[str] = []
    for mount, target in expected_mounts(workspace).items():
        relative_target = os.path.relpath(target, mount.parent)
        if mount.is_symlink() and os.readlink(mount) == relative_target:
            continue
        if not write:
            failures.append(f"{mount.relative_to(workspace)} -> {relative_target}")
            continue
        if mount.exists() and not mount.is_symlink():
            raise MountError(f"refusing to replace a real path: {mount}")
        if mount.is_symlink():
            mount.unlink()
        mount.parent.mkdir(parents=True, exist_ok=True)
        mount.symlink_to(relative_target)

    if failures:
        print("Workspace mounts differ from the manifest:", file=sys.stderr)
        for item in failures:
            print(f" - {item}", file=sys.stderr)
        return 1
    print(
        f"[OK] workspace compatibility mounts {'synchronized' if write else 'match'}"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    return sync(workspace, write=args.write)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MountError as error:
        print(f"sync-workspace-mounts: {error}", file=sys.stderr)
        raise SystemExit(1) from error
