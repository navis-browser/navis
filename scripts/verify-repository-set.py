#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


"""Verify exact sibling repository revisions at a selected Navis version."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


NAVIS_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = NAVIS_ROOT / "config/navis-repository-set.json"
SHA1 = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_REPOSITORIES = {
    "navis": ("navis-browser/navis", "."),
    "runtime": ("navis-browser/runtime", "../runtime"),
    "platform": ("navis-browser/platform", "../platform"),
}
FORBIDDEN_TRACKED_PREFIXES = {
    "navis": ("artifacts/", "logs/", "work/", "gecko/"),
    "runtime": ("gecko/", "core/rust/target/", "obj"),
    "platform": ("android/.gradle/", "android/build/"),
}


class RepositorySetError(RuntimeError):
    """The repository-set manifest or checkout is incoherent."""


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RepositorySetError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RepositorySetError(f"cannot read repository-set manifest: {error}") from error
    if not isinstance(value, dict):
        raise RepositorySetError("repository-set manifest is not an object")
    return value


def git(repo: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RepositorySetError(
            f"git {' '.join(arguments)} failed in {repo}: {detail}"
        )
    return result.stdout.strip()


def safe_path(value: Any, expected: str, label: str) -> str:
    if not isinstance(value, str) or value != expected:
        raise RepositorySetError(f"{label} must be {expected!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or "\\" in value:
        raise RepositorySetError(f"{label} is not a safe relative path")
    return value


def verify_repository(
    name: str,
    entry: dict[str, Any],
    navis_root: Path,
    baseline_tag: str,
) -> dict[str, str]:
    expected_slug, expected_path = EXPECTED_REPOSITORIES[name]
    if entry.get("slug") != expected_slug:
        raise RepositorySetError(f"{name} repository slug differs")
    relative = safe_path(entry.get("path"), expected_path, f"{name}.path")
    repo = (navis_root / relative).resolve()
    if not (repo / ".git").exists():
        raise RepositorySetError(f"{name} is not an independent Git repository: {repo}")
    top = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo:
        raise RepositorySetError(f"{name} Git root differs: {top}")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RepositorySetError(f"{name} worktree is not clean")

    head = git(repo, "rev-parse", "HEAD")
    locked = entry.get("revision")
    if name == "navis":
        if locked is not None:
            raise RepositorySetError("navis may not self-lock its current commit")
    elif not isinstance(locked, str) or not SHA1.fullmatch(locked) or head != locked:
        raise RepositorySetError(f"{name} HEAD differs from locked revision")

    baseline = git(repo, "rev-parse", f"{baseline_tag}^{{}}")
    expected_baseline = entry.get("baseline_revision")
    # The baseline cannot store its own commit hash. Only that exact Navis
    # checkout may omit the self-reference; peers always remain hash-locked.
    baseline_self = name == "navis" and expected_baseline is None and baseline == head
    if not baseline_self and (
        not isinstance(expected_baseline, str)
        or not SHA1.fullmatch(expected_baseline)
        or baseline != expected_baseline
    ):
        raise RepositorySetError(f"{name} baseline tag differs from manifest")
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", baseline, head],
        check=False,
    )
    if ancestor.returncode != 0:
        raise RepositorySetError(f"{name} {baseline_tag} is not an ancestor of main")

    tracked = git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    forbidden = [
        path
        for path in tracked
        if any(path == prefix.rstrip("/") or path.startswith(prefix)
               for prefix in FORBIDDEN_TRACKED_PREFIXES[name])
    ]
    if forbidden:
        raise RepositorySetError(f"{name} tracks generated state: {forbidden[:5]}")
    modes = git(repo, "ls-tree", "-r", "HEAD").splitlines()
    if any(line.startswith("160000 ") for line in modes):
        raise RepositorySetError(f"{name} contains an unapproved Git submodule")
    return {"head": head, "baseline": baseline}


def verify(manifest_path: Path) -> dict[str, dict[str, str]]:
    manifest = load_manifest(manifest_path)
    expected_keys = {
        "schema",
        "product_version",
        "organization",
        "checkout_layout",
        "release_baseline",
        "repositories",
        "generated_state",
    }
    if set(manifest) != expected_keys:
        raise RepositorySetError("repository-set manifest keys differ")
    if manifest.get("schema") != "navis-repository-set-v1":
        raise RepositorySetError("unsupported repository-set schema")
    if manifest.get("product_version") not in {"0.1.0", "0.2.0-dev"}:
        raise RepositorySetError("repository-set product version differs")
    if manifest.get("organization") != "navis-browser":
        raise RepositorySetError("repository-set organization differs")
    if manifest.get("checkout_layout") != "sibling-directories":
        raise RepositorySetError("repository-set checkout layout differs")
    baseline_tag = manifest.get("release_baseline")
    if baseline_tag != "v0.1.0":
        raise RepositorySetError("repository-set baseline differs")
    repositories = manifest.get("repositories")
    if not isinstance(repositories, dict) or set(repositories) != set(
        EXPECTED_REPOSITORIES
    ):
        raise RepositorySetError("repository-set inventory differs")
    navis_root = manifest_path.resolve().parents[1]
    results = {
        name: verify_repository(name, repositories[name], navis_root, baseline_tag)
        for name in EXPECTED_REPOSITORIES
    }

    runtime = (navis_root / repositories["runtime"]["path"]).resolve()
    platform = (navis_root / repositories["platform"]["path"]).resolve()
    for repo, path, label in (
        (runtime, "android", "Runtime Android"),
        (platform, "android", "Platform Android"),
    ):
        if subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{baseline_tag}:{path}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0:
            raise RepositorySetError(f"{label} incorrectly exists at {baseline_tag}")
        present = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"HEAD:{path}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        if present != (manifest["product_version"] != "0.1.0"):
            raise RepositorySetError(f"{label} presence differs from selected product version")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    try:
        results = verify(args.manifest.expanduser().resolve())
    except RepositorySetError as error:
        print(f"Repository-set verification failed: {error}", file=sys.stderr)
        return 1
    print("[OK] Navis publication repository set is coherent")
    for name, result in results.items():
        print(
            f" - {name}: HEAD={result['head'][:12]} "
            f"v0.1.0={result['baseline'][:12]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
