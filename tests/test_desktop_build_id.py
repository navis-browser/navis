#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path


VERIFIER = Path(__file__).resolve().parents[1] / "scripts/verify-desktop-build-id.py"
SPEC = importlib.util.spec_from_file_location("verify_desktop_build_id", VERIFIER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def build_note(build_id: str, *, terminated: bool = True) -> bytes:
    name = b"mzbldid\0"
    description = build_id.encode("ascii") + (b"\0" if terminated else b"")
    note = struct.pack("<III", len(name), len(description), 1) + name
    note += description
    return note + b"\0" * (-len(note) % 4)


def build_elf(
    build_id: str,
    *,
    machine: int = MODULE.ELF_MACHINE_X86_64,
    duplicate: bool = False,
    terminated: bool = True,
) -> bytes:
    notes = build_note(build_id, terminated=terminated)
    if duplicate:
        notes += build_note(build_id)
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", header, 18, machine)
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<H", header, 54, 56)
    struct.pack_into("<H", header, 56, 1)
    program = bytearray(56)
    struct.pack_into("<I", program, 0, 4)
    struct.pack_into("<Q", program, 8, 120)
    struct.pack_into("<Q", program, 32, len(notes))
    return bytes(header + program) + notes


def build_pe(
    build_id: str,
    *,
    machine: int = MODULE.PE_MACHINE_AMD64,
    section_name: bytes = b"mozbldid",
    duplicate: bool = False,
    virtual_size: int = 15,
    extra: bytes = b"",
) -> bytes:
    section_count = 2 if duplicate else 1
    pe_offset = 64
    optional = struct.pack("<H", 0x20B)
    section_offset = pe_offset + 24 + len(optional)
    raw_offset = section_offset + section_count * 40
    dos = bytearray(64)
    dos[:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, pe_offset)
    coff = bytearray(24)
    coff[:4] = b"PE\0\0"
    struct.pack_into("<HH", coff, 4, machine, section_count)
    struct.pack_into("<H", coff, 20, len(optional))
    sections = bytearray(section_count * 40)
    for index in range(section_count):
        entry = index * 40
        sections[entry : entry + 8] = section_name.ljust(8, b"\0")
        struct.pack_into("<I", sections, entry + 8, virtual_size)
        struct.pack_into("<I", sections, entry + 16, 32)
        struct.pack_into("<I", sections, entry + 20, raw_offset + index * 32)
    value = build_id.encode("ascii") + b"\0" + b"\0" * 17
    return bytes(dos + coff) + optional + bytes(sections) + value * section_count + extra


class DesktopBuildIDTests(unittest.TestCase):
    build_id = "20260904083000"

    def make_runtime(
        self,
        platform: str,
        *,
        application_id: str | None = None,
        platform_id: str | None = None,
        omni_id: str | None = None,
        native: bytes | None = None,
    ) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        application_id = application_id or self.build_id
        platform_id = platform_id or self.build_id
        omni_id = omni_id or self.build_id
        (root / "application.ini").write_text(
            f"[App]\nName=Navis\nBuildID={application_id}\n", encoding="utf-8"
        )
        (root / "platform.ini").write_text(
            f"[Build]\nBuildID={platform_id}\n", encoding="utf-8"
        )
        with zipfile.ZipFile(root / "omni.ja", "w") as archive:
            archive.writestr(
                MODULE.APP_CONSTANTS,
                f'export const AppConstants = {{ MOZ_BUILDID: "{omni_id}" }};\n',
            )
        if platform == "linux-x86_64":
            (root / "libxul.so").write_bytes(native or build_elf(self.build_id))
        else:
            (root / "xul.dll").write_bytes(
                native
                or build_pe(
                    self.build_id,
                    extra=b"unrelated certificate 20541231235959",
                )
            )
        return root

    def verify(self, platform: str, runtime: Path) -> dict[str, str]:
        return MODULE.verify_runtime(runtime, platform, self.build_id)

    def test_accepts_linux_exact_build_id_closure(self) -> None:
        identities = self.verify("linux-x86_64", self.make_runtime("linux-x86_64"))
        self.assertEqual(set(identities.values()), {self.build_id})

    def test_accepts_windows_structured_section_with_unrelated_date(self) -> None:
        identities = self.verify("win64", self.make_runtime("win64"))
        self.assertEqual(set(identities.values()), {self.build_id})

    def test_rejects_stale_linux_native_build_id(self) -> None:
        runtime = self.make_runtime(
            "linux-x86_64", native=build_elf("20260903070000")
        )
        with self.assertRaisesRegex(MODULE.BuildIDError, "native_toolkit"):
            self.verify("linux-x86_64", runtime)

    def test_rejects_stale_windows_native_build_id(self) -> None:
        runtime = self.make_runtime("win64", native=build_pe("20260903070000"))
        with self.assertRaisesRegex(MODULE.BuildIDError, "native_toolkit"):
            self.verify("win64", runtime)

    def test_rejects_stale_app_constants(self) -> None:
        runtime = self.make_runtime("linux-x86_64", omni_id="20260903070000")
        with self.assertRaisesRegex(MODULE.BuildIDError, "app_constants"):
            self.verify("linux-x86_64", runtime)

    def test_rejects_duplicate_app_constants_even_if_one_is_malformed(self) -> None:
        runtime = self.make_runtime("win64")
        with zipfile.ZipFile(runtime / "omni.ja", "w") as archive:
            archive.writestr(
                MODULE.APP_CONSTANTS,
                f'MOZ_BUILDID: "broken"; MOZ_BUILDID: "{self.build_id}";\n',
            )
        with self.assertRaisesRegex(MODULE.BuildIDError, "exactly one AppConstants"):
            self.verify("win64", runtime)

    def test_rejects_stale_application_ini(self) -> None:
        runtime = self.make_runtime("win64", application_id="20260903070000")
        with self.assertRaisesRegex(MODULE.BuildIDError, "application_ini"):
            self.verify("win64", runtime)

    def test_rejects_stale_platform_ini(self) -> None:
        runtime = self.make_runtime("linux-x86_64", platform_id="20260903070000")
        with self.assertRaisesRegex(MODULE.BuildIDError, "platform_ini"):
            self.verify("linux-x86_64", runtime)

    def test_rejects_duplicate_linux_mozilla_notes(self) -> None:
        runtime = self.make_runtime(
            "linux-x86_64", native=build_elf(self.build_id, duplicate=True)
        )
        with self.assertRaisesRegex(MODULE.BuildIDError, "exactly one mzbldid"):
            self.verify("linux-x86_64", runtime)

    def test_rejects_unterminated_linux_mozilla_note(self) -> None:
        runtime = self.make_runtime(
            "linux-x86_64", native=build_elf(self.build_id, terminated=False)
        )
        with self.assertRaisesRegex(MODULE.BuildIDError, "malformed Mozilla"):
            self.verify("linux-x86_64", runtime)

    def test_rejects_wrong_linux_machine(self) -> None:
        runtime = self.make_runtime(
            "linux-x86_64", native=build_elf(self.build_id, machine=183)
        )
        with self.assertRaisesRegex(MODULE.BuildIDError, "ELF64 x86-64"):
            self.verify("linux-x86_64", runtime)

    def test_rejects_missing_windows_build_id_section(self) -> None:
        runtime = self.make_runtime(
            "win64", native=build_pe(self.build_id, section_name=b"notmoz")
        )
        with self.assertRaisesRegex(MODULE.BuildIDError, "exactly one mozbuildid"):
            self.verify("win64", runtime)

    def test_rejects_duplicate_windows_build_id_sections(self) -> None:
        runtime = self.make_runtime(
            "win64", native=build_pe(self.build_id, duplicate=True)
        )
        with self.assertRaisesRegex(MODULE.BuildIDError, "exactly one mozbuildid"):
            self.verify("win64", runtime)

    def test_rejects_malformed_windows_build_id_section_size(self) -> None:
        runtime = self.make_runtime(
            "win64", native=build_pe(self.build_id, virtual_size=16)
        )
        with self.assertRaisesRegex(MODULE.BuildIDError, "section is malformed"):
            self.verify("win64", runtime)

    def test_rejects_wrong_windows_machine(self) -> None:
        runtime = self.make_runtime(
            "win64", native=build_pe(self.build_id, machine=0x14C)
        )
        with self.assertRaisesRegex(MODULE.BuildIDError, r"PE32\+ AMD64"):
            self.verify("win64", runtime)

    def test_rejects_malformed_expected_build_id(self) -> None:
        runtime = self.make_runtime("linux-x86_64")
        with self.assertRaisesRegex(MODULE.BuildIDError, "expected BuildID"):
            MODULE.verify_runtime(runtime, "linux-x86_64", "latest")


if __name__ == "__main__":
    unittest.main()
