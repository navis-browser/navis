#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


"""Verify Navis's offline, provider-neutral Clean Links policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent.parent
POLICY_PATH = WORKSPACE / "../platform/gecko-chrome/chrome/content/clean-links-policy.json"
PRODUCT_PREFS = WORKSPACE / "../platform/gecko-chrome/app/profile/navis.js"
PRODUCT_JAR = WORKSPACE / "../platform/gecko-chrome/chrome/jar.mn"
PRODUCT_CONFIG = WORKSPACE / "../platform/gecko-chrome/moz.configure"
REMOTE_POLICY = WORKSPACE / "../platform/gecko-chrome/config/navis-remote-settings-policy.json"
PORTS_PATH = WORKSPACE / "../runtime/config/gecko-semantic-ports.json"
PATCH_PATH = WORKSPACE / "../runtime/patches/gecko/0041-unify-product-query-stripping-policy.patch"

MOZILLA_SNAPSHOT_PARAMETERS = [
    "__hsfp",
    "__hssc",
    "__hstc",
    "__s",
    "_hsenc",
    "_openstat",
    "dclid",
    "fbclid",
    "gbraid",
    "gclid",
    "hsctatracking",
    "mc_eid",
    "mkt_tok",
    "msclkid",
    "oly_anon_id",
    "oly_enc_id",
    "twclid",
    "vero_id",
    "wbraid",
    "wickedid",
    "yclid",
    "ysclid",
]

ALIBABA_PRODUCT_DOMAINS = [
    "1688.com",
    "alibaba.com",
    "alibabacloud.com",
    "alibabagroup.com",
    "aliexpress.com",
    "alihealth.cn",
    "aliyun.com",
    "amap.com",
    "cainiao.com",
    "damai.cn",
    "damaiholdings.com",
    "daraz.com",
    "dingtalk.com",
    "ele.me",
    "fliggy.com",
    "freshhema.com",
    "freshippo.com",
    "goofish.com",
    "lazada.cn",
    "lazada.co.id",
    "lazada.com.my",
    "lazada.com.ph",
    "lazada.sg",
    "lazada.vn",
    "lingxigames.com",
    "myquark.cn",
    "quark.cn",
    "taobao.com",
    "tmall.com",
    "tmall.hk",
    "trendyol.com",
    "uc.cn",
    "youku.com",
]

DOMAIN = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
PARAMETER = re.compile(r"^[^\s&=#?]{1,128}$")


class PolicyError(RuntimeError):
    """The policy declaration and its consumers differ."""


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise PolicyError(f"{path} is not a JSON object")
    return value


def sorted_unique_strings(
    value: Any, name: str, pattern: re.Pattern[str]
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise PolicyError(f"{name} must be a nonempty array")
    if any(not isinstance(item, str) or not pattern.fullmatch(item) for item in value):
        raise PolicyError(f"{name} contains an invalid value")
    if value != sorted(set(value)):
        raise PolicyError(f"{name} must be sorted and unique")
    return value


def validate_policy() -> dict[str, Any]:
    policy = load_json(POLICY_PATH)
    expected_keys = {
        "schemaVersion",
        "revision",
        "provenance",
        "allowDomains",
        "rules",
    }
    if set(policy) != expected_keys or policy["schemaVersion"] != 1:
        raise PolicyError("unsupported Clean Links policy envelope")
    if not isinstance(policy["revision"], str) or not policy["revision"]:
        raise PolicyError("policy revision is missing")
    if not isinstance(policy["provenance"], list) or not policy["provenance"]:
        raise PolicyError("policy provenance is missing")
    allow_domains = sorted_unique_strings(
        policy["allowDomains"], "allowDomains", DOMAIN
    )
    if allow_domains != ["googleadservices.com"]:
        raise PolicyError("reviewed allow-domain snapshot changed")

    rules = policy["rules"]
    if not isinstance(rules, list) or len(rules) != 2:
        raise PolicyError("expected one snapshot and one Alibaba product rule")
    ids: set[str] = set()
    global_parameters: list[str] = []
    alibaba_rule: dict[str, Any] | None = None
    for index, rule in enumerate(rules, 1):
        if not isinstance(rule, dict):
            raise PolicyError(f"rule {index} is not an object")
        required = {"id", "scope", "queryParameters"}
        if not required <= set(rule):
            raise PolicyError(f"rule {index} is missing fields")
        identifier = rule["id"]
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", identifier)
            or identifier in ids
        ):
            raise PolicyError(f"invalid or duplicate rule id: {identifier!r}")
        ids.add(identifier)
        parameters = sorted_unique_strings(
            rule["queryParameters"], f"{identifier}.queryParameters", PARAMETER
        )
        if rule["scope"] == "global":
            if set(rule) != required:
                raise PolicyError("global rule has scope-specific fields")
            global_parameters.extend(parameters)
        elif rule["scope"] == "registrable-domain":
            if set(rule) != required | {"domains"}:
                raise PolicyError("registrable-domain rule fields differ")
            sorted_unique_strings(rule["domains"], f"{identifier}.domains", DOMAIN)
            if parameters == ["spm"]:
                alibaba_rule = rule
        else:
            raise PolicyError(f"unsupported policy scope: {rule['scope']!r}")

    if global_parameters != MOZILLA_SNAPSHOT_PARAMETERS:
        raise PolicyError("Mozilla query-stripping snapshot changed")
    if "spm" in global_parameters:
        raise PolicyError("spm must never be stripped globally")
    if not alibaba_rule or alibaba_rule["domains"] != ALIBABA_PRODUCT_DOMAINS:
        raise PolicyError("reviewed Alibaba spm domain scope changed")
    for unsafe in ("alicdn.com", "alipay.com", "aliyuncs.com", "mmstat.com", "tbcdn.cn"):
        if unsafe in alibaba_rule["domains"]:
            raise PolicyError(f"control/payment domain entered spm scope: {unsafe}")
    return policy


def require_markers(path: Path, markers: tuple[str, ...]) -> str:
    content = path.read_text(encoding="utf-8")
    for marker in markers:
        if marker not in content:
            raise PolicyError(f"{path.relative_to(WORKSPACE.parent)} lacks {marker!r}")
    return content


def verify_consumers() -> None:
    prefs = require_markers(
        PRODUCT_PREFS,
        (
            'pref("privacy.query_stripping.product_policy_uri", "chrome://navis/content/clean-links-policy.json", locked);',
            'pref("privacy.query_stripping.use_unified_rules", true, locked);',
            'pref("privacy.query_stripping.remote_settings.enabled", false, locked);',
        ),
    )
    jar = require_markers(
        PRODUCT_JAR,
        (
            "content/clean-links-policy.json",
            "(content/clean-links-policy.json)",
        ),
    )
    if not prefs or not jar:
        raise AssertionError("unreachable")

    service = require_markers(
        WORKSPACE
        / "../runtime/gecko/toolkit/components/antitracking/URLQueryStrippingListService.sys.mjs",
        (
            "CleanLinksPolicy",
            "parseProductPolicy",
            'uri.schemeIs("chrome")',
            'uri.schemeIs("resource")',
            "PREF_REMOTE_SETTINGS_ENABLED",
            "this.productStripList",
            "this.productAllowList",
            "combineAndParseLists(packagedPolicy.rules, [])",
            "await this._notifyStripOnShareObservers()",
        ),
    )
    stripper = require_markers(
        WORKSPACE
        / "../runtime/gecko/toolkit/components/antitracking/URLQueryStringStripper.cpp",
        (
            "privacy.query_stripping.use_unified_rules",
            "mStripOnShareDomainMap.Clear()",
            "rule.mDomains",
            "GetBaseDomain(aURI, 0, baseDomain)",
            "ShouldStripParam(host, baseDomain, aName)",
            "StripForCopyOrShareInternal(",
        ),
    )
    if not service or not stripper:
        raise AssertionError("unreachable")
    require_markers(
        WORKSPACE / "../runtime/gecko/dom/chrome-webidl/StripOnShareRule.webidl",
        ("sequence<UTF8String> domains = [];",),
    )

    remote_policy = load_json(REMOTE_POLICY)
    remote_ids = {
        entry.get("identifier")
        for entry in remote_policy.get("allowed_collections", [])
        if isinstance(entry, dict)
    }
    if "main/query-stripping" in remote_ids:
        raise PolicyError("Clean Links still allows live main/query-stripping")
    for path, content in (
        (PRODUCT_PREFS, prefs),
        (PRODUCT_CONFIG, PRODUCT_CONFIG.read_text(encoding="utf-8")),
    ):
        if "main/query-stripping" in content:
            raise PolicyError(
                f"{path.relative_to(WORKSPACE.parent)} still enables main/query-stripping"
            )


def verify_semantic_port() -> None:
    ports = load_json(PORTS_PATH).get("ports")
    if not isinstance(ports, list):
        raise PolicyError("semantic port ledger has no ports array")
    matches = [
        port
        for port in ports
        if isinstance(port, dict)
        and port.get("id") == "unified-product-query-stripping-policy"
    ]
    if len(matches) != 1 or matches[0].get("order") != 41:
        raise PolicyError("Clean Links semantic port is missing or misordered")
    if not PATCH_PATH.is_file():
        raise PolicyError("Clean Links semantic patch is missing")
    digest = hashlib.sha256(PATCH_PATH.read_bytes()).hexdigest()
    if matches[0].get("sha256") != digest:
        raise PolicyError("Clean Links semantic patch hash differs from ledger")


def verify_runtime(runtime: Path, policy: dict[str, Any]) -> None:
    omni = runtime / "omni.ja"
    if not omni.is_file():
        raise PolicyError(f"runtime omni.ja is missing: {omni}")
    with zipfile.ZipFile(omni) as archive:
        try:
            packaged_policy = json.loads(
                archive.read("chrome/navis/content/clean-links-policy.json"),
                object_pairs_hook=strict_object,
            )
            packaged_prefs = archive.read("defaults/pref/navis.js").decode("utf-8")
            last_modified = json.loads(
                archive.read("defaults/settings/last_modified.json")
            )
        except (KeyError, UnicodeError, json.JSONDecodeError) as error:
            raise PolicyError(f"runtime Clean Links files are invalid: {error}") from error
        if packaged_policy != policy:
            raise PolicyError("runtime Clean Links policy differs from source")
        for marker in (
            'pref("privacy.query_stripping.product_policy_uri", "chrome://navis/content/clean-links-policy.json", locked);',
            'pref("privacy.query_stripping.use_unified_rules", true, locked);',
            'pref("privacy.query_stripping.remote_settings.enabled", false, locked);',
        ):
            if marker not in packaged_prefs:
                raise PolicyError(f"runtime Clean Links preference is missing: {marker}")
        if "main/query-stripping" in last_modified:
            raise PolicyError("runtime timestamp index contains main/query-stripping")
        if "defaults/settings/main/query-stripping.json" in archive.namelist():
            raise PolicyError("runtime packages main/query-stripping data")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()
    try:
        policy = validate_policy()
        verify_consumers()
        verify_semantic_port()
        if args.runtime:
            verify_runtime(args.runtime.resolve(), policy)
    except (OSError, UnicodeError, zipfile.BadZipFile, PolicyError) as error:
        print(f"Clean Links policy verification failed: {error}", file=sys.stderr)
        return 1
    suffix = f"; runtime={args.runtime.resolve()}" if args.runtime else ""
    print(
        "Navis Clean Links policy verified: offline provider, "
        f"{len(MOZILLA_SNAPSHOT_PARAMETERS)} global snapshot parameters, "
        f"{len(ALIBABA_PRODUCT_DOMAINS)} Alibaba product domains{suffix}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
