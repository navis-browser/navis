#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


"""Verify and describe an exact-source Navis Android test-candidate APK."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


WORKSPACE = Path(__file__).resolve().parent.parent
EXPECTED_APPLICATION_ID = "org.navis.browser.debug"
EXPECTED_LAUNCHER = "org.navis.browser.LauncherActivity"
EXPECTED_ABI = "arm64-v8a"
EXPECTED_ELF_MACHINE = 183
OMNI_APP_CONSTANTS = "modules/AppConstants.sys.mjs"
OMNI_NAVIS_PREFS = f"defaults/pref/{EXPECTED_ABI}/navis-prefs.js"
NAVIS_PREFS_SOURCE = "../runtime/gecko/mobile/android/app/navis-prefs.js"
LOCKED_BUILTIN_IDS_PREF = (
    'pref("extensions.applicationBuiltins.allowedIds", '
    '"uBlock0@raymondhill.net", locked);'
)
GECKOVIEW_BUILD_CONFIG = "Lorg/mozilla/geckoview/BuildConfig;"
GECKOVIEW_BUILD_ID_FIELD = "MOZ_APP_BUILDID"
FORBIDDEN_GECKOVIEW_LIFECYCLE_CLASSES = (
    "Lorg/mozilla/geckoview/GeckoRuntime;",
    "Lorg/mozilla/geckoview/GeckoSession;",
    "Lorg/mozilla/geckoview/GeckoView;",
    "Lorg/mozilla/geckoview/GeckoDisplay;",
    "Lorg/mozilla/geckoview/SessionAccessibility;",
    "Lorg/mozilla/geckoview/WebExtensionController;",
)
FORBIDDEN_GECKOVIEW_OWNER_TYPE_REFERENCES = (
    *FORBIDDEN_GECKOVIEW_LIFECYCLE_CLASSES,
    "Lorg/mozilla/geckoview/GeckoWebExecutor;",
    "Lorg/mozilla/geckoview/PanZoomController;",
    "Lorg/mozilla/geckoview/SessionTextInput;",
    "Lorg/mozilla/geckoview/WebAuthnTokenManager;",
)
FORBIDDEN_GOOGLE_MOBILE_SERVICE_NAMESPACES = (
    "com.google.android.gms",
    "com.google.firebase",
)
REQUIRED_NATIVE_MEMBERS = (
    "lib/arm64-v8a/libxul.so",
    "lib/arm64-v8a/libmozglue.so",
)
PACKAGED_ONLY_NATIVE_MEMBERS = (
    "lib/arm64-v8a/libandroidx.graphics.path.so",
)
ANDROID_VERSION_CODE_EPOCH = 1_577_836_800
ANDROID_VERSION_CODE_BASE = 100_000_000
UBLOCK_PREFIX = "assets/web_extensions/ublock-origin/"
UBLOCK_PACKAGE = "../platform/gecko-chrome/builtin/ublock-origin/uBlock0@raymondhill.net.xpi"
EXPECTED_UBLOCK_SHA256 = (
    "175756d74468c9ba45863f7fc333d3be670f82d5b066314e915814dd547d1652"
)
# Gecko's generated JNI registration table stores this descriptor verbatim in
# libxul.  Pinning the product seam here prevents Gradle from packaging freshly
# compiled Java/Dex beside a stale native library that still exposes the older
# NativeWindow.open ABI.
NAVIS_NATIVE_WINDOW_OPEN_JNI_SIGNATURE = (
    b"(Lorg/mozilla/gecko/navis/NavisAndroidSession$NativeWindow;"
    b"Lorg/mozilla/gecko/navis/NavisAndroidSession$Compositor;"
    b"Lorg/mozilla/gecko/navis/NavisAndroidInput$NativeProvider;"
    b"Lorg/mozilla/gecko/navis/NavisAndroidTextInput;"
    b"Ljava/lang/Object;Lorg/mozilla/gecko/EventDispatcher;"
    b"JZLjava/lang/String;)V"
)


class PackageError(RuntimeError):
    pass


def matches_owner_descriptor(descriptor: bytes, owner: bytes) -> bool:
    component = descriptor.lstrip(b"[")
    return component == owner or component.startswith(owner[:-1] + b"$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_build_id(value: str) -> int:
    if not re.fullmatch(r"[0-9]{14}", value):
        raise PackageError("build ID must contain 14 UTC digits")
    try:
        instant = dt.datetime.strptime(value, "%Y%m%d%H%M%S").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as error:
        raise PackageError(f"invalid UTC build ID: {value}") from error
    return int(instant.timestamp())


def expected_version_code(epoch_seconds: int) -> int:
    value = ANDROID_VERSION_CODE_BASE + epoch_seconds - ANDROID_VERSION_CODE_EPOCH
    if not 1 <= value <= 2_100_000_000:
        raise PackageError(f"derived Android versionCode is out of range: {value}")
    return value


def run_tool(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        output = getattr(error, "stdout", "") or ""
        raise PackageError(
            f"tool failed: {' '.join(command)}\n{output.strip()}"
        ) from error
    return result.stdout


def parse_aapt_badging(output: str) -> dict[str, Any]:
    package = re.search(
        r"^package: name='([^']+)' versionCode='([0-9]+)' "
        r"versionName='([^']+)'",
        output,
        re.MULTILINE,
    )
    launcher = re.search(
        r"^launchable-activity: name='([^']+)'", output, re.MULTILINE
    )
    minimum = re.search(
        r"^(?:minSdkVersion|sdkVersion):'([0-9]+)'", output, re.MULTILINE
    )
    target = re.search(r"^targetSdkVersion:'([0-9]+)'", output, re.MULTILINE)
    native = re.search(r"^native-code:(.*)$", output, re.MULTILINE)
    if not all((package, launcher, minimum, target, native)):
        raise PackageError(
            "aapt2 badging output lacks package, SDK, launcher or ABI facts"
        )
    assert package is not None
    assert launcher is not None
    assert minimum is not None
    assert target is not None
    assert native is not None
    abis = re.findall(r"'([^']+)'", native.group(1))
    return {
        "application_id": package.group(1),
        "version_code": int(package.group(2)),
        "version_name": package.group(3),
        "launcher": launcher.group(1),
        "min_sdk": int(minimum.group(1)),
        "target_sdk": int(target.group(1)),
        "abis": abis,
    }


def parse_apksigner(output: str) -> dict[str, Any]:
    digest = re.search(
        r"^(?:Signer #1 certificate|V[2-4] Signer: certificate) "
        r"SHA-256 digest: ([0-9a-fA-F]{64})$",
        output,
        re.MULTILINE,
    )
    distinguished_name = re.search(
        r"^(?:Signer #1 certificate|V[2-4] Signer: certificate) DN: (.+)$",
        output,
        re.MULTILINE,
    )
    schemes = [
        match.group(1)
        for match in re.finditer(
            r"^Verified using (v[1-9]) scheme[^:]*: true$", output, re.MULTILINE
        )
    ]
    if not digest or not distinguished_name or not schemes:
        raise PackageError(
            "apksigner output lacks certificate or verified scheme facts"
        )
    if not any(scheme in {"v2", "v3", "v4"} for scheme in schemes):
        raise PackageError("APK is not covered by an APK Signature Scheme v2 or newer")
    if "Android Debug" not in distinguished_name.group(1):
        raise PackageError(
            "test candidate is not signed by an Android debug certificate"
        )
    return {
        "policy": "android-debug-test-candidate",
        "certificate_sha256": digest.group(1).lower(),
        "certificate_dn": distinguished_name.group(1),
        "verified_schemes": schemes,
    }


def safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    names: set[str] = set()
    casefolded: set[str] = set()
    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or path.is_absolute()
            or ".." in path.parts
        ):
            raise PackageError(f"unsafe APK member path: {name!r}")
        mode = member.external_attr >> 16
        if mode and stat.S_ISLNK(mode):
            raise PackageError(f"APK contains a symbolic link: {name}")
        if name in names:
            raise PackageError(f"APK contains a duplicate member: {name}")
        folded = name.casefold()
        if folded in casefolded:
            raise PackageError(f"APK contains a case-colliding member: {name}")
        names.add(name)
        casefolded.add(folded)
    return members


def inspect_static_xre_app_build_id(
    open_stream: Any, size: int, label: str
) -> str:
    """Read StaticXREAppData.buildID from an Android libmozglue ELF.

    Android compiles ``application.ini`` into ``sAppData`` in APKOpen.cpp.
    The symbol is local and stripped from release libraries, but the complete
    StaticXREAppData pointer table remains in a writable PT_LOAD segment.  Bind
    the BuildID to that table layout rather than accepting an arbitrary
    fourteen-digit string elsewhere in the library.
    """

    if size > 64 * 1024 * 1024:
        raise PackageError(f"libmozglue is unreasonably large: {label}")
    with open_stream() as stream:
        data = stream.read(size + 1)
    if len(data) != size:
        raise PackageError(f"native library is truncated: {label}")
    if (
        len(data) < 64
        or data[:4] != b"\x7fELF"
        or data[4] != 2
        or data[5] != 1
        or int.from_bytes(data[18:20], "little") != EXPECTED_ELF_MACHINE
    ):
        raise PackageError(
            f"native library is not little-endian ELF64 AArch64: {label}"
        )

    program_offset = struct.unpack_from("<Q", data, 32)[0]
    program_entry_size = struct.unpack_from("<H", data, 54)[0]
    program_count = struct.unpack_from("<H", data, 56)[0]
    if program_count and program_entry_size < 56:
        raise PackageError(f"native library has invalid ELF program headers: {label}")
    program_size = program_entry_size * program_count
    if program_offset > size or program_size > size - program_offset:
        raise PackageError(f"native library has truncated ELF program headers: {label}")

    load_segments: list[tuple[int, int, int, int, int]] = []
    for index in range(program_count):
        offset = program_offset + index * program_entry_size
        program_type, flags = struct.unpack_from("<II", data, offset)
        if program_type != 1:  # PT_LOAD
            continue
        file_offset, virtual_address = struct.unpack_from("<QQ", data, offset + 8)
        file_size, memory_size = struct.unpack_from("<QQ", data, offset + 32)
        if file_size > memory_size or file_offset > size or file_size > size - file_offset:
            raise PackageError(f"native library has invalid ELF load segments: {label}")
        load_segments.append(
            (file_offset, virtual_address, file_size, memory_size, flags)
        )
    if not load_segments:
        raise PackageError(f"libmozglue has no ELF load segments: {label}")

    def c_string(address: int) -> str | None:
        if not address:
            return None
        for file_offset, virtual_address, file_size, _memory_size, _flags in load_segments:
            if not virtual_address <= address < virtual_address + file_size:
                continue
            start = file_offset + address - virtual_address
            limit = min(file_offset + file_size, start + 4096)
            end = data.find(b"\0", start, limit)
            if end < 0:
                return None
            try:
                value = data[start:end].decode("utf-8")
            except UnicodeError:
                return None
            if any(ord(character) < 0x20 for character in value):
                return None
            return value
        return None

    build_ids: set[str] = set()
    structure_size = 16 * 8
    required_nonempty = (1, 2, 3, 4, 5, 8, 9)
    required_pointers = (10, 15)
    string_slots = (0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15)
    for file_offset, _virtual_address, file_size, _memory_size, flags in load_segments:
        if not flags & 2 or file_size < structure_size:  # PF_W
            continue
        first = (file_offset + 7) & ~7
        last = file_offset + file_size - structure_size
        for offset in range(first, last + 1, 8):
            fields = struct.unpack_from("<16Q", data, offset)
            build_id = c_string(fields[4])
            if build_id is None or not re.fullmatch(r"[0-9]{14}", build_id):
                continue
            try:
                parse_build_id(build_id)
            except PackageError:
                continue
            # Slot seven is a uint32 flags field followed by four bytes of
            # alignment padding in the 64-bit StaticXREAppData layout.
            if fields[7] >> 32:
                continue
            values = {
                index: c_string(fields[index]) if fields[index] else None
                for index in string_slots
            }
            if any(not values[index] for index in required_nonempty):
                continue
            if any(fields[index] == 0 or values[index] is None for index in required_pointers):
                continue
            if any(fields[index] and values[index] is None for index in string_slots):
                continue
            build_ids.add(build_id)

    if not build_ids:
        raise PackageError(
            f"libmozglue lacks a readable StaticXREAppData BuildID: {label}"
        )
    if len(build_ids) != 1:
        raise PackageError(
            f"libmozglue has conflicting StaticXREAppData BuildIDs: {label}"
        )
    return next(iter(build_ids))


class DexReader:
    """Minimal bounds-checked reader for a BuildConfig static string field."""

    def __init__(self, data: bytes, label: str):
        self.data = data
        self.label = label
        self._strings: dict[int, bytes] = {}
        if (
            len(data) < 0x70
            or not re.fullmatch(rb"dex\n[0-9]{3}\0", data[:8])
            or self.u32(36) != 0x70
            or self.u32(40) != 0x12345678
            or self.u32(32) != len(data)
        ):
            raise PackageError(f"APK contains an invalid DEX file: {label}")
        self.string_count = self.u32(56)
        self.string_offset = self.u32(60)
        self.type_count = self.u32(64)
        self.type_offset = self.u32(68)
        self.field_count = self.u32(80)
        self.field_offset = self.u32(84)
        self.class_count = self.u32(96)
        self.class_offset = self.u32(100)
        self.require(self.string_offset, self.string_count * 4)
        self.require(self.type_offset, self.type_count * 4)
        self.require(self.field_offset, self.field_count * 8)
        self.require(self.class_offset, self.class_count * 32)

    def require(self, offset: int, length: int) -> None:
        if offset > len(self.data) or length > len(self.data) - offset:
            raise PackageError(f"APK contains a truncated DEX file: {self.label}")

    def u16(self, offset: int) -> int:
        self.require(offset, 2)
        return struct.unpack_from("<H", self.data, offset)[0]

    def u32(self, offset: int) -> int:
        self.require(offset, 4)
        return struct.unpack_from("<I", self.data, offset)[0]

    def uleb128(self, offset: int) -> tuple[int, int]:
        value = 0
        for index, shift in enumerate(range(0, 35, 7)):
            self.require(offset, 1)
            byte = self.data[offset]
            offset += 1
            if index == 4 and byte & 0xF0:
                raise PackageError(
                    f"APK contains an overflowing DEX ULEB128: {self.label}"
                )
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, offset
        raise PackageError(f"APK contains an invalid DEX ULEB128: {self.label}")

    def string_bytes(self, index: int) -> bytes:
        if index >= self.string_count:
            raise PackageError(f"APK DEX string index is out of range: {self.label}")
        if index in self._strings:
            return self._strings[index]
        offset = self.u32(self.string_offset + index * 4)
        _utf16_length, start = self.uleb128(offset)
        end = self.data.find(b"\0", start)
        if end < 0:
            raise PackageError(f"APK contains an unterminated DEX string: {self.label}")
        value = self.data[start:end]
        self._strings[index] = value
        return value

    def string_ascii(self, index: int) -> str:
        try:
            return self.string_bytes(index).decode("ascii")
        except UnicodeError as error:
            raise PackageError(
                f"APK BuildConfig contains a non-ASCII DEX string: {self.label}"
            ) from error

    def type_descriptor(self, index: int) -> bytes:
        if index >= self.type_count:
            raise PackageError(f"APK DEX type index is out of range: {self.label}")
        return self.string_bytes(self.u32(self.type_offset + index * 4))

    def class_descriptors(self) -> set[bytes]:
        return {
            self.type_descriptor(self.u32(self.class_offset + index * 32))
            for index in range(self.class_count)
        }

    def type_descriptors(self) -> set[bytes]:
        return {self.type_descriptor(index) for index in range(self.type_count)}

    def encoded_value(self, offset: int, depth: int = 0) -> tuple[int, int | None, int]:
        if depth > 64:
            raise PackageError(f"APK DEX encoded value is too deep: {self.label}")
        self.require(offset, 1)
        header = self.data[offset]
        offset += 1
        value_type = header & 0x1F
        value_arg = header >> 5
        scalar_widths = {
            0x00: 0,  # VALUE_BYTE
            0x02: 1,  # VALUE_SHORT
            0x03: 1,  # VALUE_CHAR
            0x04: 3,  # VALUE_INT
            0x06: 7,  # VALUE_LONG
            0x10: 3,  # VALUE_FLOAT
            0x11: 7,  # VALUE_DOUBLE
            0x15: 3,  # VALUE_METHOD_TYPE
            0x16: 3,  # VALUE_METHOD_HANDLE
            0x17: 3,  # VALUE_STRING
            0x18: 3,  # VALUE_TYPE
            0x19: 3,  # VALUE_FIELD
            0x1A: 3,  # VALUE_METHOD
            0x1B: 3,  # VALUE_ENUM
        }
        if value_type in scalar_widths:
            if value_arg > scalar_widths[value_type]:
                raise PackageError(
                    f"APK DEX encoded value has an invalid width: {self.label}"
                )
            width = value_arg + 1
            self.require(offset, width)
            value = int.from_bytes(self.data[offset : offset + width], "little")
            return value_type, value, offset + width
        if value_type == 0x1C and value_arg == 0:  # VALUE_ARRAY
            count, offset = self.uleb128(offset)
            for _ in range(count):
                _type, _value, offset = self.encoded_value(offset, depth + 1)
            return value_type, None, offset
        if value_type == 0x1D and value_arg == 0:  # VALUE_ANNOTATION
            _type_index, offset = self.uleb128(offset)
            count, offset = self.uleb128(offset)
            for _ in range(count):
                _name_index, offset = self.uleb128(offset)
                _type, _value, offset = self.encoded_value(offset, depth + 1)
            return value_type, None, offset
        if value_type == 0x1E and value_arg == 0:  # VALUE_NULL
            return value_type, None, offset
        if value_type == 0x1F and value_arg <= 1:  # VALUE_BOOLEAN
            return value_type, value_arg, offset
        raise PackageError(f"APK contains an invalid DEX encoded value: {self.label}")

    def build_config_id(self) -> str | None:
        matching_classes: list[tuple[int, int]] = []
        for index in range(self.class_count):
            offset = self.class_offset + index * 32
            class_index = self.u32(offset)
            if self.type_descriptor(class_index) == GECKOVIEW_BUILD_CONFIG.encode():
                matching_classes.append((self.u32(offset + 24), self.u32(offset + 28)))
        if not matching_classes:
            return None
        if len(matching_classes) != 1:
            raise PackageError(
                f"APK DEX contains duplicate GeckoView BuildConfig classes: {self.label}"
            )
        class_data_offset, static_values_offset = matching_classes[0]
        if not class_data_offset or not static_values_offset:
            raise PackageError(
                f"APK GeckoView BuildConfig has no static values: {self.label}"
            )

        static_count, cursor = self.uleb128(class_data_offset)
        _instance_count, cursor = self.uleb128(cursor)
        _direct_count, cursor = self.uleb128(cursor)
        _virtual_count, cursor = self.uleb128(cursor)
        static_fields: list[tuple[int, int]] = []
        field_index = 0
        for _ in range(static_count):
            difference, cursor = self.uleb128(cursor)
            access_flags, cursor = self.uleb128(cursor)
            field_index += difference
            if field_index >= self.field_count:
                raise PackageError(
                    f"APK DEX static field index is out of range: {self.label}"
                )
            static_fields.append((field_index, access_flags))

        field_positions: list[tuple[int, int]] = []
        for position, (index, access_flags) in enumerate(static_fields):
            offset = self.field_offset + index * 8
            class_index = self.u16(offset)
            type_index = self.u16(offset + 2)
            name_index = self.u32(offset + 4)
            if (
                self.type_descriptor(class_index) == GECKOVIEW_BUILD_CONFIG.encode()
                and self.type_descriptor(type_index) == b"Ljava/lang/String;"
                and self.string_bytes(name_index) == GECKOVIEW_BUILD_ID_FIELD.encode()
            ):
                field_positions.append((position, access_flags))
        if len(field_positions) != 1:
            raise PackageError(
                f"APK GeckoView BuildConfig lacks one MOZ_APP_BUILDID field: {self.label}"
            )

        field_position, access_flags = field_positions[0]
        if access_flags & 0x18 != 0x18:  # ACC_STATIC | ACC_FINAL
            raise PackageError(
                "APK GeckoView BuildConfig MOZ_APP_BUILDID is not static final: "
                f"{self.label}"
            )

        value_count, cursor = self.uleb128(static_values_offset)
        if value_count > static_count:
            raise PackageError(
                f"APK GeckoView BuildConfig has excess static values: {self.label}"
            )
        if field_position >= value_count:
            raise PackageError(
                f"APK GeckoView BuildConfig MOZ_APP_BUILDID has no value: {self.label}"
            )
        target_string_index: int | None = None
        for position in range(value_count):
            value_type, value, cursor = self.encoded_value(cursor)
            if position != field_position:
                continue
            if value_type != 0x17 or value is None:  # VALUE_STRING
                raise PackageError(
                    f"APK GeckoView BuildConfig MOZ_APP_BUILDID is not a string: {self.label}"
                )
            target_string_index = value
        if target_string_index is None:
            raise AssertionError("DEX encoded array ended before the requested value")
        return self.string_ascii(target_string_index)


def inspect_geckoview_build_id(
    apk: zipfile.ZipFile, dex_members: list[str], expected_build_id: str
) -> tuple[str, str]:
    definitions: list[tuple[str, str]] = []
    for member in dex_members:
        build_id = DexReader(apk.read(member), member).build_config_id()
        if build_id is not None:
            definitions.append((member, build_id))
    if not definitions:
        raise PackageError("APK lacks GeckoView BuildConfig.MOZ_APP_BUILDID")
    if len(definitions) != 1:
        values = {build_id for _member, build_id in definitions}
        detail = "conflicting" if len(values) > 1 else "duplicate"
        raise PackageError(f"APK contains {detail} GeckoView BuildConfig definitions")
    member, build_id = definitions[0]
    if build_id != expected_build_id:
        raise PackageError(
            "APK GeckoView BuildConfig MOZ_APP_BUILDID differs: expected "
            f"{expected_build_id}, found {build_id}"
        )
    return build_id, member


def inspect_geckoview_lifecycle_classes(
    apk: zipfile.ZipFile, dex_members: list[str]
) -> list[str]:
    forbidden = tuple(value.encode("ascii") for value in FORBIDDEN_GECKOVIEW_LIFECYCLE_CLASSES)
    definitions: list[str] = []
    for member in dex_members:
        for descriptor in DexReader(apk.read(member), member).class_descriptors():
            for owner in forbidden:
                if matches_owner_descriptor(descriptor, owner):
                    definitions.append(
                        f"{member}:{descriptor.decode('ascii', errors='backslashreplace')}"
                    )
                    break
    if definitions:
        raise PackageError(
            "APK contains forbidden GeckoView lifecycle classes: "
            + ", ".join(sorted(definitions))
        )
    return definitions


def inspect_geckoview_owner_type_references(
    apk: zipfile.ZipFile, dex_members: list[str]
) -> list[str]:
    forbidden = tuple(
        value.encode("ascii") for value in FORBIDDEN_GECKOVIEW_OWNER_TYPE_REFERENCES
    )
    references: list[str] = []
    for member in dex_members:
        for descriptor in DexReader(apk.read(member), member).type_descriptors():
            for owner in forbidden:
                if matches_owner_descriptor(descriptor, owner):
                    references.append(
                        f"{member}:{descriptor.decode('ascii', errors='backslashreplace')}"
                    )
                    break
    if references:
        raise PackageError(
            "APK contains forbidden GeckoView owner type references: "
            + ", ".join(sorted(references))
        )
    return references


def inspect_google_mobile_services(
    apk: zipfile.ZipFile, dex_members: list[str]
) -> bool:
    manifest = apk.read("AndroidManifest.xml")
    for namespace in FORBIDDEN_GOOGLE_MOBILE_SERVICE_NAMESPACES:
        marker = namespace.encode("ascii")
        if marker in manifest or namespace.encode("utf-16le") in manifest:
            raise PackageError(
                "APK manifest imports Google Mobile Services namespace: "
                f"{namespace}"
            )

    dex_prefixes = tuple(
        ("L" + namespace.replace(".", "/") + "/").encode("ascii")
        for namespace in FORBIDDEN_GOOGLE_MOBILE_SERVICE_NAMESPACES
    )
    for member in dex_members:
        descriptors = DexReader(apk.read(member), member).type_descriptors()
        for descriptor in descriptors:
            component = descriptor.lstrip(b"[")
            for prefix in dex_prefixes:
                if not component.startswith(prefix):
                    continue
                raise PackageError(
                    "APK DEX imports Google Mobile Services namespace: "
                    f"{member}:{component.decode('ascii', errors='backslashreplace')}"
                )
    return False


def inspect_elf(
    open_stream: Any,
    size: int,
    label: str,
    inspect_app_data: bool = False,
    required_sequence: bytes | None = None,
) -> dict[str, Any]:
    sequence_found = required_sequence is None
    sequence_overlap = b""
    overlap_size = len(required_sequence) - 1 if required_sequence else 0

    def inspect_sequence(chunk: bytes) -> None:
        nonlocal sequence_found, sequence_overlap
        if sequence_found or required_sequence is None:
            return
        searchable = sequence_overlap + chunk
        sequence_found = required_sequence in searchable
        if not sequence_found and overlap_size:
            sequence_overlap = searchable[-overlap_size:]

    with open_stream() as stream:
        header = stream.read(64)
        digest = hashlib.sha256()
        digest.update(header)
        inspect_sequence(header)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            inspect_sequence(chunk)
    if (
        len(header) < 64
        or header[:4] != b"\x7fELF"
        or header[4] != 2
        or header[5] != 1
        or int.from_bytes(header[18:20], "little") != EXPECTED_ELF_MACHINE
    ):
        raise PackageError(
            f"native library is not little-endian ELF64 AArch64: {label}"
        )

    program_offset = struct.unpack_from("<Q", header, 32)[0]
    program_entry_size = struct.unpack_from("<H", header, 54)[0]
    program_count = struct.unpack_from("<H", header, 56)[0]
    if program_count:
        if program_entry_size < 56:
            raise PackageError(f"native library has invalid ELF program headers: {label}")
        program_size = program_entry_size * program_count
        if program_offset > size or program_size > size - program_offset:
            raise PackageError(f"native library has truncated ELF program headers: {label}")
        with open_stream() as stream:
            stream.seek(program_offset)
            programs = stream.read(program_size)
        if len(programs) != program_size:
            raise PackageError(f"native library has truncated ELF program headers: {label}")
    else:
        programs = b""

    build_ids: set[str] = set()
    moz_build_ids: set[str] = set()
    for index in range(program_count):
        offset = index * program_entry_size
        program_type = struct.unpack_from("<I", programs, offset)[0]
        if program_type != 4:  # PT_NOTE
            continue
        note_offset = struct.unpack_from("<Q", programs, offset + 8)[0]
        note_size = struct.unpack_from("<Q", programs, offset + 32)[0]
        if note_offset > size or note_size > size - note_offset:
            raise PackageError(f"native library has a truncated ELF note: {label}")
        if note_size > 16 * 1024 * 1024:
            raise PackageError(f"native library has an unreasonable ELF note: {label}")
        with open_stream() as stream:
            stream.seek(note_offset)
            notes = stream.read(note_size)
        cursor = 0
        while cursor + 12 <= len(notes):
            name_size, description_size, note_type = struct.unpack_from(
                "<III", notes, cursor
            )
            cursor += 12
            name_end = cursor + name_size
            description_offset = (name_end + 3) & ~3
            description_end = description_offset + description_size
            next_note = (description_end + 3) & ~3
            if next_note > len(notes):
                raise PackageError(f"native library has a malformed ELF note: {label}")
            name = notes[cursor:name_end].rstrip(b"\0")
            description = notes[description_offset:description_end]
            if note_type == 3 and name == b"GNU" and description:
                build_ids.add(description.hex())
            if note_type == 1 and name == b"mzbldid":
                try:
                    moz_build_id = description.rstrip(b"\0").decode("ascii")
                except UnicodeError as error:
                    raise PackageError(
                        f"native library has a malformed Mozilla BuildID: {label}"
                    ) from error
                if not re.fullmatch(r"[0-9]{14}", moz_build_id):
                    raise PackageError(
                        f"native library has a malformed Mozilla BuildID: {label}"
                    )
                moz_build_ids.add(moz_build_id)
            cursor = next_note
    if len(build_ids) > 1:
        raise PackageError(f"native library has conflicting GNU Build IDs: {label}")
    if len(moz_build_ids) > 1:
        raise PackageError(
            f"native library has conflicting Mozilla BuildIDs: {label}"
        )
    result = {
        "size": size,
        "sha256": digest.hexdigest(),
        "gnu_build_id": next(iter(build_ids), None),
        "moz_build_id": next(iter(moz_build_ids), None),
        "app_build_id": None,
    }
    if required_sequence is not None:
        result["required_sequence_found"] = sequence_found
    if inspect_app_data:
        result["app_build_id"] = inspect_static_xre_app_build_id(
            open_stream, size, label
        )
    return result


def inspect_apk_elf(
    archive: zipfile.ZipFile, member: str, info: zipfile.ZipInfo
) -> dict[str, Any]:
    required_sequence = (
        NAVIS_NATIVE_WINDOW_OPEN_JNI_SIGNATURE
        if member == "lib/arm64-v8a/libxul.so"
        else None
    )
    return inspect_elf(
        lambda: archive.open(member),
        info.file_size,
        member,
        inspect_app_data=PurePosixPath(member).name == "libmozglue.so",
        required_sequence=required_sequence,
    )


def inspect_file_elf(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
        return inspect_elf(
            lambda: path.open("rb"),
            size,
            str(path),
            inspect_app_data=path.name == "libmozglue.so",
        )
    except OSError as error:
        raise PackageError(f"cannot inspect staged native library {path}: {error}") from error


def inspect_runtime_assets(
    workspace: Path, apk: zipfile.ZipFile, expected_build_id: str
) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(apk.read("assets/omni.ja"))) as omni:
            source = omni.read(OMNI_APP_CONSTANTS).decode("utf-8")
            packaged_prefs = omni.read(OMNI_NAVIS_PREFS)
    except (KeyError, UnicodeError, zipfile.BadZipFile) as error:
        raise PackageError(
            "APK omni.ja lacks readable Navis runtime assets: "
            f"{error}"
        ) from error
    build_ids = re.findall(r'\bMOZ_BUILDID:\s*"([0-9]{14})"', source)
    if len(build_ids) != 1:
        raise PackageError(
            "APK runtime AppConstants does not contain exactly one MOZ_BUILDID"
        )
    runtime_build_id = build_ids[0]
    if runtime_build_id != expected_build_id:
        raise PackageError(
            f"APK runtime MOZ_BUILDID differs: expected {expected_build_id}, "
            f"found {runtime_build_id}"
        )
    prefs_path = workspace / NAVIS_PREFS_SOURCE
    try:
        source_prefs = prefs_path.read_bytes()
    except OSError as error:
        raise PackageError(
            f"cannot read Android Navis defaults source {prefs_path}: {error}"
        ) from error
    if packaged_prefs != source_prefs:
        raise PackageError(
            "APK Navis defaults differ from gecko/mobile/android/app/navis-prefs.js"
        )
    try:
        prefs_text = packaged_prefs.decode("utf-8")
    except UnicodeError as error:
        raise PackageError("APK Navis defaults are not valid UTF-8") from error
    if LOCKED_BUILTIN_IDS_PREF not in prefs_text:
        raise PackageError(
            "APK Navis defaults lack the locked built-in extension registry"
        )
    return {
        "build_id": runtime_build_id,
        "navis_defaults": {
            "member": OMNI_NAVIS_PREFS,
            "size": len(packaged_prefs),
            "sha256": hashlib.sha256(packaged_prefs).hexdigest(),
            "locked_builtin_registry": True,
        },
    }


def verify_ublock_assets(
    workspace: Path, apk: zipfile.ZipFile, apk_names: set[str]
) -> dict[str, Any]:
    package_path = workspace / UBLOCK_PACKAGE
    if not package_path.is_file():
        raise PackageError(
            f"required uBlock Origin source package is missing: {package_path}"
        )
    package_sha256 = sha256_file(package_path)
    if package_sha256 != EXPECTED_UBLOCK_SHA256:
        raise PackageError(
            f"required uBlock Origin source package hash differs: {package_sha256}"
        )
    with zipfile.ZipFile(package_path) as source:
        source_names = {
            item.filename for item in source.infolist() if not item.is_dir()
        }
        packaged_names = {
            name.removeprefix(UBLOCK_PREFIX)
            for name in apk_names
            if name.startswith(UBLOCK_PREFIX) and not name.endswith("/")
        }
        if packaged_names != source_names:
            missing = sorted(source_names - packaged_names)
            extra = sorted(packaged_names - source_names)
            raise PackageError(
                "packaged uBlock Origin asset inventory differs: "
                f"missing={missing[:5]}, extra={extra[:5]}"
            )
        for name in sorted(source_names):
            if apk.read(f"{UBLOCK_PREFIX}{name}") != source.read(name):
                raise PackageError(f"packaged uBlock Origin asset differs: {name}")
    return {
        "source_package": UBLOCK_PACKAGE,
        "source_sha256": package_sha256,
        "file_count": len(source_names),
    }


def verify_dependency_notices(workspace: Path, archive: zipfile.ZipFile) -> dict[str, Any]:
    prefix = "assets/navis-licenses/"
    try:
        text_info = archive.getinfo(prefix + "android-dependencies.txt")
        manifest_info = archive.getinfo(prefix + "android-dependencies.json")
        if max(text_info.file_size, manifest_info.file_size) > 4 * 1024 * 1024:
            raise PackageError("Android license report exceeds the reader bound")
        text = archive.read(text_info)
        report = json.loads(archive.read(manifest_info))
        policy = json.loads((workspace / "../platform/android/licenses/dependencies.json").read_text())
        if report.get("schema") != "navis-android-license-report-v1" or report.get("variant") != "debug":
            raise PackageError("Android candidate license report schema/variant differs")
        if hashlib.sha256(text).hexdigest() != report.get("text_sha256"):
            raise PackageError("Android license report content hash differs")
        seen = set()
        components = report.get("components")
        if not isinstance(components, list) or not components:
            raise PackageError("Android license report has no dependency inventory")
        for component in components:
            key = component["coordinate"] + "@" + Path(component["artifact"]).suffix.lstrip(".")
            reviewed = policy["artifacts"].get(key)
            if key in seen or reviewed is None:
                raise PackageError("Android license report has duplicate/unreviewed dependencies")
            seen.add(key)
            if any(component.get(field) != reviewed[field] for field in ("sha256", "licenses", "source")):
                raise PackageError(f"Android license report differs from reviewed metadata: {key}")
            if key.encode() not in text:
                raise PackageError(f"Android dependency omitted from readable notices: {key}")
        if seen != set(policy.get("variants", {}).get("debug", [])):
            raise PackageError("Android license report dependency set differs from reviewed variant")
        for license_id in {item for c in components for item in c["licenses"]}:
            license_data = policy["licenses"][license_id]
            canonical = (workspace / "../platform/android/licenses" / license_data["file"]).read_bytes()
            if hashlib.sha256(canonical).hexdigest() != license_data["sha256"] or canonical not in text:
                raise PackageError(f"Android license text missing or changed: {license_id}")
        return {"components": len(seen), "text_sha256": report["text_sha256"]}
    except (KeyError, ValueError, TypeError, OSError) as error:
        raise PackageError(f"Android dependency license report is missing or invalid: {error}") from error


def inspect_apk(
    workspace: Path, apk_path: Path, expected_build_id: str
) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(apk_path) as archive:
            members = safe_members(archive)
            names = {member.filename for member in members}
            dependency_notices = verify_dependency_notices(workspace, archive)
            for required in (
                "AndroidManifest.xml",
                "assets/omni.ja",
                f"{UBLOCK_PREFIX}manifest.json",
            ):
                if required not in names:
                    raise PackageError(f"APK lacks required member: {required}")
            dex_like_members = sorted(name for name in names if name.endswith(".dex"))
            dex_members = sorted(
                name
                for name in dex_like_members
                if re.fullmatch(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex", name)
            )
            if "classes.dex" not in dex_members:
                raise PackageError("APK lacks primary classes.dex bytecode")
            if dex_members != dex_like_members:
                invalid = sorted(set(dex_like_members) - set(dex_members))
                raise PackageError(f"APK has non-canonical DEX members: {invalid}")
            native_members = sorted(
                name
                for name in names
                if name.startswith("lib/") and name.endswith(".so")
            )
            if not native_members:
                raise PackageError("APK contains no native libraries")
            native_abis = sorted(
                {PurePosixPath(name).parts[1] for name in native_members}
            )
            if native_abis != [EXPECTED_ABI]:
                raise PackageError(f"APK native ABI inventory differs: {native_abis}")
            for required in REQUIRED_NATIVE_MEMBERS + PACKAGED_ONLY_NATIVE_MEMBERS:
                if required not in names:
                    raise PackageError(f"APK lacks required member: {required}")
            member_info = {member.filename: member for member in members}
            native_libraries = {}
            for member in native_members:
                native_libraries[member] = inspect_apk_elf(
                    archive, member, member_info[member]
                )
            for required in REQUIRED_NATIVE_MEMBERS + PACKAGED_ONLY_NATIVE_MEMBERS:
                if not native_libraries[required]["gnu_build_id"]:
                    raise PackageError(
                        f"APK required native library lacks a GNU Build ID: {required}"
                    )
            native_runtime_build_id = native_libraries[
                "lib/arm64-v8a/libxul.so"
            ]["moz_build_id"]
            if native_runtime_build_id != expected_build_id:
                raise PackageError(
                    "APK libxul Mozilla BuildID differs: expected "
                    f"{expected_build_id}, found {native_runtime_build_id or 'missing'}"
                )
            native_application_build_id = native_libraries[
                "lib/arm64-v8a/libmozglue.so"
            ]["app_build_id"]
            if native_application_build_id != expected_build_id:
                raise PackageError(
                    "APK libmozglue StaticXREAppData BuildID differs: expected "
                    f"{expected_build_id}, found "
                    f"{native_application_build_id or 'missing'}"
                )
            if not native_libraries["lib/arm64-v8a/libxul.so"][
                "required_sequence_found"
            ]:
                raise PackageError(
                    "APK libxul lacks the current "
                    "NavisAndroidSession.NativeWindow.open JNI signature"
                )
            geckoview_build_id, geckoview_build_config_dex = (
                inspect_geckoview_build_id(archive, dex_members, expected_build_id)
            )
            geckoview_lifecycle_classes = inspect_geckoview_lifecycle_classes(
                archive, dex_members
            )
            geckoview_owner_type_references = (
                inspect_geckoview_owner_type_references(archive, dex_members)
            )
            google_mobile_services = inspect_google_mobile_services(
                archive, dex_members
            )
            runtime_assets = inspect_runtime_assets(
                workspace, archive, expected_build_id
            )
            runtime_build_id = runtime_assets["build_id"]
            ublock = verify_ublock_assets(workspace, archive, names)
    except (OSError, zipfile.BadZipFile) as error:
        raise PackageError(f"cannot inspect APK {apk_path}: {error}") from error
    return {
        "member_count": len(members),
        "native_library_count": len(native_members),
        "native_abis": native_abis,
        "runtime_build_id": runtime_build_id,
        "runtime_assets": runtime_assets["navis_defaults"],
        "native_runtime_build_id": native_runtime_build_id,
        "native_application_build_id": native_application_build_id,
        "geckoview_build_id": geckoview_build_id,
        "geckoview_build_config_dex": geckoview_build_config_dex,
        "geckoview_lifecycle_classes": geckoview_lifecycle_classes,
        "geckoview_owner_type_references": geckoview_owner_type_references,
        "google_mobile_services": google_mobile_services,
        "native_libraries": native_libraries,
        "dependency_notices": dependency_notices,
        "ublock_origin": ublock,
    }


def verify_staged_native_libraries(
    native_dir: Path, native_libraries: dict[str, dict[str, Any]]
) -> int:
    if not native_dir.is_dir():
        raise PackageError(f"staged native library directory is missing: {native_dir}")
    if native_dir.is_symlink():
        raise PackageError(f"staged native library directory is a symlink: {native_dir}")
    if native_dir.name == EXPECTED_ABI:
        abi_dir = native_dir
    else:
        abi_dir = native_dir / EXPECTED_ABI
        if not abi_dir.is_dir() or abi_dir.is_symlink():
            raise PackageError(
                f"staged native ABI directory is missing: {abi_dir}"
            )

    staged_paths = sorted(native_dir.rglob("*.so"))
    if not staged_paths:
        raise PackageError(f"staged native library directory is empty: {native_dir}")
    staged_members: dict[str, Path] = {}
    for staged_path in staged_paths:
        if staged_path.is_symlink() or not staged_path.is_file():
            raise PackageError(
                f"staged native library is not a regular file: {staged_path}"
            )
        try:
            relative = staged_path.relative_to(abi_dir)
        except ValueError as error:
            raise PackageError(
                f"staged native library has an unexpected path: {staged_path}"
            ) from error
        if len(relative.parts) != 1:
            raise PackageError(
                f"staged native library has an unexpected path: {staged_path}"
            )
        member = f"lib/{EXPECTED_ABI}/{staged_path.name}"
        if member in PACKAGED_ONLY_NATIVE_MEMBERS:
            raise PackageError(
                f"packaged-only native library is present in staging: {staged_path}"
            )
        if member in staged_members:
            raise PackageError(
                f"staged native library is duplicated: {staged_path.name}"
            )
        staged_members[member] = staged_path

    for member in REQUIRED_NATIVE_MEMBERS:
        if member not in staged_members:
            raise PackageError(
                "staged native library is missing: "
                f"{PurePosixPath(member).name}"
            )

    expected_packaged_members = set(staged_members) | set(
        PACKAGED_ONLY_NATIVE_MEMBERS
    )
    packaged_members = set(native_libraries)
    if packaged_members != expected_packaged_members:
        missing = sorted(expected_packaged_members - packaged_members)
        extra = sorted(packaged_members - expected_packaged_members)
        raise PackageError(
            "APK native library inventory differs from staged output and "
            f"packaged-only allowlist: missing={missing}, extra={extra}"
        )

    for member, staged_path in sorted(staged_members.items()):
        if member not in native_libraries:
            raise PackageError(
                f"staged native library is absent from the APK: {staged_path.name}"
            )
        staged = inspect_file_elf(staged_path)
        packaged = native_libraries[member]
        if member in REQUIRED_NATIVE_MEMBERS and not staged["gnu_build_id"]:
            raise PackageError(
                f"staged native library lacks a GNU Build ID: {staged_path}"
            )
        for field in (
            "size",
            "sha256",
            "gnu_build_id",
            "moz_build_id",
            "app_build_id",
        ):
            if packaged[field] != staged[field]:
                raise PackageError(
                    f"APK native library {field} differs from staged output: {member}"
                )
    return len(staged_members)


def load_source_freeze(workspace: Path, path: Path) -> dict[str, Any]:
    verifier_path = workspace / "scripts/verify-source-freeze.py"
    spec = importlib.util.spec_from_file_location(
        "navis_source_freeze", verifier_path
    )
    if spec is None or spec.loader is None:
        raise PackageError("cannot load source-freeze verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        manifest = module.verify(workspace, path)
    except module.FreezeError as error:
        raise PackageError(
            f"source freeze does not match the package source: {error}"
        ) from error
    android_paths = [
        item["path"]
        for item in manifest["files"]
        if item["path"].startswith("platform/android/")
    ]
    if not android_paths:
        raise PackageError("source freeze contains no Android source")
    return {
        "manifest": path.name,
        "manifest_sha256": sha256_file(path),
        "aggregate_sha256": manifest["aggregate_sha256"],
        "file_count": manifest["file_count"],
        "android_file_count": len(android_paths),
        "gecko_commit": manifest["upstream"].get("commit"),
        "semantic_port_count": manifest["semantic_port_count"],
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--source-freeze", type=Path, required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--aapt2", type=Path, required=True)
    parser.add_argument("--apksigner", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--artifact-name")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument("--acceptance-api", type=int, default=34)
    return parser.parse_args()


def verify(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace.resolve()
    apk = args.apk.resolve()
    if not apk.is_file() or apk.is_symlink():
        raise PackageError(f"APK is not a regular file: {apk}")
    epoch_seconds = parse_build_id(args.build_id)
    derived_code = expected_version_code(epoch_seconds)
    if args.version_code != derived_code:
        raise PackageError(
            f"versionCode {args.version_code} does not match build ID "
            f"derivation {derived_code}"
        )
    base_version = (workspace / "../platform/gecko-chrome/config/version.txt").read_text(
        encoding="utf-8"
    ).strip()
    display_version = (
        workspace / "../platform/gecko-chrome/config/version_display.txt"
    ).read_text(encoding="utf-8").strip()
    badging = parse_aapt_badging(
        run_tool([str(args.aapt2), "dump", "badging", str(apk)])
    )
    if badging["application_id"] != EXPECTED_APPLICATION_ID:
        raise PackageError(
            f"Android application ID differs: {badging['application_id']}"
        )
    if badging["version_name"] != display_version:
        raise PackageError(f"Android versionName differs: {badging['version_name']}")
    if badging["version_code"] != args.version_code:
        raise PackageError(f"Android versionCode differs: {badging['version_code']}")
    if badging["launcher"] != EXPECTED_LAUNCHER:
        raise PackageError(f"Android launcher differs: {badging['launcher']}")
    if badging["abis"] != [EXPECTED_ABI]:
        raise PackageError(
            f"Android badging ABI inventory differs: {badging['abis']}"
        )
    if badging["min_sdk"] > args.acceptance_api:
        raise PackageError(
            f"APK minSdk {badging['min_sdk']} exceeds acceptance API "
            f"{args.acceptance_api}"
        )
    signing = parse_apksigner(
        run_tool(
            [str(args.apksigner), "verify", "--verbose", "--print-certs", str(apk)]
        )
    )
    inventory = inspect_apk(workspace, apk, args.build_id)
    staged_native_count = verify_staged_native_libraries(
        args.native_dir.resolve(), inventory["native_libraries"]
    )
    inventory["staged_native_match"] = True
    inventory["staged_native_library_count"] = staged_native_count
    source_freeze = load_source_freeze(workspace, args.source_freeze.resolve())
    artifact_name = args.artifact_name or apk.name
    expected_name = (
        f"navis-{display_version}-android-{EXPECTED_ABI}-test-candidate-"
        f"{args.build_id}.apk"
    )
    if artifact_name != expected_name:
        raise PackageError(
            f"Android candidate artifact name differs: expected {expected_name}, "
            f"found {artifact_name}"
        )
    return {
        "schema_version": 1,
        "kind": "navis-android-test-candidate",
        "build_id": args.build_id,
        "source_date_epoch": epoch_seconds,
        "product": {
            "base_version": base_version,
            "display_version": display_version,
        },
        "android": badging,
        "artifact": {
            "name": artifact_name,
            "size": apk.stat().st_size,
            "sha256": sha256_file(apk),
        },
        "signing": signing,
        "source_freeze": source_freeze,
        "inventory": inventory,
    }


def main() -> int:
    args = parse_args()
    try:
        report = verify(args)
        if args.json_output:
            atomic_json(args.json_output.resolve(), report)
        print("Navis Android package verification passed.")
        print(f"- artifact: {report['artifact']['name']}")
        print(f"- SHA-256: {report['artifact']['sha256']}")
        print(f"- ABI: {', '.join(report['android']['abis'])}")
        print(f"- signer: {report['signing']['certificate_sha256']}")
        return 0
    except (OSError, UnicodeError, PackageError) as error:
        print(f"Navis Android package verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
