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
POLICY_PATH = WORKSPACE / "../platform/gecko-chrome/config/navis-remote-settings-policy.json"
PORTS_PATH = WORKSPACE / "../runtime/config/gecko-semantic-ports.json"
PRODUCT_CONFIG = WORKSPACE / "../platform/gecko-chrome/moz.configure"
PRODUCT_PREFS = WORKSPACE / "../platform/gecko-chrome/app/profile/navis.js"
ANDROID_MOZCONFIG = WORKSPACE / "../runtime/mozconfig.android-aarch64.sccache"
ANDROID_PREFS = WORKSPACE / "../runtime/gecko/mobile/android/app/geckoview-prefs.js"
TOOLKIT_CONFIG = WORKSPACE / "../runtime/gecko/toolkit/moz.configure"
APP_CONSTANTS = WORKSPACE / "../runtime/gecko/toolkit/modules/AppConstants.sys.mjs"
TOOLKIT_MODULES_BUILD = WORKSPACE / "../runtime/gecko/toolkit/modules/moz.build"
REMOTE_SETTINGS_RUNTIME = WORKSPACE / "../runtime/gecko/services/settings/remote-settings.sys.mjs"
PATCH_PATH = (
    WORKSPACE / "../runtime/patches/gecko/0029-scope-navis-remote-settings-policy.patch"
)
POLICY_RELATIVE_PATH = "navis/config/navis-remote-settings-policy.json"
OPTION_NAME = "--with-navis-remote-settings-policy"
CAPABILITY_NAME = "MOZ_NAVIS_REMOTE_SETTINGS_POLICY"
CONFIG_NAME = "MOZ_NAVIS_REMOTE_SETTINGS_COLLECTIONS"
BOOTSTRAP_CONFIG_NAME = "MOZ_NAVIS_REMOTE_SETTINGS_BOOTSTRAP_COLLECTIONS"
CSV_CONFIG_NAME = "MOZ_NAVIS_REMOTE_SETTINGS_COLLECTIONS_CSV"
SYNC_CONFIG_NAME = "MOZ_NAVIS_REMOTE_SETTINGS_SYNC_COLLECTIONS"
SYNC_CSV_CONFIG_NAME = "MOZ_NAVIS_REMOTE_SETTINGS_SYNC_COLLECTIONS_CSV"
TEST_RUNTIME_PREF = "services.settings.navis_allowed_collections"
TEST_SYNC_PREF = "services.settings.navis_sync_collections"
SERVER_PREF = "services.settings.server"
PREVIEW_PREF = "services.settings.preview_enabled"
IDENTIFIER = re.compile(r"^[a-z0-9_-]+/[a-z0-9_-]+$")


def has_enabled_app_constant(source: str, name: str) -> bool:
    return (
        re.search(
            rf"(?m)^[ \t]*{re.escape(name)}:[ \t]*\r?$\n"
            rf"(?:[ \t]*//@line[^\r\n]*\r?$\n)*"
            rf"[ \t]*true,",
            source,
        )
        is not None
    )


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
        "../runtime/gecko/services/settings/dumps",
        "../runtime/gecko/services/settings/static-dumps",
    ):
        root = WORKSPACE / relative
        for path in root.glob("*/*.json"):
            inventory.add(f"{path.parent.name}/{path.stem}")
    return inventory


def validate_policy() -> tuple[dict[str, Any], list[str], set[str], set[str]]:
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
    if policy["schema_version"] != 2 or policy["product"] != "Navis 1.0":
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
    entry_keys = {
        "identifier",
        "packaged_bootstrap",
        "remote_sync",
        "signer",
        "purpose",
    }
    allowed: list[str] = []
    packaged: set[str] = set()
    syncable: set[str] = set()
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
        if not isinstance(entry["remote_sync"], bool):
            raise PolicyError(f"{identifier} remote_sync is not boolean")
        if not entry["remote_sync"] and not entry["packaged_bootstrap"]:
            raise PolicyError(
                f"{identifier} disables remote sync without a packaged bootstrap"
            )
        if entry["signer"] not in known_signers:
            raise PolicyError(f"{identifier} has an unknown signer")
        if not isinstance(entry["purpose"], str) or not entry["purpose"].strip():
            raise PolicyError(f"{identifier} has no purpose")
        allowed.append(identifier)
        if entry["packaged_bootstrap"]:
            packaged.add(identifier)
        if entry["remote_sync"]:
            syncable.add(identifier)
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
    return policy, allowed, packaged, syncable


def require_single(pattern: str, content: str, description: str) -> str:
    matches = re.findall(pattern, content, flags=re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise PolicyError(f"expected one {description}, found {len(matches)}")
    return matches[0]


def verify_product_declaration(
    policy: dict[str, Any], allowed: list[str], syncable: set[str]
) -> None:
    expected = ",".join(allowed)
    product_config = PRODUCT_CONFIG.read_text(encoding="utf-8")
    configured = require_single(
        rf'imply_option\(\s*"{re.escape(OPTION_NAME)}"\s*,\s*"([^"]+)"\s*,?\s*\)',
        product_config,
        "product policy path",
    )
    if configured != POLICY_RELATIVE_PATH:
        raise PolicyError("desktop product does not consume the owned policy file")

    android_mozconfig = ANDROID_MOZCONFIG.read_text(encoding="utf-8")
    expected_android_option = f"ac_add_options {OPTION_NAME}={POLICY_RELATIVE_PATH}"
    if android_mozconfig.count(expected_android_option) != 1:
        raise PolicyError("Android product does not consume the owned policy file")

    prefs = PRODUCT_PREFS.read_text(encoding="utf-8")
    android_prefs = ANDROID_PREFS.read_text(encoding="utf-8")
    server = re.escape(policy["provider"]["production_server"])
    required_pref_patterns = (
        rf'pref\("{re.escape(SERVER_PREF)}",\s*"{server}",\s*locked\);',
        rf'pref\("{re.escape(PREVIEW_PREF)}",\s*false,\s*locked\);',
    )
    for owner, content in (("desktop", prefs), ("Android", android_prefs)):
        for pattern in required_pref_patterns:
            if not re.search(pattern, content):
                raise PolicyError(
                    f"required {owner} provider preference is absent: {pattern}"
                )

    toolkit_config = TOOLKIT_CONFIG.read_text(encoding="utf-8")
    app_constants = APP_CONSTANTS.read_text(encoding="utf-8")
    toolkit_modules_build = TOOLKIT_MODULES_BUILD.read_text(encoding="utf-8")
    runtime = REMOTE_SETTINGS_RUNTIME.read_text(encoding="utf-8")
    for marker in (
        OPTION_NAME,
        CAPABILITY_NAME,
        CONFIG_NAME,
        BOOTSTRAP_CONFIG_NAME,
        CSV_CONFIG_NAME,
        SYNC_CONFIG_NAME,
        SYNC_CSV_CONFIG_NAME,
        'policy.get("allowed_collections")',
        'entry.get("packaged_bootstrap")',
        'entry.get("remote_sync")',
        "source-tree-relative",
    ):
        if marker not in toolkit_config:
            raise PolicyError(f"configure policy parser lacks marker: {marker}")
    for marker in (
        CAPABILITY_NAME,
        "NAVIS_REMOTE_SETTINGS_ALLOWED_COLLECTIONS",
        f'"@{CSV_CONFIG_NAME}@".split(",")',
        "NAVIS_REMOTE_SETTINGS_SYNC_COLLECTIONS",
        f'"@{SYNC_CSV_CONFIG_NAME}@".split(",")',
    ):
        if marker not in app_constants:
            raise PolicyError(f"AppConstants policy projection lacks marker: {marker}")
    define_loops = re.findall(
        r"for var in \((.*?)\):\s*DEFINES\[var\]\s*=\s*CONFIG\[var\]\s*or\s*\"\"",
        toolkit_modules_build,
        flags=re.DOTALL,
    )
    for config_name in (CSV_CONFIG_NAME, SYNC_CSV_CONFIG_NAME):
        if sum(f'"{config_name}"' in body for body in define_loops) != 1:
            raise PolicyError(
                "AppConstants preprocessing does not project the configured "
                f"{config_name} string with an empty policy-off fallback"
            )
    for marker in (
        f"AppConstants.{CAPABILITY_NAME}",
        "AppConstants.NAVIS_REMOTE_SETTINGS_ALLOWED_COLLECTIONS",
        "AppConstants.NAVIS_REMOTE_SETTINGS_SYNC_COLLECTIONS",
        "navisAllowedCollections()",
        "navisSyncCollections()",
        "Collection disabled by Navis product policy",
        "Remote synchronization disabled by Navis product policy",
    ):
        if marker not in runtime:
            raise PolicyError(f"runtime policy gate lacks marker: {marker}")
    for forbidden in (
        "desktop_embedder_allowed_collections",
        "desktop_embedder_enforce_allowed_collections_in_tests",
        "MOZ_DESKTOP_EMBEDDER_REMOTE_SETTINGS_COLLECTIONS",
    ):
        if forbidden in toolkit_config + app_constants + runtime + prefs + android_prefs:
            raise PolicyError(f"retired desktop-only policy marker remains: {forbidden}")
    if expected not in app_constants and f"@{CSV_CONFIG_NAME}@" not in app_constants:
        raise PolicyError("AppConstants does not consume the configured collection CSV")
    expected_sync = ",".join(sorted(syncable))
    if (
        expected_sync not in app_constants
        and f"@{SYNC_CSV_CONFIG_NAME}@" not in app_constants
    ):
        raise PolicyError("AppConstants does not consume the configured sync CSV")


def verify_semantic_port() -> None:
    ports = load_json(PORTS_PATH).get("ports")
    if not isinstance(ports, list):
        raise PolicyError("semantic port ledger has no ports array")
    matches = [
        port
        for port in ports
        if isinstance(port, dict)
        and port.get("id") == "navis-remote-settings-policy"
    ]
    if len(matches) != 1 or matches[0].get("order") != 29:
        raise PolicyError("Remote Settings semantic port is missing or misordered")
    digest = hashlib.sha256(PATCH_PATH.read_bytes()).hexdigest()
    if matches[0].get("sha256") != digest:
        raise PolicyError("Remote Settings semantic port hash differs from ledger")

    patch = PATCH_PATH.read_text(encoding="utf-8")
    required_markers = (
        OPTION_NAME,
        CAPABILITY_NAME,
        CONFIG_NAME,
        BOOTSTRAP_CONFIG_NAME,
        CSV_CONFIG_NAME,
        SYNC_CONFIG_NAME,
        SYNC_CSV_CONFIG_NAME,
        'DEFINES[var] = CONFIG[var] or ""',
        "NAVIS_REMOTE_SETTINGS_ALLOWED_COLLECTIONS",
        "NAVIS_REMOTE_SETTINGS_SYNC_COLLECTIONS",
        "navis_allowed_collections",
        "navis_sync_collections",
        "navis_enforce_allowed_collections_in_tests",
        "Collection disabled by Navis product policy",
        "Remote synchronization disabled by Navis product policy",
        "if (this._disabled || this._syncDisabled)",
        "allowed_collections and identifier not in allowed_collections",
        "test_navis_collection_policy.js",
        "An empty policy denies every collection",
        "A disallowed collection cannot read stale profile data",
        "A packaged-only collection cannot synchronize or mutate local data",
    )
    for marker in required_markers:
        if marker not in patch:
            raise PolicyError(f"semantic port lacks required marker: {marker}")
    for path in (
        "services/settings/dumps/blocklists/moz.build",
        "services/settings/dumps/main/moz.build",
        "services/settings/dumps/security-state/moz.build",
        "services/settings/static-dumps/main/moz.build",
        "toolkit/modules/moz.build",
    ):
        if path not in patch:
            raise PolicyError(f"semantic port does not filter {path}")
    for forbidden in (
        "--with-desktop-embedder-remote-settings-collections",
        "MOZ_DESKTOP_EMBEDDER_REMOTE_SETTINGS_COLLECTIONS",
        "AppConstants.MOZ_DESKTOP_EMBEDDER ||",
    ):
        if forbidden in patch:
            raise PolicyError(f"semantic port retains desktop-only policy: {forbidden}")

    signatures = (
        WORKSPACE
        / "../runtime/gecko/services/settings/test/unit/test_remote_settings_signatures.js"
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
    runtime: Path,
    policy: dict[str, Any],
    allowed: list[str],
    packaged: set[str],
    syncable: set[str],
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
        dumps_root = WORKSPACE / "../runtime/gecko/services/settings/dumps"
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
        for marker in (
            f'pref("{SERVER_PREF}", "{policy["provider"]["production_server"]}", locked);',
            f'pref("{PREVIEW_PREF}", false, locked);',
        ):
            if marker not in packaged_prefs:
                raise PolicyError(f"runtime policy preference is missing: {marker}")

        app_constants = archive.read("modules/AppConstants.sys.mjs").decode("utf-8")
        expected_csv = ",".join(allowed)
        expected_sync_csv = ",".join(sorted(syncable))
        if not has_enabled_app_constant(app_constants, CAPABILITY_NAME):
            raise PolicyError(
                f"runtime AppConstants policy is not enabled: {CAPABILITY_NAME}"
            )
        for marker in (
            "NAVIS_REMOTE_SETTINGS_ALLOWED_COLLECTIONS:",
            f'Object.freeze("{expected_csv}".split(","))',
            "NAVIS_REMOTE_SETTINGS_SYNC_COLLECTIONS:",
            f'Object.freeze("{expected_sync_csv}".split(","))',
        ):
            if marker not in app_constants:
                raise PolicyError(f"runtime AppConstants policy is missing: {marker}")

    config_status = runtime.parent.parent / "config.status"
    if config_status.is_file():
        substs = read_substs(config_status)
        configured = substs.get(CONFIG_NAME)
        if tuple(configured or ()) != tuple(allowed):
            raise PolicyError(
                f"configured collection allowlist differs: {configured!r}"
            )
        configured_bootstraps = substs.get(BOOTSTRAP_CONFIG_NAME)
        if set(configured_bootstraps or ()) != packaged:
            raise PolicyError(
                "configured bootstrap inventory differs: "
                f"{configured_bootstraps!r}"
            )
        if substs.get(CSV_CONFIG_NAME) != ",".join(allowed):
            raise PolicyError("configured collection CSV differs from policy")
        configured_sync = substs.get(SYNC_CONFIG_NAME)
        if set(configured_sync or ()) != syncable:
            raise PolicyError(
                f"configured sync collection inventory differs: {configured_sync!r}"
            )
        if substs.get(SYNC_CSV_CONFIG_NAME) != ",".join(sorted(syncable)):
            raise PolicyError("configured sync collection CSV differs from policy")
        if not substs.get(CAPABILITY_NAME):
            raise PolicyError("configured runtime lacks the Navis policy capability")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()
    try:
        policy, allowed, packaged, syncable = validate_policy()
        verify_product_declaration(policy, allowed, syncable)
        verify_semantic_port()
        if args.runtime:
            verify_runtime(
                args.runtime.resolve(), policy, allowed, packaged, syncable
            )
    except (OSError, UnicodeError, zipfile.BadZipFile, PolicyError) as error:
        print(f"Remote Settings policy verification failed: {error}", file=sys.stderr)
        return 1

    suffix = f"; runtime={args.runtime.resolve()}" if args.runtime else ""
    print(
        "Navis Remote Settings policy verified: "
        f"{len(allowed)} readable collections, {len(packaged)} bootstrap dumps, "
        f"{len(syncable)} remotely synchronized collections"
        f"{suffix}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
