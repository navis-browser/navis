#!/usr/bin/env python3

"""Verify Navis's exact signed Remote Settings source and package policy."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent.parent
POLICY_PATH = WORKSPACE / "config/navis-remote-settings-policy.json"
PORTS_PATH = WORKSPACE / "config/gecko-semantic-ports.json"
PRODUCT_CONFIG = WORKSPACE / "product/moz.configure"
PRODUCT_PREFS = WORKSPACE / "product/app/profile/navis.js"
PATCH_PATH = (
    WORKSPACE / "patches/gecko/0029-scope-desktop-embedder-remote-settings.patch"
)
OPTION_NAME = "--with-desktop-embedder-remote-settings-collections"
CONFIG_NAME = "MOZ_DESKTOP_EMBEDDER_REMOTE_SETTINGS_COLLECTIONS"
RUNTIME_PREF = "services.settings.desktop_embedder_allowed_collections"
SERVER_PREF = "services.settings.server"
PREVIEW_PREF = "services.settings.preview_enabled"
IDENTIFIER = re.compile(r"^[a-z0-9_-]+/[a-z0-9_-]+$")


class PolicyError(RuntimeError):
    """The declared policy and its implementation differ."""


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"cannot read {path}: {error}") from error
    if not isinstance(payload, dict):
        raise PolicyError(f"{path} is not a JSON object")
    return payload


def source_collection_inventory() -> set[str]:
    inventory: set[str] = set()
    for relative in (
        "gecko/services/settings/dumps",
        "gecko/services/settings/static-dumps",
    ):
        root = WORKSPACE / relative
        for path in root.glob("*/*.json"):
            inventory.add(f"{path.parent.name}/{path.stem}")
    return inventory


def validate_policy() -> tuple[dict[str, Any], list[str], set[str]]:
    policy = load_json(POLICY_PATH)
    expected_keys = {
        "schema_version",
        "product",
        "provider",
        "allowed_collections",
        "excluded_source_collections",
    }
    if set(policy) != expected_keys:
        raise PolicyError(
            "policy keys differ: "
            f"missing={sorted(expected_keys - set(policy))}, "
            f"extra={sorted(set(policy) - expected_keys)}"
        )
    if policy["schema_version"] != 1 or policy["product"] != "Navis 1.0":
        raise PolicyError("unsupported Remote Settings policy identity")

    provider = policy["provider"]
    provider_keys = {
        "production_server",
        "default_signer",
        "security_state_signer",
        "signature_algorithm",
    }
    if not isinstance(provider, dict) or set(provider) != provider_keys:
        raise PolicyError("provider metadata is incomplete")
    if provider["production_server"] != (
        "https://firefox.settings.services.mozilla.com/v1"
    ):
        raise PolicyError("the production Remote Settings endpoint changed")
    if provider["signature_algorithm"] != "p384ecdsa":
        raise PolicyError("the reviewed signature algorithm changed")

    entries = policy["allowed_collections"]
    if not isinstance(entries, list) or not entries:
        raise PolicyError("allowed_collections must be a nonempty array")
    entry_keys = {"identifier", "packaged_bootstrap", "signer", "purpose"}
    allowed: list[str] = []
    packaged: set[str] = set()
    known_signers = {
        provider["default_signer"],
        provider["security_state_signer"],
    }
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or set(entry) != entry_keys:
            raise PolicyError(f"allowed collection {index} has invalid fields")
        identifier = entry["identifier"]
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise PolicyError(f"invalid collection identifier: {identifier!r}")
        if not isinstance(entry["packaged_bootstrap"], bool):
            raise PolicyError(f"{identifier} packaged_bootstrap is not boolean")
        if entry["signer"] not in known_signers:
            raise PolicyError(f"{identifier} has an unknown signer")
        if not isinstance(entry["purpose"], str) or not entry["purpose"].strip():
            raise PolicyError(f"{identifier} has no purpose")
        allowed.append(identifier)
        if entry["packaged_bootstrap"]:
            packaged.add(identifier)
    if allowed != sorted(set(allowed)):
        raise PolicyError("allowed collections must be sorted and unique")

    inventory = source_collection_inventory()
    missing_bootstrap = sorted(packaged - inventory)
    if missing_bootstrap:
        raise PolicyError(f"bootstrap dumps are missing: {missing_bootstrap}")
    excluded = policy["excluded_source_collections"]
    if not isinstance(excluded, list) or excluded != sorted(set(excluded)):
        raise PolicyError("excluded source collections must be sorted and unique")
    expected_excluded = sorted(inventory - set(allowed))
    if excluded != expected_excluded:
        raise PolicyError(
            "excluded source inventory differs: "
            f"expected={expected_excluded}, declared={excluded}"
        )
    return policy, allowed, packaged


def require_single(pattern: str, content: str, description: str) -> str:
    matches = re.findall(pattern, content, flags=re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise PolicyError(f"expected one {description}, found {len(matches)}")
    return matches[0]


def verify_product_declaration(policy: dict[str, Any], allowed: list[str]) -> None:
    expected = ",".join(allowed)
    product_config = PRODUCT_CONFIG.read_text(encoding="utf-8")
    configured = require_single(
        rf'imply_option\(\s*"{re.escape(OPTION_NAME)}"\s*,\s*"([^"]+)"\s*,?\s*\)',
        product_config,
        "product configure allowlist",
    )
    if configured != expected:
        raise PolicyError("product configure allowlist differs from policy")

    prefs = PRODUCT_PREFS.read_text(encoding="utf-8")
    declared_pref = require_single(
        rf'pref\("{re.escape(RUNTIME_PREF)}",\s*"([^"]+)",\s*locked\);',
        prefs,
        "locked runtime allowlist",
    )
    if declared_pref != expected:
        raise PolicyError("runtime preference allowlist differs from policy")
    server = re.escape(policy["provider"]["production_server"])
    required_pref_patterns = (
        rf'pref\("{re.escape(SERVER_PREF)}",\s*"{server}",\s*locked\);',
        rf'pref\("{re.escape(PREVIEW_PREF)}",\s*false,\s*locked\);',
        rf'#ifndef ENABLE_TESTS[\s\S]+?pref\("{re.escape(RUNTIME_PREF)}"',
    )
    for pattern in required_pref_patterns:
        if not re.search(pattern, prefs):
            raise PolicyError(f"required product preference policy is absent: {pattern}")


def verify_semantic_port() -> None:
    ports = load_json(PORTS_PATH).get("ports")
    if not isinstance(ports, list):
        raise PolicyError("semantic port ledger has no ports array")
    matches = [
        port
        for port in ports
        if isinstance(port, dict)
        and port.get("id") == "desktop-embedder-remote-settings-allowlist"
    ]
    if len(matches) != 1 or matches[0].get("order") != 29:
        raise PolicyError("Remote Settings semantic port is missing or misordered")
    digest = hashlib.sha256(PATCH_PATH.read_bytes()).hexdigest()
    if matches[0].get("sha256") != digest:
        raise PolicyError("Remote Settings semantic port hash differs from ledger")

    patch = PATCH_PATH.read_text(encoding="utf-8")
    required_markers = (
        OPTION_NAME,
        CONFIG_NAME,
        "desktop_embedder_allowed_collections",
        "desktop_embedder_enforce_allowed_collections_in_tests",
        "Collection disabled by Desktop Embedder policy",
        "if (this._disabled)",
        "allowed_collections and identifier not in allowed_collections",
        "test_desktop_embedder_collection_policy.js",
        "A disallowed collection cannot read stale profile data",
    )
    for marker in required_markers:
        if marker not in patch:
            raise PolicyError(f"semantic port lacks required marker: {marker}")
    for path in (
        "services/settings/dumps/blocklists/moz.build",
        "services/settings/dumps/main/moz.build",
        "services/settings/dumps/security-state/moz.build",
        "services/settings/static-dumps/main/moz.build",
    ):
        if path not in patch:
            raise PolicyError(f"semantic port does not filter {path}")

    signatures = (
        WORKSPACE
        / "gecko/services/settings/test/unit/test_remote_settings_signatures.js"
    ).read_text(encoding="utf-8")
    for marker in (
        '"bad-signature"',
        'equal(error.name, "InvalidSignatureError")',
        "await clientEmpty.sync()",
    ):
        if marker not in signatures:
            raise PolicyError(f"upstream invalid-signature test lost marker: {marker}")


def read_substs(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "substs"
            for target in node.targets
        ):
            continue
        result: dict[str, Any] = {}
        for key_node, value_node in zip(node.value.keys, node.value.values):
            try:
                key = ast.literal_eval(key_node)
                value = ast.literal_eval(value_node)
            except (TypeError, ValueError):
                continue
            if isinstance(key, str):
                result[key] = value
        return result
    raise PolicyError(f"substs assignment is missing from {path}")


def verify_runtime(
    runtime: Path, policy: dict[str, Any], allowed: list[str], packaged: set[str]
) -> None:
    omni = runtime / "omni.ja"
    if not omni.is_file():
        raise PolicyError(f"runtime omni.ja is missing: {omni}")

    with zipfile.ZipFile(omni) as archive:
        names = set(archive.namelist())
        actual_dumps = {
            name.removeprefix("defaults/settings/").removesuffix(".json")
            for name in names
            if re.fullmatch(
                r"defaults/settings/(?:blocklists|main|security-state)/[^/]+\.json",
                name,
            )
        }
        if actual_dumps != packaged:
            raise PolicyError(
                "runtime bootstrap dump inventory differs: "
                f"expected={sorted(packaged)}, actual={sorted(actual_dumps)}"
            )

        last_modified = json.loads(
            archive.read("defaults/settings/last_modified.json")
        )
        dumps_root = WORKSPACE / "gecko/services/settings/dumps"
        expected_timestamps = {
            identifier
            for identifier in allowed
            if (dumps_root / f"{identifier}.json").is_file()
        }
        if set(last_modified) != expected_timestamps:
            raise PolicyError(
                "runtime last_modified inventory differs: "
                f"expected={sorted(expected_timestamps)}, "
                f"actual={sorted(last_modified)}"
            )

        expected_attachments = {
            "defaults/settings/blocklists/addons-bloomfilters/addons-mlbf.bin",
            "defaults/settings/blocklists/addons-bloomfilters/addons-mlbf.bin.meta.json",
            "defaults/settings/blocklists/addons-bloomfilters/softblocks-addons-mlbf.bin",
            "defaults/settings/blocklists/addons-bloomfilters/softblocks-addons-mlbf.bin.meta.json",
        }
        actual_attachments = {
            name
            for name in names
            if name.startswith("defaults/settings/")
            and name.count("/") >= 4
            and not name.endswith("/")
        }
        if actual_attachments != expected_attachments:
            raise PolicyError(
                "runtime Remote Settings attachment inventory differs: "
                f"expected={sorted(expected_attachments)}, "
                f"actual={sorted(actual_attachments)}"
            )

        packaged_prefs = archive.read("defaults/pref/navis.js").decode("utf-8")
        expected = ",".join(allowed)
        for marker in (
            f'pref("{RUNTIME_PREF}", "{expected}", locked);',
            f'pref("{SERVER_PREF}", "{policy["provider"]["production_server"]}", locked);',
            f'pref("{PREVIEW_PREF}", false, locked);',
        ):
            if marker not in packaged_prefs:
                raise PolicyError(f"runtime policy preference is missing: {marker}")

    config_status = runtime.parent.parent / "config.status"
    if config_status.is_file():
        configured = read_substs(config_status).get(CONFIG_NAME)
        if tuple(configured or ()) != tuple(allowed):
            raise PolicyError(
                f"configured collection allowlist differs: {configured!r}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()
    try:
        policy, allowed, packaged = validate_policy()
        verify_product_declaration(policy, allowed)
        verify_semantic_port()
        if args.runtime:
            verify_runtime(args.runtime.resolve(), policy, allowed, packaged)
    except (OSError, UnicodeError, zipfile.BadZipFile, PolicyError) as error:
        print(f"Remote Settings policy verification failed: {error}", file=sys.stderr)
        return 1

    suffix = f"; runtime={args.runtime.resolve()}" if args.runtime else ""
    print(
        "Navis Remote Settings policy verified: "
        f"{len(allowed)} allowed collections, {len(packaged)} bootstrap dumps"
        f"{suffix}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
