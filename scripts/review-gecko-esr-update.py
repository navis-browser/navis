#!/usr/bin/env python3

"""Create a deterministic, path-complete Gecko ESR semantic-review dossier."""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath


WORKSPACE = Path(__file__).resolve().parent.parent
DEFAULT_PIN = WORKSPACE / "vendor/gecko.json"
DEFAULT_PORTS = WORKSPACE / "config/gecko-semantic-ports.json"
DEFAULT_ROUTES = WORKSPACE / "config/gecko-esr-review-routes.json"


class ReviewError(RuntimeError):
    """The requested comparison cannot support its claimed review coverage."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=WORKSPACE / "gecko")
    parser.add_argument("--from-pin", type=Path, default=DEFAULT_PIN)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--to-pin", type=Path)
    target.add_argument("--to-ref")
    parser.add_argument("--to-label")
    parser.add_argument("--semantic-ports", type=Path, default=DEFAULT_PORTS)
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-incomplete-history",
        action="store_true",
        help="emit a tree-complete dossier that explicitly marks commit history incomplete",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="record counts and inventory hashes without individual paths or commits",
    )
    parser.add_argument(
        "--include-commit-paths",
        action="store_true",
        help="include every per-commit path in addition to the complete tree delta",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.summary_only and args.include_commit_paths:
        parser.error("--summary-only cannot be combined with --include-commit-paths")
    return args


def strict_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ReviewError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> dict:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReviewError(f"cannot read JSON metadata {path}: {error}") from error
    if not isinstance(value, dict):
        raise ReviewError(f"expected a JSON object in {path}")
    return value


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReviewError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


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
        raise ReviewError(f"unsafe {description}: {value!r}")
    return path


def run_git(
    repo: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment.update({"LC_ALL": "C", "TZ": "UTC"})
    command = ["git", "-C", os.fspath(repo), *arguments]
    try:
        result = subprocess.run(
            command,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
    except OSError as error:
        raise ReviewError(f"cannot execute git: {error}") from error
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise ReviewError(f"git command failed: {' '.join(command)}{suffix}")
    return result


def decode_git(value: bytes) -> str:
    return value.decode("utf-8", errors="surrogateescape")


def resolve_commit(repo: Path, reference: str) -> str:
    result = run_git(repo, "rev-parse", "--verify", f"{reference}^{{commit}}")
    commit = decode_git(result.stdout).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReviewError(f"reference did not resolve to a full commit: {reference!r}")
    return commit


def validate_pin(path: Path) -> dict:
    pin = load_json(path)
    required = {"remote", "tag", "build_tag", "commit"}
    if set(pin) != required:
        raise ReviewError(
            f"{path} must contain exactly: {', '.join(sorted(required))}"
        )
    if not all(isinstance(pin[key], str) and pin[key] for key in required):
        raise ReviewError(f"{path} contains an empty or non-string pin value")
    if not re.fullmatch(r"[0-9a-f]{40}", pin["commit"]):
        raise ReviewError(f"invalid commit in {path}: {pin['commit']!r}")
    return pin


def endpoint_metadata(repo: Path, commit: str, label: str, reference: str) -> dict:
    result = run_git(
        repo,
        "show",
        "-s",
        "--format=%H%x00%T%x00%aI%x00%cI%x00%s",
        commit,
    )
    fields = result.stdout.rstrip(b"\n").split(b"\x00")
    if len(fields) != 5:
        raise ReviewError(f"cannot parse endpoint metadata for {commit}")
    raw = run_git(repo, "cat-file", "-p", commit).stdout
    parents = [
        decode_git(line[7:])
        for line in raw.splitlines()
        if line.startswith(b"parent ")
    ]
    missing_parents = [
        parent
        for parent in parents
        if run_git(repo, "cat-file", "-e", f"{parent}^{{commit}}", check=False).returncode
        != 0
    ]
    return {
        "label": label,
        "reference": reference,
        "commit": decode_git(fields[0]),
        "tree": decode_git(fields[1]),
        "author_date": decode_git(fields[2]),
        "committer_date": decode_git(fields[3]),
        "subject": decode_git(fields[4]),
        "parents": parents,
        "missing_parent_objects": missing_parents,
    }


def load_semantic_ports(path: Path) -> tuple[list[dict], dict[str, list[str]]]:
    ledger = load_json(path)
    if ledger.get("schema_version") != 1:
        raise ReviewError(f"unsupported semantic-port schema in {path}")
    ports = ledger.get("ports")
    if not isinstance(ports, list) or not ports:
        raise ReviewError(f"semantic-port ledger is empty: {path}")
    summaries: list[dict] = []
    path_owners: dict[str, list[str]] = defaultdict(list)
    for order, port in enumerate(ports, 1):
        if not isinstance(port, dict) or port.get("order") != order:
            raise ReviewError(f"invalid semantic-port order at entry {order}")
        port_id = port.get("id")
        relative = port.get("patch")
        expected_hash = port.get("sha256")
        if not isinstance(port_id, str) or not port_id:
            raise ReviewError(f"semantic port {order} has no id")
        if not isinstance(relative, str):
            raise ReviewError(f"semantic port {port_id} has no patch path")
        safe_relative(relative, f"patch path for {port_id}")
        patch = WORKSPACE / relative
        if not patch.is_file() or patch.is_symlink():
            raise ReviewError(f"semantic-port patch is missing or unsafe: {patch}")
        actual_hash = sha256_file(patch)
        if actual_hash != expected_hash:
            raise ReviewError(f"semantic-port hash mismatch: {relative}")
        try:
            text = patch.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ReviewError(f"cannot read semantic-port patch {patch}: {error}") from error
        touched: set[str] = set()
        for source, destination in re.findall(
            r"^diff --git a/(.+?) b/(.+?)$", text, flags=re.MULTILINE
        ):
            if source != destination:
                raise ReviewError(f"semantic-port rename is unsupported: {relative}")
            safe_relative(source, f"path in {relative}")
            touched.add(source)
        if not touched:
            raise ReviewError(f"semantic-port patch has no diff entries: {relative}")
        for touched_path in sorted(touched):
            path_owners[touched_path].append(port_id)
        summaries.append(
            {
                "order": order,
                "id": port_id,
                "classification": port.get("classification"),
                "intent": port.get("intent"),
                "port_review": port.get("port_review"),
                "patch": relative,
                "sha256": actual_hash,
                "watched_paths": sorted(touched),
            }
        )
    return summaries, dict(path_owners)


def load_routes(path: Path) -> tuple[dict, list[dict]]:
    config = load_json(path)
    if set(config) != {
        "schema_version",
        "description",
        "default_route",
        "routes",
    } or config.get("schema_version") != 1:
        raise ReviewError(f"invalid ESR review-route schema in {path}")
    default_route = config["default_route"]
    routes = config["routes"]
    if not isinstance(default_route, str) or not default_route:
        raise ReviewError("ESR review default route is invalid")
    if not isinstance(routes, list) or not routes:
        raise ReviewError("ESR review-route list is empty")
    compiled: list[dict] = []
    seen: set[str] = {default_route}
    for route in routes:
        if not isinstance(route, dict) or set(route) != {
            "id",
            "description",
            "patterns",
        }:
            raise ReviewError("invalid ESR review-route entry")
        route_id = route["id"]
        patterns = route["patterns"]
        if not isinstance(route_id, str) or not route_id or route_id in seen:
            raise ReviewError(f"invalid or duplicate ESR review route: {route_id!r}")
        if not isinstance(patterns, list) or not patterns or not all(
            isinstance(pattern, str) for pattern in patterns
        ):
            raise ReviewError(f"ESR review route {route_id!r} has invalid patterns")
        try:
            compiled_patterns = [re.compile(pattern) for pattern in patterns]
        except re.error as error:
            raise ReviewError(f"invalid pattern in route {route_id}: {error}") from error
        seen.add(route_id)
        compiled.append({**route, "compiled_patterns": compiled_patterns})
    public = {
        "description": config["description"],
        "default_route": default_route,
        "routes": [
            {key: route[key] for key in ("id", "description", "patterns")}
            for route in routes
        ],
    }
    return public, compiled


def classify_path(
    path: str,
    routes: list[dict],
    default_route: str,
    port_owners: dict[str, list[str]],
) -> tuple[list[str], list[str]]:
    matched = [
        route["id"]
        for route in routes
        if any(pattern.search(path) for pattern in route["compiled_patterns"])
    ]
    return matched or [default_route], port_owners.get(path, [])


def parse_name_status(content: bytes) -> list[tuple[str, str]]:
    fields = content.split(b"\x00")
    while fields and not fields[-1]:
        fields.pop()
    if len(fields) % 2:
        raise ReviewError("cannot parse Git name-status output")
    changes: list[tuple[str, str]] = []
    for index in range(0, len(fields), 2):
        status = decode_git(fields[index]).lstrip("\n")
        path = decode_git(fields[index + 1])
        if not re.fullmatch(r"[ACDMTUXB]", status):
            raise ReviewError(f"unexpected Git path status: {status!r}")
        safe_relative(path, "path reported by Git")
        changes.append((status, path))
    return changes


def inventory_hash(changes: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for status, path in changes:
        digest.update(status.encode("ascii"))
        digest.update(b"\x00")
        digest.update(path.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\n")
    return digest.hexdigest()


def describe_changes(
    changes: list[tuple[str, str]],
    routes: list[dict],
    default_route: str,
    port_owners: dict[str, list[str]],
    include_paths: bool,
) -> dict:
    status_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    port_counts: Counter[str] = Counter()
    port_paths: list[dict] = []
    entries: list[dict] = []
    for status, path in changes:
        matched_routes, ports = classify_path(
            path, routes, default_route, port_owners
        )
        status_counts[status] += 1
        route_counts.update(matched_routes)
        port_counts.update(ports)
        if ports:
            port_paths.append({"status": status, "path": path, "ports": ports})
        if include_paths:
            entries.append(
                {
                    "status": status,
                    "path": path,
                    "review_routes": matched_routes,
                    "semantic_ports": ports,
                }
            )
    result = {
        "path_count": len(changes),
        "inventory_sha256": inventory_hash(changes),
        "status_counts": dict(sorted(status_counts.items())),
        "review_route_counts": dict(sorted(route_counts.items())),
        "semantic_port_counts": dict(sorted(port_counts.items())),
        "semantic_port_changed_paths": port_paths,
    }
    if include_paths:
        result["paths"] = entries
    return result


def parse_commit_log(
    content: bytes,
    routes: list[dict],
    default_route: str,
    port_owners: dict[str, list[str]],
    include_commit_paths: bool,
) -> list[dict]:
    records: list[dict] = []
    for chunk in content.split(b"\x1e"):
        if not chunk:
            continue
        fields = chunk.split(b"\x00", 5)
        if len(fields) != 6:
            raise ReviewError("cannot parse Git commit log header")
        commit, parents, author_date, committer_date, subject, remainder = fields
        remainder = remainder.lstrip(b"\x00\n")
        changes = parse_name_status(remainder) if remainder else []
        description = describe_changes(
            changes,
            routes,
            default_route,
            port_owners,
            include_commit_paths,
        )
        if include_commit_paths:
            per_commit_paths = description.pop("paths")
            description["paths"] = per_commit_paths
        records.append(
            {
                "commit": decode_git(commit),
                "parents": decode_git(parents).split(),
                "author_date": decode_git(author_date),
                "committer_date": decode_git(committer_date),
                "subject": decode_git(subject),
                "changes": description,
            }
        )
    return records


def revision_list(repo: Path, range_spec: str) -> list[str]:
    if not range_spec:
        return []
    output = run_git(
        repo, "rev-list", "--reverse", "--topo-order", range_spec
    ).stdout
    return [decode_git(line) for line in output.splitlines() if line]


def commit_side(
    repo: Path,
    range_spec: str,
    expected_hashes: list[str],
    routes: list[dict],
    default_route: str,
    port_owners: dict[str, list[str]],
    summary_only: bool,
    include_commit_paths: bool,
) -> dict:
    digest = hashlib.sha256()
    for commit in expected_hashes:
        digest.update(commit.encode("ascii"))
        digest.update(b"\n")
    result: dict = {
        "commit_count": len(expected_hashes),
        "commit_inventory_sha256": digest.hexdigest(),
    }
    if summary_only or not expected_hashes:
        if not summary_only:
            result["commits"] = []
        return result
    log = run_git(
        repo,
        "log",
        "--reverse",
        "--topo-order",
        "--no-renames",
        "--format=%x1e%H%x00%P%x00%aI%x00%cI%x00%s%x00",
        "--name-status",
        "-z",
        range_spec,
    ).stdout
    commits = parse_commit_log(
        log, routes, default_route, port_owners, include_commit_paths
    )
    actual_hashes = [record["commit"] for record in commits]
    if actual_hashes != expected_hashes:
        raise ReviewError("Git log inventory differs from Git rev-list inventory")
    result["commits"] = commits
    return result


def read_shallow_commits(repo: Path) -> set[str]:
    result = run_git(repo, "rev-parse", "--git-path", "shallow")
    value = decode_git(result.stdout).strip()
    path = Path(value)
    if not path.is_absolute():
        path = repo / path
    try:
        return {
            line.strip()
            for line in path.read_text(encoding="ascii").splitlines()
            if line.strip()
        }
    except FileNotFoundError:
        return set()
    except OSError as error:
        raise ReviewError(f"cannot read Git shallow boundary {path}: {error}") from error


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = run_git(
        repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False
    )
    if result.returncode not in (0, 1):
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ReviewError(f"cannot inspect Git ancestry: {detail}")
    return result.returncode == 0


def build_history(
    repo: Path,
    base: str,
    target: str,
    base_endpoint: dict,
    target_endpoint: dict,
    routes: list[dict],
    default_route: str,
    port_owners: dict[str, list[str]],
    summary_only: bool,
    include_commit_paths: bool,
) -> tuple[dict, bool]:
    merge = run_git(repo, "merge-base", base, target, check=False)
    merge_base = decode_git(merge.stdout).strip() if merge.returncode == 0 else None
    reasons: list[str] = []
    if not merge_base:
        reasons.append("no merge base is available in the local object graph")
        if base_endpoint["missing_parent_objects"]:
            reasons.append("the base endpoint has missing parent objects")
        if target_endpoint["missing_parent_objects"]:
            reasons.append("the target endpoint has missing parent objects")
        return (
            {
                "status": "incomplete",
                "topology": "unknown",
                "merge_base": None,
                "incomplete_reasons": reasons,
                "base_side": {
                    "commit_count": None,
                    "commit_inventory_sha256": None,
                },
                "target_side": {
                    "commit_count": None,
                    "commit_inventory_sha256": None,
                },
            },
            False,
        )

    if merge_base == base:
        topology = "base-ancestor-of-target"
    elif merge_base == target:
        topology = "target-ancestor-of-base"
    elif is_ancestor(repo, merge_base, base) and is_ancestor(repo, merge_base, target):
        topology = "diverged-from-common-ancestor"
    else:
        topology = "unknown"
        reasons.append("the available merge base does not cover both endpoints")

    base_range = "" if merge_base == base else f"{merge_base}..{base}"
    target_range = "" if merge_base == target else f"{merge_base}..{target}"
    base_hashes = revision_list(repo, base_range)
    target_hashes = revision_list(repo, target_range)
    shallow = read_shallow_commits(repo)
    shallow_in_range = sorted(shallow & set(base_hashes + target_hashes))
    if shallow_in_range:
        reasons.append(
            "shallow boundary commits occur inside the required history range: "
            + ", ".join(shallow_in_range)
        )
    complete = not reasons
    return (
        {
            "status": "complete" if complete else "incomplete",
            "topology": topology,
            "merge_base": merge_base,
            "incomplete_reasons": reasons,
            "base_side": commit_side(
                repo,
                base_range,
                base_hashes,
                routes,
                default_route,
                port_owners,
                summary_only,
                include_commit_paths,
            ),
            "target_side": commit_side(
                repo,
                target_range,
                target_hashes,
                routes,
                default_route,
                port_owners,
                summary_only,
                include_commit_paths,
            ),
        },
        complete,
    )


def write_report(path: Path, content: bytes, force: bool) -> None:
    path = path.resolve()
    if path.suffix not in {".json", ".xz"}:
        raise ReviewError("review output must end in .json or .json.xz")
    if path.suffix == ".xz" and not path.name.endswith(".json.xz"):
        raise ReviewError("compressed review output must end in .json.xz")
    if path.exists() and not force:
        raise ReviewError(f"refusing to overwrite review dossier: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if path.suffix == ".xz":
                with lzma.LZMAFile(
                    stream,
                    mode="w",
                    format=lzma.FORMAT_XZ,
                    check=lzma.CHECK_CRC64,
                    preset=6,
                ) as compressed:
                    compressed.write(content)
            else:
                stream.write(content)
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    if not (repo / ".git").exists():
        raise ReviewError(f"not a Git worktree: {repo}")

    base_pin = validate_pin(args.from_pin)
    base = resolve_commit(repo, base_pin["commit"])
    if resolve_commit(repo, base_pin["tag"]) != base:
        raise ReviewError("base pin tag and commit resolve to different objects")

    if args.to_pin:
        target_pin = validate_pin(args.to_pin)
        target_reference = target_pin["tag"]
        target = resolve_commit(repo, target_pin["commit"])
        if resolve_commit(repo, target_reference) != target:
            raise ReviewError("target pin tag and commit resolve to different objects")
        target_label = args.to_label or target_pin["tag"]
    else:
        target_pin = None
        target_reference = args.to_ref
        target = resolve_commit(repo, target_reference)
        target_label = args.to_label or target_reference
    if base == target:
        raise ReviewError("base and target resolve to the same Gecko commit")

    port_summaries, port_owners = load_semantic_ports(args.semantic_ports)
    public_routes, compiled_routes = load_routes(args.routes)
    default_route = public_routes["default_route"]
    base_endpoint = endpoint_metadata(
        repo, base, base_pin["tag"], base_pin["tag"]
    )
    target_endpoint = endpoint_metadata(
        repo, target, target_label, target_reference
    )
    history, complete_history = build_history(
        repo,
        base,
        target,
        base_endpoint,
        target_endpoint,
        compiled_routes,
        default_route,
        port_owners,
        args.summary_only,
        args.include_commit_paths,
    )
    if not complete_history and not args.allow_incomplete_history:
        reasons = "; ".join(history["incomplete_reasons"])
        raise ReviewError(
            "complete commit coverage is unavailable; fetch the required ancestry "
            f"or pass --allow-incomplete-history: {reasons}"
        )

    tree_changes = parse_name_status(
        run_git(
            repo,
            "diff",
            "--name-status",
            "-z",
            "--no-renames",
            base,
            target,
            "--",
        ).stdout
    )
    tree_delta = describe_changes(
        tree_changes,
        compiled_routes,
        default_route,
        port_owners,
        not args.summary_only,
    )
    changed_by_port = {
        port["id"]: [
            entry
            for entry in tree_delta["semantic_port_changed_paths"]
            if port["id"] in entry["ports"]
        ]
        for port in port_summaries
    }
    for port in port_summaries:
        port["changed_paths"] = changed_by_port[port["id"]]
        port["unchanged_watched_paths"] = sorted(
            set(port["watched_paths"])
            - {entry["path"] for entry in port["changed_paths"]}
        )

    origin = run_git(repo, "remote", "get-url", "origin", check=False)
    report = {
        "schema_version": 1,
        "tool": "review-gecko-esr-update.py",
        "coverage": {
            "tree_delta": "complete",
            "commit_history": history["status"],
            "summary_only": args.summary_only,
            "per_commit_paths_included": args.include_commit_paths,
            "claim": (
                "Every base-to-target tree path is inventoried. Commit coverage is "
                + ("complete." if complete_history else "explicitly incomplete.")
            ),
        },
        "repository": {
            "origin": (
                decode_git(origin.stdout).strip() if origin.returncode == 0 else None
            ),
            "is_shallow": bool(read_shallow_commits(repo)),
        },
        "inputs": {
            "base_pin": base_pin,
            "target_pin": target_pin,
            "semantic_port_ledger_sha256": sha256_file(args.semantic_ports),
            "review_routes_sha256": sha256_file(args.routes),
        },
        "base": base_endpoint,
        "target": target_endpoint,
        "history": history,
        "tree_delta": tree_delta,
        "semantic_ports": port_summaries,
        "review_routing": public_routes,
    }
    content = (
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")
    write_report(args.output, content, args.force)
    print(
        "Gecko ESR review dossier written: "
        f"{tree_delta['path_count']} tree paths, "
        f"history={history['status']}, "
        f"semantic-port paths={len(tree_delta['semantic_port_changed_paths'])}"
    )
    print(f"SHA-256 {sha256_file(args.output)}  {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReviewError as error:
        print(f"Gecko ESR review failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
