#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Verify that every configured native source belongs to a declared domain."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    workspace = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gecko", type=Path, required=True)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument(
        "--domains",
        type=Path,
        default=workspace / "config/runtime-source-domains.json",
    )
    parser.add_argument(
        "--explain-domain",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="include matched entries for a source domain (repeatable)",
    )
    parser.add_argument(
        "--explain-references",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="scan configured C-family sources for a domain's declared API markers",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def normalize_file(
    path: Path,
    gecko: Path,
    objdir: Path,
    external_roots: tuple[tuple[str, Path], ...] = (),
) -> str | None:
    absolute = lexical_absolute(path)
    try:
        return "@generated/" + absolute.relative_to(objdir).as_posix()
    except ValueError:
        pass
    try:
        relative = absolute.relative_to(gecko)
        if relative.parts and relative.parts[0].startswith("obj-"):
            return "@foreign-generated/" + relative.as_posix()
        return relative.as_posix()
    except ValueError:
        pass
    for prefix, root in external_roots:
        try:
            relative = absolute.relative_to(root)
        except ValueError:
            continue
        return f"{prefix}/{relative.as_posix()}"
    return None


def load_backend_entries(objdir: Path) -> dict[str, Path | None]:
    path = objdir / "backend.RecursiveMakeBackend"
    if not path.is_file():
        raise FileNotFoundError(f"RecursiveMake backend is missing: {path}")
    entries: dict[str, Path | None] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry:
            continue
        normalized = entry if "/" in entry else f"@backend-meta/{entry}"
        entries[normalized] = None
    return entries


def load_c_family_sources(
    gecko: Path, objdir: Path
) -> tuple[dict[str, Path], list[str]]:
    path = objdir / "clangd/compile_commands.json"
    if not path.is_file():
        raise FileNotFoundError(f"Clang compilation database is missing: {path}")
    database = json.loads(path.read_text(encoding="utf-8"))
    sources: dict[str, Path] = {}
    outside: list[str] = []
    for command in database:
        source = lexical_absolute(Path(command["file"]))
        normalized = normalize_file(source, gecko, objdir)
        if normalized is None:
            outside.append(source.as_posix())
            continue
        sources[normalized] = source
    return sources, sorted(set(outside))


def config_substitution(objdir: Path, name: str) -> str:
    json_status = objdir / "config.status.json"
    if json_status.is_file():
        value = json.loads(json_status.read_text(encoding="utf-8"))["substs"].get(name)
        if isinstance(value, str):
            return value

    status = objdir / "config.status"
    tree = ast.parse(status.read_text(encoding="utf-8"), filename=str(status))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != "substs":
            continue
        if not isinstance(node.value, ast.Dict):
            break
        for key_node, value_node in zip(node.value.keys, node.value.values):
            try:
                key = ast.literal_eval(key_node)
                value = ast.literal_eval(value_node)
            except (TypeError, ValueError):
                continue
            if key == name and isinstance(value, str):
                return value
    raise ValueError(f"config substitution {name!r} is missing from {objdir}")


def rust_dependency_file(objdir: Path) -> Path:
    target = config_substitution(objdir, "RUST_TARGET")
    os_target = config_substitution(objdir, "OS_TARGET")
    filename = "gkrust.d" if os_target == "WINNT" else "libgkrust.d"
    path = objdir / target / "release" / filename
    if not path.is_file():
        raise FileNotFoundError(f"linked gkrust dep-info is missing: {path}")
    return path


def load_rust_sources(
    gecko: Path, objdir: Path
) -> tuple[dict[str, Path], list[str], str]:
    dep_info = rust_dependency_file(objdir)
    external_roots: tuple[tuple[str, Path], ...] = ()
    if config_substitution(objdir, "OS_TARGET") == "WINNT":
        windows_rs = lexical_absolute(
            Path(config_substitution(objdir, "MOZ_WINDOWS_RS_DIR"))
        )
        external_roots = (("@external/windows-rs", windows_rs),)
    sources: dict[str, Path] = {}
    outside: list[str] = []
    content = dep_info.read_text(errors="replace").replace("\\\n", " ")
    for token in content.split():
        token = token.rstrip(":")
        if token.startswith("env-dep:") or not token.endswith(".rs"):
            continue
        source = Path(token)
        if not source.is_absolute():
            source = gecko / source
        source = lexical_absolute(source)
        normalized = normalize_file(source, gecko, objdir, external_roots)
        if normalized is None:
            outside.append(source.as_posix())
            continue
        sources[normalized] = source
    return sources, sorted(set(outside)), dep_info.as_posix()


def compile_manifest(manifest: dict) -> tuple[list[dict], list[re.Pattern[str]]]:
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported source-domain manifest schema")
    domains = manifest.get("domains")
    if not isinstance(domains, list) or not domains:
        raise ValueError("source-domain manifest has no domains")

    seen: set[str] = set()
    compiled: list[dict] = []
    for domain in domains:
        domain_id = domain.get("id")
        patterns = domain.get("patterns")
        if not isinstance(domain_id, str) or domain_id in seen:
            raise ValueError(f"invalid or duplicate source domain: {domain_id!r}")
        if not isinstance(patterns, list) or not patterns:
            raise ValueError(f"source domain {domain_id!r} has no patterns")
        seen.add(domain_id)
        item = dict(domain)
        item["compiled_patterns"] = [re.compile(pattern) for pattern in patterns]
        requires_config = domain.get("requires_config", {})
        if not isinstance(requires_config, dict) or not all(
            isinstance(name, str)
            and name
            and isinstance(expected, str)
            and expected
            for name, expected in requires_config.items()
        ):
            raise ValueError(
                f"source domain {domain_id!r} has invalid requires_config"
            )
        item["requires_config"] = dict(requires_config)
        reference_patterns = domain.get("reference_patterns", [])
        if not isinstance(reference_patterns, list) or not all(
            isinstance(pattern, str) for pattern in reference_patterns
        ):
            raise ValueError(
                f"source domain {domain_id!r} has invalid reference_patterns"
            )
        item["compiled_reference_pattern"] = (
            re.compile("|".join(f"(?:{pattern})" for pattern in reference_patterns))
            if reference_patterns
            else None
        )
        compiled.append(item)

    forbidden = manifest.get("forbidden_patterns", [])
    if not isinstance(forbidden, list):
        raise ValueError("forbidden_patterns must be an array")
    return compiled, [re.compile(pattern) for pattern in forbidden]


def configured_domains(domains: list[dict], objdir: Path) -> list[dict]:
    substitutions: dict[str, str | None] = {}
    selected: list[dict] = []
    for domain in domains:
        requirements = domain["requires_config"]
        enabled = True
        for name, expected in requirements.items():
            if name not in substitutions:
                try:
                    substitutions[name] = config_substitution(objdir, name)
                except ValueError:
                    substitutions[name] = None
            if substitutions[name] != expected:
                enabled = False
        if enabled:
            selected.append(domain)
    return selected


def source_bytes(path: Path | None) -> int:
    if path is None or not path.is_file():
        return 0
    return path.stat().st_size


def selected_domain(entry: str, domains: list[dict]) -> dict | None:
    return next(
        (
            domain
            for domain in domains
            if any(pattern.search(entry) for pattern in domain["compiled_patterns"])
        ),
        None,
    )


def audit_lens(
    entries: dict[str, Path | None],
    domains: list[dict],
    forbidden_patterns: list[re.Pattern[str]],
    explain_domains: set[str] | None = None,
) -> dict:
    explain_domains = explain_domains or set()
    counts: dict[str, int] = defaultdict(int)
    byte_counts: dict[str, int] = defaultdict(int)
    explained_entries: dict[str, list[str]] = defaultdict(list)
    unclassified: list[str] = []
    forbidden: list[str] = []

    for entry, source in sorted(entries.items()):
        if any(pattern.search(entry) for pattern in forbidden_patterns):
            forbidden.append(entry)
        selected = selected_domain(entry, domains)
        if selected is None:
            unclassified.append(entry)
            continue
        domain_id = selected["id"]
        counts[domain_id] += 1
        byte_counts[domain_id] += source_bytes(source)
        if domain_id in explain_domains:
            explained_entries[domain_id].append(entry)

    domain_counts = {
        domain["id"]: {
            "classification": domain["classification"],
            "state": domain["state"],
            "count": counts.get(domain["id"], 0),
            "bytes": byte_counts.get(domain["id"], 0),
        }
        for domain in domains
        if counts.get(domain["id"], 0)
    }
    result = {
        "entry_count": len(entries),
        "classified_count": len(entries) - len(unclassified),
        "domain_counts": domain_counts,
        "unclassified": unclassified,
        "forbidden": forbidden,
    }
    if explain_domains:
        result["explained_entries"] = {
            domain_id: explained_entries.get(domain_id, [])
            for domain_id in sorted(explain_domains)
        }
    return result


def scan_domain_references(
    entries: dict[str, Path], domains: list[dict], requested_domains: set[str]
) -> dict:
    results: dict[str, dict] = {}
    domains_by_id = {domain["id"]: domain for domain in domains}
    for domain_id in sorted(requested_domains):
        domain = domains_by_id[domain_id]
        pattern = domain["compiled_reference_pattern"]
        if pattern is None:
            raise ValueError(
                f"source domain {domain_id!r} has no reference_patterns to scan"
            )

        matches: list[str] = []
        external_matches: list[str] = []
        for entry, source in sorted(entries.items()):
            if not source.is_file():
                continue
            content = source.read_text(encoding="utf-8", errors="replace")
            if not pattern.search(content):
                continue
            matches.append(entry)
            owner = selected_domain(entry, domains)
            if owner is None or owner["id"] != domain_id:
                external_matches.append(entry)

        results[domain_id] = {
            "reference_patterns": domain.get("reference_patterns", []),
            "configured_c_family_match_count": len(matches),
            "configured_c_family_matches": matches,
            "external_match_count": len(external_matches),
            "external_matches": external_matches,
        }
    return results


def main() -> int:
    args = parse_args()
    gecko = lexical_absolute(args.gecko)
    objdir = lexical_absolute(args.objdir)
    manifest = json.loads(args.domains.read_text(encoding="utf-8"))
    all_domains, forbidden_patterns = compile_manifest(manifest)
    domain_ids = {domain["id"] for domain in all_domains}
    explain_domains = set(args.explain_domain)
    reference_domains = set(args.explain_references)
    unknown_explain_domains = sorted(
        (explain_domains | reference_domains) - domain_ids
    )
    if unknown_explain_domains:
        raise ValueError(
            "unknown source domain(s) requested for explanation: "
            + ", ".join(unknown_explain_domains)
        )
    domains = configured_domains(all_domains, objdir)
    inactive_explain_domains = sorted(
        explain_domains - {domain["id"] for domain in domains}
    )
    if inactive_explain_domains:
        raise ValueError(
            "source domain(s) inactive for this build configuration: "
            + ", ".join(inactive_explain_domains)
        )

    c_sources, c_outside = load_c_family_sources(gecko, objdir)
    rust_sources, rust_outside, rust_depfile = load_rust_sources(gecko, objdir)
    report = {
        "gecko": gecko.as_posix(),
        "objdir": objdir.as_posix(),
        "rust_dependency_file": rust_depfile,
        "outside_source_files": sorted(set(c_outside + rust_outside)),
        "lenses": {
            "recursive_make_backend": audit_lens(
                load_backend_entries(objdir),
                domains,
                forbidden_patterns,
                explain_domains,
            ),
            "configured_c_family_sources": audit_lens(
                c_sources, domains, forbidden_patterns, explain_domains
            ),
            "linked_rust_sources": audit_lens(
                rust_sources, domains, forbidden_patterns, explain_domains
            ),
        },
    }
    if reference_domains:
        report["domain_reference_scans"] = scan_domain_references(
            c_sources, domains, reference_domains
        )

    violations = len(report["outside_source_files"])
    violations += sum(
        len(lens["unclassified"]) + len(lens["forbidden"])
        for lens in report["lenses"].values()
    )
    report["violation_count"] = violations

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Runtime source-domain audit: {objdir}")
        for name, lens in report["lenses"].items():
            print(
                f"  {name}: {lens['classified_count']}/{lens['entry_count']} "
                "classified"
            )
            review = [
                f"{domain_id}={values['count']}"
                for domain_id, values in lens["domain_counts"].items()
                if values["state"] in {
                    "candidate",
                    "decomposition-required",
                    "profile-dependent",
                    "review-required",
                }
            ]
            if review:
                print("    review: " + ", ".join(review))
            for domain_id, entries in lens.get("explained_entries", {}).items():
                print(f"    {domain_id} entries ({len(entries)}):")
                for entry in entries:
                    print(f"      {entry}")
        for domain_id, scan in report.get("domain_reference_scans", {}).items():
            print(
                f"  {domain_id} C-family API-marker references: "
                f"{scan['configured_c_family_match_count']} total, "
                f"{scan['external_match_count']} outside domain"
            )
            for entry in scan["external_matches"]:
                print(f"      {entry}")
        print(f"  violations: {violations}")

    if violations:
        if not args.json:
            for outside in report["outside_source_files"][:50]:
                print(f"FAIL: source outside Gecko/objdir: {outside}", file=sys.stderr)
            for name, lens in report["lenses"].items():
                for entry in lens["unclassified"][:50]:
                    print(f"FAIL: {name}: unclassified: {entry}", file=sys.stderr)
                for entry in lens["forbidden"][:50]:
                    print(f"FAIL: {name}: forbidden: {entry}", file=sys.stderr)
            if violations > 50:
                print(
                    "FAIL: additional violations omitted; use --json for the full report",
                    file=sys.stderr,
                )
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"Runtime source-domain audit failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
