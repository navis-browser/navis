#!/usr/bin/env python3

"""Prepare a pinned Gecko checkout for the product-neutral Desktop Embedder."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


class PrepareError(RuntimeError):
    """A reproducibility or source-boundary check failed."""


def parse_args() -> argparse.Namespace:
    default_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=default_root,
        help="unpacked Desktop Embedder source-package root",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        help=(
            "split-workspace Runtime root containing core/embedder/patches/vendor "
            "(defaults to SOURCE_ROOT for an unpacked source package)"
        ),
    )
    parser.add_argument(
        "--gecko",
        type=Path,
        help="Gecko checkout path (defaults to SOURCE_ROOT/gecko)",
    )
    parser.add_argument(
        "--no-clone",
        action="store_true",
        help="fail instead of cloning when the Gecko checkout is absent",
    )
    parser.add_argument(
        "--no-mount",
        action="store_true",
        help="apply/verify the semantic-port patches without mounting embedder/ or core/",
    )
    return parser.parse_args()


def strict_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise PrepareError(f"duplicate JSON key in source metadata: {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> dict:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PrepareError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise PrepareError(f"expected a JSON object in {path}")
    return value


def safe_relative(value: str, description: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != value
    ):
        raise PrepareError(f"unsafe {description}: {value!r}")
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_git(
    gecko: Path,
    *arguments: str,
    capture: bool = False,
    environment: dict[str, str] | None = None,
) -> str:
    command = ["git", "-C", os.fspath(gecko), *arguments]
    try:
        result = subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            env=environment,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise PrepareError(f"git command failed: {' '.join(command)}{suffix}") from error
    return result.stdout.strip() if capture else ""


def validate_pin(runtime_root: Path) -> dict:
    pin_path = runtime_root / "vendor/gecko.json"
    pin = load_json(pin_path)
    required = {"remote", "tag", "build_tag", "commit"}
    if set(pin) != required:
        raise PrepareError(
            f"{pin_path} must contain exactly: {', '.join(sorted(required))}"
        )
    if not all(isinstance(pin[key], str) and pin[key] for key in required):
        raise PrepareError(f"{pin_path} contains an empty or non-string pin value")
    commit = pin["commit"]
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise PrepareError(f"invalid Gecko commit in {pin_path}: {commit!r}")
    return pin


def validate_source_api(source_root: Path, runtime_root: Path) -> int:
    definition_path = source_root / "config/desktop-embedder-source-package.json"
    definition = load_json(definition_path)
    version = definition.get("source_api_version")
    if not isinstance(version, int) or version <= 0:
        raise PrepareError(f"invalid source API version in {definition_path}")

    module_path = runtime_root / "embedder/modules/DesktopEngine.sys.mjs"
    try:
        module = module_path.read_text(encoding="utf-8")
    except OSError as error:
        raise PrepareError(f"cannot read {module_path}: {error}") from error
    declared = re.findall(
        r"^export const DESKTOP_EMBEDDER_API_VERSION = ([0-9]+);$",
        module,
        flags=re.MULTILINE,
    )
    if declared != [str(version)]:
        raise PrepareError(
            f"public source API differs between {definition_path} and {module_path}"
        )
    return version


def validate_ports(source_root: Path, runtime_root: Path) -> list[tuple[Path, str]]:
    ledger_path = source_root / "config/gecko-semantic-ports.json"
    ledger = load_json(ledger_path)
    if ledger.get("schema_version") != 1:
        raise PrepareError(f"unsupported semantic-port schema in {ledger_path}")
    if ledger.get("upstream_pin") != "vendor/gecko.json":
        raise PrepareError(f"{ledger_path} does not select vendor/gecko.json")
    ports = ledger.get("ports")
    if not isinstance(ports, list) or not ports:
        raise PrepareError(f"{ledger_path} has no semantic ports")

    validated: list[tuple[Path, str]] = []
    seen_paths: set[str] = set()
    for expected_order, port in enumerate(ports, 1):
        if not isinstance(port, dict) or port.get("order") != expected_order:
            raise PrepareError(
                f"semantic-port order must be contiguous at entry {expected_order}"
            )
        relative = port.get("patch")
        expected_hash = port.get("sha256")
        if not isinstance(relative, str) or not relative.startswith("patches/gecko/"):
            raise PrepareError(f"invalid patch path at semantic port {expected_order}")
        safe_relative(relative, f"patch path at semantic port {expected_order}")
        if not isinstance(expected_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_hash
        ):
            raise PrepareError(f"invalid patch hash at semantic port {expected_order}")
        if relative in seen_paths:
            raise PrepareError(f"duplicate semantic-port patch: {relative}")
        seen_paths.add(relative)
        patch = runtime_root / relative
        if not patch.is_file() or patch.is_symlink():
            raise PrepareError(f"semantic-port patch is missing or unsafe: {patch}")
        actual_hash = sha256(patch)
        if actual_hash != expected_hash:
            raise PrepareError(
                f"semantic-port hash mismatch for {relative}: "
                f"expected {expected_hash}, found {actual_hash}"
            )
        try:
            patch_text = patch.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise PrepareError(f"cannot read semantic-port patch {patch}: {error}") from error
        touched_paths: set[str] = set()
        for source, destination in re.findall(
            r"^diff --git a/(.+?) b/(.+?)$", patch_text, flags=re.MULTILINE
        ):
            if source != destination:
                raise PrepareError(f"semantic-port patch rename is unsupported: {relative}")
            safe_relative(source, f"path in semantic-port patch {relative}")
            touched_paths.add(source)
        if not touched_paths:
            raise PrepareError(f"semantic-port patch contains no diff entries: {relative}")
        validated.append((patch, f"{actual_hash} {patch.name}"))

    actual_patches = {
        path.relative_to(runtime_root).as_posix()
        for path in (runtime_root / "patches/gecko").glob("*.patch")
    }
    if actual_patches != seen_paths:
        missing = sorted(seen_paths - actual_patches)
        extra = sorted(actual_patches - seen_paths)
        raise PrepareError(
            f"semantic-port patch inventory mismatch; missing={missing}, extra={extra}"
        )
    return validated


def clone_if_needed(gecko: Path, pin: dict, no_clone: bool) -> None:
    if (gecko / ".git").exists():
        return
    if no_clone:
        raise PrepareError(f"Gecko checkout does not exist: {gecko}")
    if gecko.exists():
        if not gecko.is_dir() or any(gecko.iterdir()):
            raise PrepareError(f"refusing to clone into nonempty path: {gecko}")
    gecko.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        pin["tag"],
        "--single-branch",
        pin["remote"],
        os.fspath(gecko),
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as error:
        raise PrepareError(f"Gecko clone failed with exit code {error.returncode}") from error


def read_state(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise PrepareError(f"cannot read patch state {path}: {error}") from error


def write_state(path: Path, lines: list[str]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def verify_applied_ports(gecko: Path, ports: list[tuple[Path, str]]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="desktop-embedder-index."
    )
    os.close(descriptor)
    index = Path(temporary_name)
    index.unlink()
    index_lock = Path(f"{index}.lock")
    environment = os.environ.copy()
    environment["GIT_INDEX_FILE"] = os.fspath(index)
    try:
        run_git(gecko, "read-tree", "HEAD", environment=environment)
        run_git(
            gecko,
            "apply",
            "--cached",
            *(os.fspath(patch) for patch, _ in ports),
            environment=environment,
        )
        expected_tree = run_git(
            gecko, "write-tree", capture=True, environment=environment
        )
        expected_paths = run_git(
            gecko,
            "diff",
            "--cached",
            "--name-only",
            "HEAD",
            "--",
            capture=True,
            environment=environment,
        ).splitlines()
        if not expected_paths:
            raise PrepareError("semantic-port series produces no tracked Gecko changes")
        actual_paths = run_git(
            gecko, "diff", "--name-only", "HEAD", "--", capture=True
        ).splitlines()
        extra_paths = sorted(set(actual_paths) - set(expected_paths))
        if extra_paths:
            raise PrepareError(
                "Gecko has tracked changes outside the semantic-port series: "
                f"{extra_paths}"
            )

        run_git(
            gecko,
            "read-tree",
            "--reset",
            "HEAD",
            environment=environment,
        )
        run_git(
            gecko,
            "add",
            "--all",
            "--",
            *expected_paths,
            environment=environment,
        )
        actual_tree = run_git(
            gecko, "write-tree", capture=True, environment=environment
        )
        if actual_tree != expected_tree:
            changed_paths = run_git(
                gecko, "diff-tree", "--no-commit-id", "--name-only", "-r",
                expected_tree, actual_tree, capture=True, environment=environment,
            )
            raise PrepareError(
                "Gecko tracked content differs from the pinned commit plus the "
                f"semantic-port series:\n{changed_paths}"
            )
    finally:
        for temporary in (index_lock, index):
            if temporary.exists():
                temporary.unlink()


def apply_ports(gecko: Path, ports: list[tuple[Path, str]]) -> None:
    state_path = gecko / ".desktop-embedder-patches-applied"
    legacy_state_path = gecko / ".navis-patches-applied"
    expected_state = [state for _, state in ports]

    if state_path.is_file():
        applied_state = read_state(state_path)
    elif legacy_state_path.is_file():
        applied_state = read_state(legacy_state_path)
    else:
        tracked_clean = True
        for arguments in (("diff", "--quiet"), ("diff", "--cached", "--quiet")):
            result = subprocess.run(
                ["git", "-C", os.fspath(gecko), *arguments], check=False
            )
            if result.returncode not in (0, 1):
                raise PrepareError("cannot inspect Gecko tracked-change state")
            tracked_clean = tracked_clean and result.returncode == 0
        if not tracked_clean:
            raise PrepareError(
                "Gecko has tracked changes but no Desktop Embedder patch-state file"
            )
        applied_state = []

    divergence = None
    if len(applied_state) > len(expected_state):
        divergence = len(expected_state) + 1
    else:
        for index, applied in enumerate(applied_state):
            if applied != expected_state[index]:
                divergence = index + 1
                break

    if divergence is not None:
        try:
            verify_applied_ports(gecko, ports)
        except PrepareError as error:
            raise PrepareError(
                "recorded Gecko patch state diverges before semantic port "
                f"{divergence}, and the checkout does not match the refreshed "
                f"series: {error}"
            ) from error
        applied_state = expected_state

    for patch, _ in ports[len(applied_state) :]:
        try:
            run_git(gecko, "apply", "--check", os.fspath(patch), capture=True)
        except PrepareError:
            # An appended series may already be generated but not recorded.
            # Accept it only after the complete tracked tree matches the ledger.
            verify_applied_ports(gecko, ports)
            break
        run_git(gecko, "apply", os.fspath(patch))
    else:
        verify_applied_ports(gecko, ports)
    if not state_path.is_file() or read_state(state_path) != expected_state:
        write_state(state_path, expected_state)


def mount_source_directory(
    source_root: Path, gecko: Path, source_name: str, destination_name: str
) -> None:
    source = (source_root / source_name).resolve()
    if not source.is_dir():
        raise PrepareError(f"required source directory is missing: {source}")
    destination = gecko / destination_name
    if destination.is_symlink():
        raw_target = os.readlink(destination)
        if not Path(raw_target).is_absolute() and destination.resolve() == source:
            return
        destination.unlink()
    elif destination.exists():
        raise PrepareError(f"mount destination exists and is not a symlink: {destination}")
    relative_source = os.path.relpath(source, destination.parent.resolve())
    destination.symlink_to(relative_source, target_is_directory=True)


def mount_sources(source_root: Path, gecko: Path) -> None:
    mount_source_directory(source_root, gecko, "embedder", "desktop-embedder")
    mount_source_directory(source_root, gecko, "core", "navis-core")


def main() -> int:
    args = parse_args()
    source_root = args.source_root.resolve()
    runtime_root = (args.runtime_root or source_root).resolve()
    gecko = (args.gecko or (runtime_root / "gecko")).resolve()
    source_api_version = validate_source_api(source_root, runtime_root)
    pin = validate_pin(runtime_root)
    ports = validate_ports(source_root, runtime_root)
    clone_if_needed(gecko, pin, args.no_clone)

    actual_commit = run_git(gecko, "rev-parse", "HEAD", capture=True)
    if actual_commit != pin["commit"]:
        raise PrepareError(
            f"Gecko revision mismatch: expected {pin['commit']}, found {actual_commit}"
        )
    apply_ports(gecko, ports)
    if not args.no_mount:
        mount_sources(runtime_root, gecko)

    mount_status = (
        "not mounted"
        if args.no_mount
        else "mounted as desktop-embedder/ and navis-core/"
    )
    print(
        f"Desktop Gecko Embedder source API {source_api_version} prepared against "
        f"{pin['tag']} "
        f"({actual_commit}); {len(ports)} semantic ports; {mount_status}."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PrepareError as error:
        print(f"Desktop Embedder preparation failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
