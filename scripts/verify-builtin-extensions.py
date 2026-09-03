#!/usr/bin/env python3

import argparse
import hashlib
import json
import pathlib
import re
import sys
import zipfile


REQUIRED_SIGNATURES = {
    "META-INF/cose.manifest",
    "META-INF/cose.sig",
    "META-INF/manifest.mf",
    "META-INF/mozilla.rsa",
    "META-INF/mozilla.sf",
}

PRODUCTION_MOZCONFIGS = (
    "mozconfig.runtime",
    "mozconfig.runtime.release",
    "mozconfig.runtime.no-webrtc.sccache",
    "mozconfig.runtime.no-webrtc.tests.sccache",
    "mozconfig.win64",
    "mozconfig.win64.release",
)
def fail(message: str) -> None:
    raise SystemExit(f"Built-in extension verification failed: {message}")


def load_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path}: {error}")


def safe_child(root: pathlib.Path, relative: str) -> pathlib.Path:
    path = pathlib.PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative:
        fail(f"unsafe registry path: {relative}")
    result = root.joinpath(*path.parts).resolve()
    if result != root and root not in result.parents:
        fail(f"registry path escapes built-in directory: {relative}")
    return result


def verify_extension(
    builtin_dir: pathlib.Path,
    entry: dict,
    package_root: pathlib.Path | None,
) -> tuple[str, bool, str, str, str]:
    metadata_path = safe_child(builtin_dir, entry["metadata"])
    metadata = load_json(metadata_path)
    artifact = safe_child(metadata_path.parent, metadata["artifact"])
    try:
        payload = artifact.read_bytes()
    except OSError as error:
        fail(f"cannot read {artifact}: {error}")

    digest = hashlib.sha256(payload).hexdigest()
    if len(payload) != metadata["size"]:
        fail(f"{artifact.name} size is {len(payload)}, expected {metadata['size']}")
    if digest != metadata["sha256"]:
        fail(f"{artifact.name} SHA-256 is {digest}, expected {metadata['sha256']}")
    if not re.fullmatch(r"[0-9a-f]{40}", metadata["source_commit"]):
        fail(f"{artifact.name} source_commit is not a full Git object ID")
    if not metadata["download"].startswith("https://"):
        fail(f"{artifact.name} download URL is not HTTPS")
    if not metadata["source_repository"].startswith("https://"):
        fail(f"{artifact.name} source repository is not HTTPS")

    with zipfile.ZipFile(artifact) as archive:
        bad_member = archive.testzip()
        if bad_member:
            fail(f"{artifact.name} has corrupt ZIP member: {bad_member}")
        names = archive.namelist()
        if len(names) != len(set(names)):
            fail(f"{artifact.name} contains duplicate ZIP members")
        for name in names:
            path = pathlib.PurePosixPath(name)
            if name.startswith("/") or ".." in path.parts or "\\" in name:
                fail(f"{artifact.name} has unsafe ZIP member: {name}")
        missing_signatures = REQUIRED_SIGNATURES.difference(names)
        if missing_signatures:
            fail(f"{artifact.name} missing signatures: {sorted(missing_signatures)}")
        if "LICENSE.txt" not in names:
            fail(f"{artifact.name} does not contain LICENSE.txt")
        manifest = json.loads(archive.read("manifest.json"))

    addon_id = manifest["browser_specific_settings"]["gecko"]["id"]
    if artifact.name != f"{addon_id}.xpi":
        fail(f"{artifact.name} does not match manifest ID {addon_id}")
    for key in ("name", "version"):
        if manifest[key] != metadata[key]:
            fail(
                f"{artifact.name} manifest {key} is {manifest[key]}, "
                f"expected {metadata[key]}"
            )
    if addon_id != metadata["id"]:
        fail(f"{artifact.name} manifest ID is {addon_id}, expected {metadata['id']}")
    permissions = manifest.get("permissions", [])
    if permissions != metadata["required_permissions"]:
        fail(f"{artifact.name} manifest permission set or ordering changed")
    if "nativeMessaging" in permissions:
        fail(f"{artifact.name} requests forbidden nativeMessaging permission")
    if manifest.get("manifest_version") not in (2, 3):
        fail(f"{artifact.name} has unsupported manifest version")

    if package_root is not None:
        packaged = package_root / "extensions" / artifact.name
        if not packaged.is_file():
            fail(f"packaged built-in is missing: {packaged}")
        packaged_digest = hashlib.sha256(packaged.read_bytes()).hexdigest()
        if packaged_digest != digest:
            fail(f"packaged built-in hash differs: {packaged}")

    return (
        addon_id,
        entry["default_toolbar_pinned"],
        metadata["version"],
        digest,
        artifact.relative_to(builtin_dir).as_posix(),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "root",
        nargs="?",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--package-root", type=pathlib.Path)
    args = parser.parse_args()
    builtin_dir = (args.root / "product" / "builtin").resolve()
    registry = load_json(builtin_dir / "extensions.json")
    if registry.get("schema") != 3:
        fail("unsupported registry schema")
    entries = registry.get("extensions")
    if not isinstance(entries, list) or not entries:
        fail("registry has no extensions")

    identities: set[str] = set()
    results = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail("registry entry is not an object")
        if set(entry) != {"required", "default_toolbar_pinned", "metadata"}:
            fail(f"registry entry has unexpected fields: {entry}")
        if not isinstance(entry["metadata"], str) or not entry["metadata"]:
            fail("registry entry has no metadata path")
        if entry["required"] is not True:
            fail(f"1.0 registry entry is not required: {entry['metadata']}")
        if not isinstance(entry["default_toolbar_pinned"], bool):
            fail(
                "registry default_toolbar_pinned state is not boolean: "
                f"{entry['metadata']}"
            )
        result = verify_extension(builtin_dir, entry, args.package_root)
        addon_id, _default_pinned, _version, _digest, _artifact_path = result
        if addon_id in identities:
            fail(f"duplicate built-in extension ID: {addon_id}")
        identities.add(addon_id)
        results.append(result)

    for relative in PRODUCTION_MOZCONFIGS:
        mozconfig_path = args.root / relative
        try:
            mozconfig = mozconfig_path.read_text(encoding="utf-8")
        except OSError as error:
            fail(f"cannot read production profile {mozconfig_path}: {error}")
        if "ac_add_options --enable-webextensions-runtime" not in mozconfig:
            fail(f"production profile does not retain built-ins: {relative}")
        if "ac_add_options --disable-webextensions-runtime" in mozconfig:
            fail(f"production profile disables built-ins: {relative}")

    profile = (args.root / "product" / "app" / "profile" / "navis.js").read_text(
        encoding="utf-8"
    )
    expected_ids = ",".join(addon_id for addon_id, *_rest in results)
    expected_pref = (
        'pref("extensions.applicationBuiltins.allowedIds", '
        f'"{expected_ids}", locked);'
    )
    if expected_pref not in profile:
        fail("locked built-in ID preference differs from registry ordering")
    pinned_json = json.dumps(
        [addon_id for addon_id, pinned, *_rest in results if pinned],
        ensure_ascii=True,
        separators=(",", ":"),
    ).replace('"', '\\"')
    expected_pinned_pref = (
        'pref("navis.extensions.toolbar.pinned", '
        f'"{pinned_json}");'
    )
    if expected_pinned_pref not in profile:
        fail("default pinned extension actions differ from registry ordering")

    build_definition = (builtin_dir / "moz.build").read_text(encoding="utf-8")
    for addon_id, _pinned, _version, _digest, artifact_path in results:
        if f'"{artifact_path}"' not in build_definition:
            fail(f"built-in artifact is not packaged by moz.build: {addon_id}")

    package_manifest = (
        args.root / "product" / "installer" / "package-manifest.in"
    ).read_text(encoding="utf-8")
    for required_line in (
        "@RESPATH@/extensions/*.xpi",
        "@RESPATH@/builtin-extensions/extensions.json",
    ):
        if required_line not in package_manifest:
            fail(f"installer does not package the built-in collection: {required_line}")

    if args.package_root is not None:
        packaged_registry = (
            args.package_root / "builtin-extensions" / "extensions.json"
        )
        if not packaged_registry.is_file():
            fail(f"packaged built-in registry is missing: {packaged_registry}")
        if packaged_registry.read_bytes() != (
            builtin_dir / "extensions.json"
        ).read_bytes():
            fail("packaged built-in registry differs from the reviewed source")

        extension_dir = args.package_root / "extensions"
        expected_files = {f"{addon_id}.xpi" for addon_id in identities}
        actual_files = (
            {path.name for path in extension_dir.iterdir()}
            if extension_dir.is_dir()
            else set()
        )
        if actual_files != expected_files:
            fail(
                "packaged extension set differs from registry: "
                f"actual={sorted(actual_files)}, expected={sorted(expected_files)}"
            )

    for addon_id, pinned, version, digest, _artifact_path in results:
        print(
            "Built-in extension verified: "
            f"{addon_id} {version} default-pinned={str(pinned).lower()} "
            f"sha256={digest}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
