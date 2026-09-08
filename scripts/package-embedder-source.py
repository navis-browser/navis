#!/usr/bin/env python3

"""Build or verify a deterministic Desktop Gecko Embedder source package."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import os
import re
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


WORKSPACE = Path(__file__).resolve().parent.parent
RUNTIME = WORKSPACE.parent / "runtime"
if not RUNTIME.is_dir():
    RUNTIME = WORKSPACE
DEFAULT_DEFINITION = WORKSPACE / "config/desktop-embedder-source-package.json"
GENERATED_MANIFEST = "SOURCE-MANIFEST.json"

RUNTIME_OWNED_SOURCES = {
    "core",
    "embedder",
    "patches",
    "vendor",
    "config/gecko-esr-review-routes.json",
    "config/gecko-semantic-ports.json",
    "scripts/prepare-desktop-embedder.py",
    "scripts/review-gecko-esr-update.py",
}
RUNTIME_OWNED_PREFIXES = ("core/", "embedder/", "patches/", "vendor/")


class PackageError(RuntimeError):
    """A source-package boundary or integrity invariant failed."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, default=DEFAULT_DEFINITION)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the working-tree package definition without writing an archive",
    )
    parser.add_argument(
        "--verify",
        type=Path,
        help="verify an existing archive instead of building one",
    )
    return parser.parse_args()


def strict_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise PackageError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_json_bytes(content: bytes, description: str) -> dict:
    try:
        value = json.loads(content, object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PackageError(f"invalid JSON in {description}: {error}") from error
    if not isinstance(value, dict):
        raise PackageError(f"expected a JSON object in {description}")
    return value


def load_json(path: Path) -> dict:
    try:
        return load_json_bytes(path.read_bytes(), os.fspath(path))
    except OSError as error:
        raise PackageError(f"cannot read {path}: {error}") from error


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_relative(value: str, description: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
    ):
        raise PackageError(f"unsafe {description}: {value!r}")
    if path.as_posix() != value:
        raise PackageError(f"noncanonical {description}: {value!r}")
    return path


def validate_definition_data(definition: dict) -> None:
    if definition.get("schema_version") != 1:
        raise PackageError("unsupported source-package definition schema")
    required = {
        "schema_version",
        "package_name",
        "version",
        "source_api_version",
        "upstream_pin",
        "semantic_ports",
        "license",
        "readme",
        "includes",
        "forbidden_archive_prefixes",
    }
    if set(definition) != required:
        raise PackageError(
            "source-package definition keys differ: "
            f"missing={sorted(required - set(definition))}, "
            f"extra={sorted(set(definition) - required)}"
        )
    if not isinstance(definition["package_name"], str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9.-]*", definition["package_name"]
    ):
        raise PackageError("invalid source-package name")
    if not isinstance(definition["version"], str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", definition["version"]
    ):
        raise PackageError("source-package version must be semantic x.y.z")
    if not isinstance(definition["source_api_version"], int) or definition[
        "source_api_version"
    ] <= 0:
        raise PackageError("source_api_version must be a positive integer")
    for key in ("upstream_pin", "semantic_ports", "license", "readme"):
        if not isinstance(definition[key], str):
            raise PackageError(f"{key} must be a source-relative path")
        normalized_relative(definition[key], key)
    prefixes = definition["forbidden_archive_prefixes"]
    if not isinstance(prefixes, list) or not prefixes or not all(
        isinstance(prefix, str) and prefix.endswith("/") for prefix in prefixes
    ):
        raise PackageError("forbidden_archive_prefixes must be nonempty prefixes")
    if prefixes != sorted(set(prefixes)):
        raise PackageError("forbidden_archive_prefixes must be sorted and unique")
    for prefix in prefixes:
        normalized_relative(prefix.removesuffix("/"), "forbidden archive prefix")
    includes = definition["includes"]
    if not isinstance(includes, list) or not includes:
        raise PackageError("source-package definition has no includes")
    for index, include in enumerate(includes, 1):
        if not isinstance(include, dict) or set(include) != {"path", "role"}:
            raise PackageError(f"invalid include entry {index}")
        relative = include["path"]
        role = include["role"]
        if not isinstance(relative, str) or not isinstance(role, str) or not role:
            raise PackageError(f"invalid include entry {index}")
        normalized_relative(relative, f"include path {index}")


def validate_definition(definition_path: Path) -> tuple[dict, Path]:
    definition_path = definition_path.resolve()
    definition = load_json(definition_path)
    validate_definition_data(definition)

    try:
        definition_path.relative_to(WORKSPACE)
    except ValueError as error:
        raise PackageError("source-package definition is outside the workspace") from error
    return definition, definition_path


def file_mode(path: Path) -> int:
    return 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644


def source_root(relative: str) -> Path:
    return (
        RUNTIME
        if relative in RUNTIME_OWNED_SOURCES
        or relative.startswith(RUNTIME_OWNED_PREFIXES)
        else WORKSPACE
    )


def collect_files(definition: dict) -> list[dict]:
    selected: dict[str, dict] = {}
    include_paths: set[str] = set()
    for index, include in enumerate(definition["includes"], 1):
        if not isinstance(include, dict) or set(include) != {"path", "role"}:
            raise PackageError(f"invalid include entry {index}")
        relative = include["path"]
        role = include["role"]
        if not isinstance(relative, str) or not isinstance(role, str) or not role:
            raise PackageError(f"invalid include entry {index}")
        normalized_relative(relative, f"include path {index}")
        if relative in include_paths:
            raise PackageError(f"duplicate include path: {relative}")
        include_paths.add(relative)
        root = source_root(relative)
        source = root / relative
        if source.is_symlink() or not source.exists():
            raise PackageError(f"included source is missing or a symlink: {relative}")
        candidates = [source] if source.is_file() else sorted(source.rglob("*"))
        for candidate in candidates:
            if candidate.is_symlink():
                raise PackageError(f"source package refuses symlink: {candidate}")
            if not candidate.is_file():
                continue
            path = candidate.relative_to(root).as_posix()
            if any(path.startswith(prefix) for prefix in definition["forbidden_archive_prefixes"]):
                raise PackageError(f"forbidden path selected for source package: {path}")
            if path in selected:
                raise PackageError(f"source package selects {path} more than once")
            content = candidate.read_bytes()
            selected[path] = {
                "path": path,
                "role": role,
                "bytes": len(content),
                "sha256": sha256_bytes(content),
                "mode": f"{file_mode(candidate):04o}",
            }

    required_paths = {
        definition["upstream_pin"],
        definition["semantic_ports"],
        definition["license"],
        definition["readme"],
        "config/desktop-embedder-source-package.json",
        "config/gecko-esr-review-routes.json",
        "core/rust/runtime/src/lib.rs",
        "core/rust/ffi/include/NavisCoreFFI.h",
        "core/rust/Cargo.lock",
        "docs/navis-architecture.md",
        "docs/development-standards.md",
        "scripts/prepare-desktop-embedder.py",
        "scripts/package-embedder-source.py",
        "scripts/review-gecko-esr-update.py",
        "scripts/inject-embedder-shell-into-omnijar.sh",
        "scripts/m2-server.py",
        "scripts/verify-core-abi.py",
        "tests/embedder-shell/shell.mjs",
        "tests/m2-site/index.html",
        "tests/test_review_gecko_esr_update.py",
    }
    missing = sorted(required_paths - set(selected))
    if missing:
        raise PackageError(f"required source-package files are not selected: {missing}")
    if any(path.startswith(("product/", "navis/")) for path in selected):
        raise PackageError("product source entered the reusable source package")
    return [selected[path] for path in sorted(selected)]


def parse_patch_paths(content: bytes, patch_path: str) -> list[str]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageError(f"patch is not UTF-8: {patch_path}") from error
    paths: set[str] = set()
    for source, destination in re.findall(
        r"^diff --git a/(.+?) b/(.+?)$", text, flags=re.MULTILINE
    ):
        if source != destination:
            raise PackageError(f"renaming semantic-port patch is unsupported: {patch_path}")
        normalized_relative(source, f"path in {patch_path}")
        paths.add(source)
    if not paths:
        raise PackageError(f"semantic-port patch contains no diff entries: {patch_path}")
    return sorted(paths)


def validate_semantic_ports(
    definition: dict, contents: dict[str, bytes]
) -> tuple[dict, list[dict]]:
    ledger_path = definition["semantic_ports"]
    ledger = load_json_bytes(contents[ledger_path], ledger_path)
    if ledger.get("schema_version") != 1:
        raise PackageError("unsupported semantic-port ledger schema")
    if ledger.get("upstream_pin") != definition["upstream_pin"]:
        raise PackageError("semantic-port ledger selects a different upstream pin")
    ports = ledger.get("ports")
    if not isinstance(ports, list) or not ports:
        raise PackageError("semantic-port ledger is empty")
    generated_ports: list[dict] = []
    seen_patches: set[str] = set()
    for expected_order, port in enumerate(ports, 1):
        if not isinstance(port, dict) or port.get("order") != expected_order:
            raise PackageError(f"invalid semantic-port order at entry {expected_order}")
        for key in (
            "id",
            "patch",
            "sha256",
            "classification",
            "intent",
            "invariants",
            "port_review",
        ):
            if key not in port:
                raise PackageError(f"semantic port {expected_order} lacks {key}")
        patch_path = port["patch"]
        if patch_path in seen_patches or patch_path not in contents:
            raise PackageError(f"invalid semantic-port patch inventory: {patch_path}")
        seen_patches.add(patch_path)
        actual_hash = sha256_bytes(contents[patch_path])
        if port["sha256"] != actual_hash:
            raise PackageError(f"semantic-port hash mismatch: {patch_path}")
        generated_ports.append(
            {
                "order": expected_order,
                "id": port["id"],
                "patch": patch_path,
                "sha256": actual_hash,
                "classification": port["classification"],
                "touched_paths": parse_patch_paths(contents[patch_path], patch_path),
            }
        )
    packaged_patches = {
        path
        for path in contents
        if path.startswith("patches/gecko/") and path.endswith(".patch")
    }
    if packaged_patches != seen_patches:
        raise PackageError(
            "packaged patch inventory differs from semantic-port ledger: "
            f"unowned={sorted(packaged_patches - seen_patches)}, "
            f"missing={sorted(seen_patches - packaged_patches)}"
        )
    return ledger, generated_ports


def validate_pin(definition: dict, contents: dict[str, bytes]) -> dict:
    pin_path = definition["upstream_pin"]
    pin = load_json_bytes(contents[pin_path], pin_path)
    if set(pin) != {"remote", "tag", "build_tag", "commit"}:
        raise PackageError("upstream pin must contain remote/tag/build_tag/commit")
    if not all(isinstance(value, str) and value for value in pin.values()):
        raise PackageError("upstream pin contains an empty or non-string value")
    if not re.fullmatch(r"[0-9a-f]{40}", pin["commit"]):
        raise PackageError("upstream pin commit is not a full SHA-1")
    return pin


def validate_esr_review_assets(contents: dict[str, bytes]) -> dict:
    route_path = "config/gecko-esr-review-routes.json"
    tool_path = "scripts/review-gecko-esr-update.py"
    test_path = "tests/test_review_gecko_esr_update.py"
    routes = load_json_bytes(contents[route_path], route_path)
    if set(routes) != {
        "schema_version",
        "description",
        "default_route",
        "routes",
    } or routes.get("schema_version") != 1:
        raise PackageError("packaged ESR review-route schema is invalid")
    default_route = routes["default_route"]
    entries = routes["routes"]
    if not isinstance(default_route, str) or not default_route:
        raise PackageError("packaged ESR review default route is invalid")
    if not isinstance(entries, list) or not entries:
        raise PackageError("packaged ESR review-route list is empty")
    route_ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "id",
            "description",
            "patterns",
        }:
            raise PackageError("packaged ESR review route is invalid")
        route_id = entry["id"]
        patterns = entry["patterns"]
        if not isinstance(route_id, str) or not route_id:
            raise PackageError("packaged ESR review route has no id")
        if not isinstance(patterns, list) or not patterns or not all(
            isinstance(pattern, str) for pattern in patterns
        ):
            raise PackageError(f"packaged ESR review route {route_id!r} is invalid")
        try:
            for pattern in patterns:
                re.compile(pattern)
        except re.error as error:
            raise PackageError(
                f"packaged ESR review route {route_id!r} has invalid regex: {error}"
            ) from error
        route_ids.append(route_id)
    if len(set(route_ids)) != len(route_ids) or default_route in route_ids:
        raise PackageError("packaged ESR review route ids are not unique")
    if not contents[tool_path].startswith(b"#!/usr/bin/env python3\n"):
        raise PackageError("packaged ESR review tool has an invalid entry point")
    if b"class GeckoEsrReviewTest" not in contents[test_path]:
        raise PackageError("packaged ESR review test has an invalid entry point")
    return {
        "tool": tool_path,
        "test": test_path,
        "routes": route_path,
        "default_route": default_route,
        "route_ids": route_ids,
    }


def validate_conformance_assets(contents: dict[str, bytes]) -> dict:
    shell_path = "tests/embedder-shell/shell.mjs"
    fixture_path = "tests/m2-site/index.html"
    injector_path = "scripts/inject-embedder-shell-into-omnijar.sh"
    server_path = "scripts/m2-server.py"
    windows_result_path = ("docs/runtime-validation.md"
        if "docs/runtime-validation.md" in contents
        else "docs/win64-api-v2-delegate-results.md")
    required_paths = {
        shell_path,
        "tests/embedder-shell/bootstrap.mjs",
        "tests/embedder-shell/shell.xhtml",
        "tests/embedder-shell/auxiliary.xhtml",
        "tests/embedder-shell/user.js",
        fixture_path,
        "tests/m2-site/history.html",
        "tests/m2-site/history-target.html",
        injector_path,
        server_path,
        windows_result_path,
    }
    missing = sorted(required_paths - set(contents))
    if missing:
        raise PackageError(f"packaged conformance harness is incomplete: {missing}")

    try:
        shell = contents[shell_path].decode("utf-8")
        injector = contents[injector_path].decode("utf-8")
        server = contents[server_path].decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageError("packaged conformance harness is not UTF-8") from error

    shell_markers = {
        "active-content-process",
        "contract-complete",
        "loading-stop-complete",
        "multi-window-lifecycle-complete",
        "view-lifecycle-transfer-complete",
    }
    missing_markers = sorted(marker for marker in shell_markers if marker not in shell)
    if missing_markers:
        raise PackageError(
            f"packaged API shell lacks conformance markers: {missing_markers}"
        )
    if not injector.startswith("#!/usr/bin/env bash\n") or shell_path not in injector:
        raise PackageError("packaged shell injector has an invalid entry point")
    if not server.startswith("#!/usr/bin/env python3\n") or not all(
        marker in server for marker in ("/state", "/slow")
    ):
        raise PackageError("packaged conformance fixture server is incomplete")
    return {
        "shell": shell_path,
        "fixture": fixture_path,
        "fixture_server": server_path,
        "shell_injector": injector_path,
        "windows_result": windows_result_path,
    }


def validate_api(definition: dict, contents: dict[str, bytes]) -> None:
    source_path = "embedder/modules/DesktopEngine.sys.mjs"
    try:
        source = contents[source_path].decode("utf-8")
    except KeyError as error:
        raise PackageError(f"public API module is not packaged: {source_path}") from error
    except UnicodeDecodeError as error:
        raise PackageError("public API module is not UTF-8") from error
    versions = re.findall(
        r"^export const DESKTOP_EMBEDDER_API_VERSION = ([0-9]+);$",
        source,
        flags=re.MULTILINE,
    )
    expected = str(definition["source_api_version"])
    if versions != [expected]:
        raise PackageError(
            f"public source API version differs from package definition: {versions}"
        )


def aggregate_hash(files: list[dict]) -> str:
    digest = hashlib.sha256()
    for entry in files:
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry["sha256"].encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry["bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(entry["mode"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def validate_file_entries(files: object) -> list[dict]:
    if not isinstance(files, list) or not files:
        raise PackageError("generated source manifest has no files")
    paths: list[str] = []
    for index, entry in enumerate(files, 1):
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "role",
            "bytes",
            "sha256",
            "mode",
        }:
            raise PackageError(f"invalid generated file entry {index}")
        path = entry["path"]
        if not isinstance(path, str):
            raise PackageError(f"invalid path in generated file entry {index}")
        normalized_relative(path, "generated source-manifest path")
        if path == GENERATED_MANIFEST:
            raise PackageError(f"{GENERATED_MANIFEST} cannot list itself")
        if not isinstance(entry["role"], str) or not entry["role"]:
            raise PackageError(f"invalid role for generated source path: {path}")
        if (
            not isinstance(entry["bytes"], int)
            or isinstance(entry["bytes"], bool)
            or entry["bytes"] < 0
        ):
            raise PackageError(f"invalid byte count for generated source path: {path}")
        if not isinstance(entry["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", entry["sha256"]
        ):
            raise PackageError(f"invalid SHA-256 for generated source path: {path}")
        if entry["mode"] not in {"0644", "0755"}:
            raise PackageError(f"invalid mode for generated source path: {path}")
        paths.append(path)
    if paths != sorted(paths):
        raise PackageError("generated source manifest paths are not sorted")
    if len(set(paths)) != len(paths):
        raise PackageError("generated source manifest has duplicate paths")
    return files


def validate_definition_inventory(
    definition: dict,
    files: list[dict],
    require_esr_review: bool = True,
    require_conformance: bool = True,
) -> None:
    entries = {entry["path"]: entry for entry in files}
    claimed: set[str] = set()
    for include in definition["includes"]:
        relative = include["path"]
        matches = {
            path
            for path in entries
            if path == relative or path.startswith(f"{relative}/")
        }
        if not matches:
            raise PackageError(f"source-package include has no files: {relative}")
        overlap = claimed & matches
        if overlap:
            raise PackageError(
                f"source-package includes overlap at: {sorted(overlap)}"
            )
        wrong_roles = sorted(
            path for path in matches if entries[path]["role"] != include["role"]
        )
        if wrong_roles:
            raise PackageError(
                f"generated roles differ from package definition: {wrong_roles}"
            )
        claimed.update(matches)
    if claimed != set(entries):
        raise PackageError(
            "generated inventory contains files outside package includes: "
            f"{sorted(set(entries) - claimed)}"
        )
    required_paths = {
        definition["upstream_pin"],
        definition["semantic_ports"],
        definition["license"],
        definition["readme"],
        "config/desktop-embedder-source-package.json",
        "scripts/prepare-desktop-embedder.py",
        "scripts/package-embedder-source.py",
    }
    if require_esr_review:
        required_paths.update(
            {
                "config/gecko-esr-review-routes.json",
                "scripts/review-gecko-esr-update.py",
                "tests/test_review_gecko_esr_update.py",
            }
        )
    if require_conformance:
        required_paths.update(
            {
                "docs/runtime-validation.md",
                "scripts/inject-embedder-shell-into-omnijar.sh",
                "scripts/m2-server.py",
                "tests/embedder-shell/shell.mjs",
                "tests/m2-site/index.html",
            }
        )
    missing = sorted(required_paths - set(entries))
    if missing:
        raise PackageError(f"required source-package files are not selected: {missing}")
    for path in entries:
        if any(
            path.startswith(prefix)
            for prefix in definition["forbidden_archive_prefixes"]
        ):
            raise PackageError(f"forbidden source path in package inventory: {path}")


def validate_document_links(contents: dict[str, bytes]) -> None:
    for path in sorted(contents):
        if not path.endswith(".md"):
            continue
        try:
            document = contents[path].decode("utf-8")
        except UnicodeDecodeError as error:
            raise PackageError(f"packaged Markdown is not UTF-8: {path}") from error
        for target in re.findall(r"(?<!!)\[[^]]*\]\(([^)]+)\)", document):
            if target.startswith("#") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            normalized_relative(target, f"local documentation link in {path}")
            resolved = (PurePosixPath(path).parent / target).as_posix()
            if resolved not in contents:
                raise PackageError(
                    f"packaged documentation link is unresolved: {path} -> {target}"
                )


def prepare_source_manifest(definition: dict, files: list[dict]) -> tuple[dict, dict[str, bytes]]:
    validate_file_entries(files)
    validate_definition_inventory(definition, files)
    contents = {
        entry["path"]: (source_root(entry["path"]) / entry["path"]).read_bytes()
        for entry in files
    }
    validate_document_links(contents)
    pin = validate_pin(definition, contents)
    ledger, ports = validate_semantic_ports(definition, contents)
    esr_review = validate_esr_review_assets(contents)
    conformance = validate_conformance_assets(contents)
    validate_api(definition, contents)
    archive_root = f"{definition['package_name']}-{definition['version']}"
    manifest = {
        "schema_version": 3,
        "package_name": definition["package_name"],
        "version": definition["version"],
        "archive_root": archive_root,
        "source_api_version": definition["source_api_version"],
        "upstream": pin,
        "semantic_port_model": ledger.get("model"),
        "semantic_ports": ports,
        "esr_review": esr_review,
        "conformance": conformance,
        "license": definition["license"],
        "readme": definition["readme"],
        "forbidden_archive_prefixes": definition["forbidden_archive_prefixes"],
        "files": files,
        "aggregate_sha256": aggregate_hash(files),
    }
    return manifest, contents


def tar_info(name: str, mode: int, size: int = 0, directory: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = mode
    info.size = size
    if directory:
        info.type = tarfile.DIRTYPE
    return info


def build_tar(manifest: dict, contents: dict[str, bytes]) -> bytes:
    root = manifest["archive_root"]
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    directories = {root}
    for path in [*contents, GENERATED_MANIFEST]:
        parent = PurePosixPath(root, path).parent
        while parent.as_posix() != ".":
            directories.add(parent.as_posix())
            if parent.as_posix() == root:
                break
            parent = parent.parent

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for directory in sorted(directories):
            archive.addfile(tar_info(f"{directory}/", 0o755, directory=True))
        modes = {entry["path"]: int(entry["mode"], 8) for entry in manifest["files"]}
        for path in sorted(contents):
            content = contents[path]
            archive.addfile(
                tar_info(f"{root}/{path}", modes[path], len(content)),
                io.BytesIO(content),
            )
        archive.addfile(
            tar_info(f"{root}/{GENERATED_MANIFEST}", 0o644, len(manifest_bytes)),
            io.BytesIO(manifest_bytes),
        )
    return buffer.getvalue()


def write_archive(path: Path, tar_content: bytes, force: bool) -> None:
    path = path.resolve()
    if path.exists() and not force:
        raise PackageError(f"refusing to overwrite source package: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with lzma.LZMAFile(
                raw,
                mode="w",
                format=lzma.FORMAT_XZ,
                check=lzma.CHECK_CRC64,
                preset=9 | lzma.PRESET_EXTREME,
            ) as compressed:
                compressed.write(tar_content)
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def safe_member_path(member: tarfile.TarInfo) -> PurePosixPath:
    raw_name = member.name.rstrip("/")
    path = PurePosixPath(raw_name)
    if (
        not raw_name
        or "\\" in raw_name
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != raw_name
    ):
        raise PackageError(f"unsafe archive member: {member.name}")
    if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
        raise PackageError(f"unsupported archive member type: {member.name}")
    if member.uid != 0 or member.gid != 0 or member.mtime != 0:
        raise PackageError(f"nondeterministic archive metadata: {member.name}")
    return path


def verify_archive(path: Path) -> dict:
    path = path.resolve()
    try:
        archive = tarfile.open(path, mode="r:xz")
    except (OSError, tarfile.TarError, lzma.LZMAError) as error:
        raise PackageError(f"cannot open source package {path}: {error}") from error
    with archive:
        members = archive.getmembers()
        if not members:
            raise PackageError("source package is empty")
        member_names = [member.name.rstrip("/") for member in members]
        if len(member_names) != len(set(member_names)):
            raise PackageError("source package has duplicate archive members")
        member_paths = [safe_member_path(member) for member in members]
        roots = {member_path.parts[0] for member_path in member_paths}
        if len(roots) != 1:
            raise PackageError(f"source package has multiple roots: {sorted(roots)}")
        root = next(iter(roots))
        manifest_name = f"{root}/{GENERATED_MANIFEST}"
        manifest_members = [member for member in members if member.name == manifest_name]
        if len(manifest_members) != 1 or not manifest_members[0].isfile():
            raise PackageError(f"source package must contain one {GENERATED_MANIFEST}")
        if manifest_members[0].mode != 0o644:
            raise PackageError(f"{GENERATED_MANIFEST} has an invalid mode")
        manifest_stream = archive.extractfile(manifest_members[0])
        if manifest_stream is None:
            raise PackageError("cannot read generated source manifest")
        manifest = load_json_bytes(manifest_stream.read(), manifest_name)
        common_manifest_keys = {
            "schema_version",
            "package_name",
            "version",
            "archive_root",
            "source_api_version",
            "upstream",
            "semantic_port_model",
            "semantic_ports",
            "license",
            "readme",
            "forbidden_archive_prefixes",
            "files",
            "aggregate_sha256",
        }
        manifest_schema = manifest.get("schema_version")
        required_manifest_keys = set(common_manifest_keys)
        if manifest_schema == 3:
            required_manifest_keys.update({"esr_review", "conformance"})
        elif manifest_schema == 2:
            required_manifest_keys.add("esr_review")
        elif manifest_schema != 1:
            raise PackageError("generated source manifest has an invalid schema")
        if set(manifest) != required_manifest_keys:
            raise PackageError("generated source manifest has an invalid schema")
        if manifest.get("archive_root") != root:
            raise PackageError("generated source manifest has an invalid root/schema")
        if not isinstance(manifest["package_name"], str) or not isinstance(
            manifest["version"], str
        ):
            raise PackageError("generated source manifest has an invalid identity")
        expected_root = f"{manifest['package_name']}-{manifest['version']}"
        if root != expected_root:
            raise PackageError(
                f"archive root differs from generated identity: {root!r}"
            )
        files = validate_file_entries(manifest["files"])
        if manifest.get("aggregate_sha256") != aggregate_hash(files):
            raise PackageError("generated source manifest aggregate hash mismatch")

        regular = {member.name: member for member in members if member.isfile()}
        expected_names = {f"{root}/{entry['path']}" for entry in files}
        expected_names.add(manifest_name)
        if set(regular) != expected_names:
            raise PackageError(
                "archive file inventory differs from generated source manifest: "
                f"extra={sorted(set(regular) - expected_names)}, "
                f"missing={sorted(expected_names - set(regular))}"
            )
        expected_directories = {root}
        for name in expected_names:
            parent = PurePosixPath(name).parent
            while parent.as_posix() != ".":
                expected_directories.add(parent.as_posix())
                if parent.as_posix() == root:
                    break
                parent = parent.parent
        directories = {member.name.rstrip("/"): member for member in members if member.isdir()}
        if set(directories) != expected_directories:
            raise PackageError(
                "archive directory inventory differs from generated source manifest: "
                f"extra={sorted(set(directories) - expected_directories)}, "
                f"missing={sorted(expected_directories - set(directories))}"
            )
        for name, member in directories.items():
            if member.mode != 0o755:
                raise PackageError(f"archive directory has invalid mode: {name}")
        contents: dict[str, bytes] = {}
        for entry in files:
            relative = entry["path"]
            if any(
                relative.startswith(prefix)
                for prefix in manifest.get("forbidden_archive_prefixes", [])
            ):
                raise PackageError(f"forbidden source path in archive: {relative}")
            member = regular[f"{root}/{relative}"]
            stream = archive.extractfile(member)
            if stream is None:
                raise PackageError(f"cannot read archive member: {relative}")
            content = stream.read()
            if len(content) != entry["bytes"] or sha256_bytes(content) != entry["sha256"]:
                raise PackageError(f"source content hash/size mismatch: {relative}")
            if member.mode != int(entry["mode"], 8):
                raise PackageError(f"source mode mismatch: {relative}")
            contents[relative] = content

        definition_path = "config/desktop-embedder-source-package.json"
        definition = load_json_bytes(contents[definition_path], definition_path)
        validate_definition_data(definition)
        validate_definition_inventory(
            definition,
            files,
            require_esr_review=manifest_schema >= 2,
            require_conformance=manifest_schema >= 3,
        )
        validate_document_links(contents)
        if definition.get("package_name") != manifest.get("package_name") or definition.get(
            "version"
        ) != manifest.get("version"):
            raise PackageError("packaged definition differs from generated identity")
        if definition.get("source_api_version") != manifest.get("source_api_version"):
            raise PackageError("packaged definition differs from generated API version")
        if definition.get("forbidden_archive_prefixes") != manifest.get(
            "forbidden_archive_prefixes"
        ):
            raise PackageError("packaged definition differs from generated exclusions")
        if definition.get("license") != manifest.get("license") or definition.get(
            "readme"
        ) != manifest.get("readme"):
            raise PackageError("packaged definition differs from generated documentation")
        pin = validate_pin(definition, contents)
        ledger, ports = validate_semantic_ports(definition, contents)
        esr_review = (
            validate_esr_review_assets(contents) if manifest_schema >= 2 else None
        )
        conformance = (
            validate_conformance_assets(contents) if manifest_schema >= 3 else None
        )
        validate_api(definition, contents)
        if pin != manifest.get("upstream"):
            raise PackageError("generated upstream pin differs from packaged pin")
        if ports != manifest.get("semantic_ports"):
            raise PackageError("generated semantic-port summary differs from ledger")
        if ledger.get("model") != manifest.get("semantic_port_model"):
            raise PackageError("generated semantic-port model differs from ledger")
        if manifest_schema >= 2 and esr_review != manifest.get("esr_review"):
            raise PackageError("generated ESR review summary differs from packaged assets")
        if manifest_schema >= 3 and conformance != manifest.get("conformance"):
            raise PackageError(
                "generated conformance summary differs from packaged assets"
            )

    return {
        "path": path,
        "package_name": manifest["package_name"],
        "version": manifest["version"],
        "source_api_version": manifest["source_api_version"],
        "file_count": len(files),
        "source_bytes": sum(entry["bytes"] for entry in files),
        "archive_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "semantic_port_count": len(manifest["semantic_ports"]),
    }


def print_verification(result: dict) -> None:
    print(
        "Desktop Gecko Embedder source package verified: "
        f"API {result['source_api_version']}, {result['file_count']} files, "
        f"{result['semantic_port_count']} semantic ports, "
        f"{result['source_bytes']} source bytes, {result['archive_bytes']} archive bytes"
    )
    print(f"SHA-256 {result['sha256']}  {result['path']}")


def main() -> int:
    args = parse_args()
    if args.verify and (args.output or args.force or args.check):
        raise PackageError("--verify cannot be combined with build options")
    if args.verify:
        print_verification(verify_archive(args.verify))
        return 0

    definition, _ = validate_definition(args.definition)
    files = collect_files(definition)
    manifest, contents = prepare_source_manifest(definition, files)
    if args.check:
        if args.output or args.force:
            raise PackageError("--check cannot be combined with --output/--force")
        print(
            "Desktop Gecko Embedder source-package definition verified: "
            f"API {manifest['source_api_version']}, {len(files)} files, "
            f"{len(manifest['semantic_ports'])} semantic ports, "
            f"{sum(entry['bytes'] for entry in files)} source bytes"
        )
        return 0

    output = args.output or (
        WORKSPACE
        / "artifacts"
        / f"{definition['package_name']}-{definition['version']}-source.tar.xz"
    )
    write_archive(output, build_tar(manifest, contents), args.force)
    print_verification(verify_archive(output))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackageError as error:
        print(f"Desktop Embedder source packaging failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None
