#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Verify one desktop runtime's generated and native Gecko BuildID closure."""

from __future__ import annotations

import argparse
import configparser
import io
import re
import struct
import sys
import zipfile
from pathlib import Path


BUILD_ID = re.compile(r"^[0-9]{14}$")
APP_CONSTANTS = "modules/AppConstants.sys.mjs"
ELF_MACHINE_X86_64 = 62
PE_MACHINE_AMD64 = 0x8664
MAX_ELF_NOTE_BYTES = 16 * 1024 * 1024
MAX_PE_HEADER_OFFSET = 1024 * 1024


class BuildIDError(RuntimeError):
    pass


def read_exact(stream: io.BufferedReader, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0:
        raise BuildIDError(f"{label} has an invalid file range")
    stream.seek(offset)
    value = stream.read(size)
    if len(value) != size:
        raise BuildIDError(f"{label} is truncated")
    return value


def decode_build_id(value: bytes, label: str) -> str:
    if len(value) != 15 or value[-1:] != b"\0" or not value[:14].isdigit():
        raise BuildIDError(f"{label} contains a malformed Mozilla BuildID")
    return value[:14].decode("ascii")


def inspect_elf_build_id(path: Path) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            header = read_exact(stream, 0, 64, str(path))
            if (
                header[:4] != b"\x7fELF"
                or header[4] != 2
                or header[5] != 1
                or struct.unpack_from("<H", header, 18)[0] != ELF_MACHINE_X86_64
            ):
                raise BuildIDError(
                    f"Linux native library is not little-endian ELF64 x86-64: {path}"
                )
            program_offset = struct.unpack_from("<Q", header, 32)[0]
            entry_size = struct.unpack_from("<H", header, 54)[0]
            entry_count = struct.unpack_from("<H", header, 56)[0]
            if entry_count and entry_size < 56:
                raise BuildIDError(f"ELF program headers are invalid: {path}")
            table_size = entry_size * entry_count
            if program_offset > size or table_size > size - program_offset:
                raise BuildIDError(f"ELF program headers are truncated: {path}")
            programs = read_exact(stream, program_offset, table_size, str(path))
            build_ids: list[str] = []
            for index in range(entry_count):
                entry = index * entry_size
                if struct.unpack_from("<I", programs, entry)[0] != 4:  # PT_NOTE
                    continue
                note_offset = struct.unpack_from("<Q", programs, entry + 8)[0]
                note_size = struct.unpack_from("<Q", programs, entry + 32)[0]
                if (
                    note_offset > size
                    or note_size > size - note_offset
                    or note_size > MAX_ELF_NOTE_BYTES
                ):
                    raise BuildIDError(f"ELF note range is invalid: {path}")
                notes = read_exact(stream, note_offset, note_size, str(path))
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
                        raise BuildIDError(f"ELF note is malformed: {path}")
                    name = notes[cursor:name_end].rstrip(b"\0")
                    if note_type == 1 and name == b"mzbldid":
                        build_ids.append(
                            decode_build_id(
                                notes[description_offset:description_end],
                                f"ELF mzbldid note in {path}",
                            )
                        )
                    cursor = next_note
    except OSError as error:
        raise BuildIDError(f"cannot inspect Linux native library {path}: {error}") from error
    if len(build_ids) != 1:
        raise BuildIDError(
            f"Linux native library must contain exactly one mzbldid note: {path}"
        )
    return build_ids[0]


def inspect_pe_build_id(path: Path) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            dos = read_exact(stream, 0, 64, str(path))
            if dos[:2] != b"MZ":
                raise BuildIDError(f"Windows native library has no DOS header: {path}")
            pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
            if pe_offset < 64 or pe_offset > MAX_PE_HEADER_OFFSET:
                raise BuildIDError(f"Windows PE header offset is invalid: {path}")
            coff = read_exact(stream, pe_offset, 24, str(path))
            if coff[:4] != b"PE\0\0":
                raise BuildIDError(f"Windows native library has no PE header: {path}")
            machine, section_count = struct.unpack_from("<HH", coff, 4)
            optional_size = struct.unpack_from("<H", coff, 20)[0]
            if (
                machine != PE_MACHINE_AMD64
                or not 1 <= section_count <= 96
                or optional_size < 2
            ):
                raise BuildIDError(f"Windows native library is not PE32+ AMD64: {path}")
            optional = read_exact(stream, pe_offset + 24, optional_size, str(path))
            if struct.unpack_from("<H", optional)[0] != 0x20B:
                raise BuildIDError(f"Windows native library is not PE32+ AMD64: {path}")
            section_offset = pe_offset + 24 + optional_size
            section_table_size = section_count * 40
            if section_offset > size or section_table_size > size - section_offset:
                raise BuildIDError(f"Windows PE section table is truncated: {path}")
            sections = read_exact(stream, section_offset, section_table_size, str(path))
            matches: list[tuple[int, int, int]] = []
            for index in range(section_count):
                entry = index * 40
                if sections[entry : entry + 8] != b"mozbldid":
                    continue
                virtual_size = struct.unpack_from("<I", sections, entry + 8)[0]
                raw_size = struct.unpack_from("<I", sections, entry + 16)[0]
                raw_offset = struct.unpack_from("<I", sections, entry + 20)[0]
                matches.append((virtual_size, raw_size, raw_offset))
            if len(matches) != 1:
                raise BuildIDError(
                    f"Windows native library must contain exactly one mozbuildid section: {path}"
                )
            virtual_size, raw_size, raw_offset = matches[0]
            if (
                virtual_size != 15
                or raw_size < virtual_size
                or raw_offset > size
                or raw_size > size - raw_offset
            ):
                raise BuildIDError(f"Windows mozbuildid section is malformed: {path}")
            value = read_exact(stream, raw_offset, virtual_size, str(path))
    except OSError as error:
        raise BuildIDError(f"cannot inspect Windows native library {path}: {error}") from error
    return decode_build_id(value, f"PE mozbuildid section in {path}")


def inspect_ini_build_id(path: Path, section: str) -> str:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, configparser.Error) as error:
        raise BuildIDError(f"cannot read BuildID from {path}: {error}") from error
    if not parser.has_option(section, "BuildID"):
        raise BuildIDError(f"{path} has no [{section}] BuildID")
    value = parser.get(section, "BuildID")
    if not BUILD_ID.fullmatch(value):
        raise BuildIDError(f"{path} contains a malformed BuildID")
    return value


def inspect_omni_build_id(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            source = archive.read(APP_CONSTANTS).decode("utf-8")
    except (OSError, KeyError, UnicodeError, zipfile.BadZipFile) as error:
        raise BuildIDError(f"cannot read {APP_CONSTANTS} from {path}: {error}") from error
    values = re.findall(r'\bMOZ_BUILDID\s*:\s*"([^"\r\n]*)"', source)
    if len(values) != 1:
        raise BuildIDError(f"{path} does not contain exactly one AppConstants MOZ_BUILDID")
    if not BUILD_ID.fullmatch(values[0]):
        raise BuildIDError(f"{path} contains a malformed AppConstants MOZ_BUILDID")
    return values[0]


def verify_runtime(runtime: Path, platform: str, expected: str) -> dict[str, str]:
    if not BUILD_ID.fullmatch(expected):
        raise BuildIDError("expected BuildID must contain 14 digits")
    if not runtime.is_dir() or runtime.is_symlink():
        raise BuildIDError(f"runtime directory is missing or unsafe: {runtime}")
    native_path = runtime / ("libxul.so" if platform == "linux-x86_64" else "xul.dll")
    identities = {
        "application_ini": inspect_ini_build_id(runtime / "application.ini", "App"),
        "platform_ini": inspect_ini_build_id(runtime / "platform.ini", "Build"),
        "app_constants": inspect_omni_build_id(runtime / "omni.ja"),
        "native_toolkit": (
            inspect_elf_build_id(native_path)
            if platform == "linux-x86_64"
            else inspect_pe_build_id(native_path)
        ),
    }
    for surface, actual in identities.items():
        if actual != expected:
            raise BuildIDError(
                f"desktop {surface} BuildID differs: expected {expected}, found {actual}"
            )
    return identities


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument(
        "--platform", choices=("linux-x86_64", "win64"), required=True
    )
    parser.add_argument("--build-id", required=True)
    args = parser.parse_args()
    try:
        identities = verify_runtime(
            args.runtime.resolve(), args.platform, args.build_id
        )
    except BuildIDError as error:
        print(f"Navis desktop BuildID verification failed: {error}", file=sys.stderr)
        return 1
    print(f"Navis {args.platform} BuildID closure verified: {args.build_id}")
    for surface in identities:
        print(f"- {surface}: {identities[surface]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
