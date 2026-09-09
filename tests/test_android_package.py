#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


SOURCE_ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = SOURCE_ROOT / "scripts/verify-android-package.py"
SPEC = importlib.util.spec_from_file_location("verify_android_package", VERIFIER_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


AAPT_OUTPUT = """\
package: name='org.navis.browser.debug' versionCode='310390400' versionName='0.2.0-dev'
minSdkVersion:'26'
targetSdkVersion:'36'
application-label:'Navis'
launchable-activity: name='org.navis.browser.LauncherActivity'  label='Navis' icon=''
native-code: 'arm64-v8a'
"""

SIGNER_OUTPUT = (
    "Verifies\n"
    "Verified using v1 scheme (JAR signing): false\n"
    "Verified using v2 scheme (APK Signature Scheme v2): true\n"
    "Verified using v3 scheme (APK Signature Scheme v3): true\n"
    "V2 Signer: certificate DN: C=US, O=Android, CN=Android Debug\n"
    "V2 Signer: certificate SHA-256 digest: " + "a" * 64 + "\n"
)


def elf_note(name: bytes, description: bytes, note_type: int) -> bytes:
    note = struct.pack("<III", len(name), len(description), note_type)
    note += name + b"\0" * ((-len(name)) % 4)
    note += description + b"\0" * ((-len(description)) % 4)
    return note


def elf_aarch64(
    gnu_build_id: bytes | None,
    moz_build_id: str | None = None,
    app_build_ids: list[str] | None = None,
) -> bytes:
    app_build_ids = app_build_ids or []
    notes = b""
    if gnu_build_id is not None:
        notes += elf_note(b"GNU\0", gnu_build_id, 3)
    if moz_build_id is not None:
        notes += elf_note(b"mzbldid\0", moz_build_id.encode("ascii") + b"\0", 1)

    program_count = (1 if notes else 0) + (2 if app_build_ids else 0)
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[6] = 1
    struct.pack_into("<H", header, 16, 3)
    header[18:20] = (183).to_bytes(2, "little")
    struct.pack_into("<I", header, 20, 1)
    struct.pack_into("<H", header, 52, 64)
    if not program_count:
        return bytes(header)
    program_offset = len(header)
    struct.pack_into("<Q", header, 32, program_offset)
    struct.pack_into("<H", header, 54, 56)
    struct.pack_into("<H", header, 56, program_count)

    cursor = program_offset + program_count * 56
    programs: list[bytes] = []
    chunks: list[tuple[int, bytes]] = []
    if notes:
        note_offset = cursor
        programs.append(
            struct.pack(
                "<IIQQQQQQ",
                4,
                4,
                note_offset,
                0,
                0,
                len(notes),
                len(notes),
                4,
            )
        )
        chunks.append((note_offset, notes))
        cursor += len(notes)

    if app_build_ids:
        read_offset = (cursor + 7) & ~7
        read_address = 0x10000
        strings = bytearray()
        string_offsets: dict[str, int] = {}

        def intern(value: str) -> int:
            if value not in string_offsets:
                string_offsets[value] = len(strings)
                strings.extend(value.encode("utf-8") + b"\0")
            return read_address + string_offsets[value]

        structures = bytearray()
        for build_id in app_build_ids:
            fields = (
                intern("Mozilla"),
                intern("Fennec"),
                intern("fennec-default"),
                intern("0.2.0"),
                intern(build_id),
                intern("{3f72b8df-6865-4f75-9f26-fd8e39e954de}"),
                0,
                0,
                intern("0.2.0"),
                intern("0.2.0"),
                intern(""),
                0,
                0,
                0,
                0,
                intern(""),
            )
            structures.extend(struct.pack("<16Q", *fields))

        write_offset = (read_offset + len(strings) + 7) & ~7
        write_address = 0x20000
        programs.extend(
            [
                struct.pack(
                    "<IIQQQQQQ",
                    1,
                    4,
                    read_offset,
                    read_address,
                    read_address,
                    len(strings),
                    len(strings),
                    8,
                ),
                struct.pack(
                    "<IIQQQQQQ",
                    1,
                    6,
                    write_offset,
                    write_address,
                    write_address,
                    len(structures),
                    len(structures),
                    8,
                ),
            ]
        )
        chunks.extend([(read_offset, bytes(strings)), (write_offset, bytes(structures))])

    output = bytearray(header + b"".join(programs))
    for offset, chunk in chunks:
        if len(output) < offset:
            output.extend(b"\0" * (offset - len(output)))
        output.extend(chunk)
    return bytes(output)


def uleb128(value: int) -> bytes:
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        output.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(output)


def dex_build_config(
    build_id: str,
    extra_strings: tuple[str, ...] = (),
    *,
    class_descriptor: str = VERIFY.GECKOVIEW_BUILD_CONFIG,
    field_class_descriptor: str | None = None,
    field_type_descriptor: str = "Ljava/lang/String;",
    access_flags: int = 0x19,
    target_value: bytes | None = None,
    trailing_value: bytes | None = None,
    include_trailing_field: bool = False,
    value_count: int | None = None,
) -> bytes:
    strings: list[str] = []

    def intern(value: str) -> int:
        if value not in strings:
            strings.append(value)
        return strings.index(value)

    class_string_index = intern(class_descriptor)
    object_string_index = intern("Ljava/lang/Object;")
    field_type_string_index = intern(field_type_descriptor)
    field_class_string_index = intern(field_class_descriptor or class_descriptor)
    field_name_string_index = intern(VERIFY.GECKOVIEW_BUILD_ID_FIELD)
    build_id_string_index = intern(build_id)
    trailing_name_string_index = intern("ZZZ_TRAILING_FIELD")
    for value in extra_strings:
        intern(value)

    type_string_indices = [
        class_string_index,
        object_string_index,
        field_type_string_index,
    ]
    if field_class_string_index not in type_string_indices:
        type_string_indices.append(field_class_string_index)
    field_class_type_index = type_string_indices.index(field_class_string_index)
    field_type_index = type_string_indices.index(field_type_string_index)

    header_size = 0x70
    string_ids_offset = header_size
    type_ids_offset = string_ids_offset + len(strings) * 4
    field_ids_offset = type_ids_offset + len(type_string_indices) * 4
    field_count = 2 if include_trailing_field else 1
    class_defs_offset = field_ids_offset + field_count * 8
    data_offset = class_defs_offset + 32

    string_data = bytearray()
    string_offsets: list[int] = []
    for value in strings:
        string_offsets.append(data_offset + len(string_data))
        string_data.extend(uleb128(len(value)))
        string_data.extend(value.encode("utf-8") + b"\0")
    class_data_offset = data_offset + len(string_data)
    static_count = 2 if include_trailing_field else 1
    class_data = (
        uleb128(static_count)
        + b"\x00\x00\x00"
        + b"\x00"
        + uleb128(access_flags)
    )
    if include_trailing_field:
        class_data += b"\x01" + uleb128(0x19)
    static_values_offset = class_data_offset + len(class_data)
    encoded_build_id = (
        bytes([0x17, build_id_string_index])
        if target_value is None
        else target_value
    )
    encoded_values = encoded_build_id + (trailing_value or b"")
    if value_count is None:
        value_count = 2 if trailing_value is not None else 1
    static_values = uleb128(value_count) + encoded_values
    file_size = static_values_offset + len(static_values)

    header = bytearray(header_size)
    header[:8] = b"dex\n035\0"
    struct.pack_into("<I", header, 32, file_size)
    struct.pack_into("<I", header, 36, header_size)
    struct.pack_into("<I", header, 40, 0x12345678)
    struct.pack_into("<II", header, 56, len(strings), string_ids_offset)
    struct.pack_into("<II", header, 64, len(type_string_indices), type_ids_offset)
    struct.pack_into("<II", header, 80, field_count, field_ids_offset)
    struct.pack_into("<II", header, 96, 1, class_defs_offset)
    struct.pack_into("<II", header, 104, file_size - data_offset, data_offset)
    string_ids = b"".join(struct.pack("<I", value) for value in string_offsets)
    type_ids = b"".join(struct.pack("<I", value) for value in type_string_indices)
    field_ids = struct.pack(
        "<HHI", field_class_type_index, field_type_index, field_name_string_index
    )
    if include_trailing_field:
        field_ids += struct.pack(
            "<HHI", 0, field_type_index, trailing_name_string_index
        )
    class_def = struct.pack(
        "<IIIIIIII",
        0,
        1,
        1,
        0,
        0xFFFFFFFF,
        0,
        class_data_offset,
        static_values_offset,
    )
    return bytes(
        header
        + string_ids
        + type_ids
        + field_ids
        + class_def
        + string_data
        + class_data
        + static_values
    )


def omni(build_id: str, navis_prefs: bytes | None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            VERIFY.OMNI_APP_CONSTANTS,
            f'export const AppConstants = {{ MOZ_BUILDID: "{build_id}", }};\n',
        )
        if navis_prefs is not None:
            archive.writestr(VERIFY.OMNI_NAVIS_PREFS, navis_prefs)
    return output.getvalue()


class AndroidPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "navis"
        self.root.mkdir()
        license_dir = self.root / "../platform/android/licenses"
        license_dir.mkdir(parents=True)
        license_data = b"Fixture Apache license text\n"
        (license_dir / "Apache-2.0.txt").write_bytes(license_data)
        component = {"coordinate": "example:library:1", "artifact": "library-1.jar",
                     "sha256": "a" * 64, "licenses": ["Apache-2.0"], "source": "https://example.org/source"}
        (license_dir / "dependencies.json").write_text(json.dumps({
            "variants": {"debug": ["example:library:1@jar"]},
            "artifacts": {"example:library:1@jar": component},
            "licenses": {"Apache-2.0": {"file": "Apache-2.0.txt", "sha256": hashlib.sha256(license_data).hexdigest()}}}))
        self.license_text = b"example:library:1@jar\n" + license_data
        self.license_report = {"schema": "navis-android-license-report-v1", "variant": "debug",
                               "components": [component], "text_sha256": hashlib.sha256(self.license_text).hexdigest()}
        version_dir = self.root / "../platform/gecko-chrome/config"
        version_dir.mkdir(parents=True)
        (version_dir / "version.txt").write_text("0.2.0\n", encoding="utf-8")
        (version_dir / "version_display.txt").write_text(
            "0.2.0-dev\n", encoding="utf-8"
        )
        prefs_dir = self.root / "../runtime/gecko/mobile/android/app"
        prefs_dir.mkdir(parents=True)
        self.navis_prefs = prefs_dir / "navis-prefs.js"
        self.navis_prefs.write_text(
            VERIFY.LOCKED_BUILTIN_IDS_PREF + "\n", encoding="utf-8"
        )
        package_dir = self.root / "../platform/gecko-chrome/builtin/ublock-origin"
        package_dir.mkdir(parents=True)
        self.ublock = package_dir / "uBlock0@raymondhill.net.xpi"
        with zipfile.ZipFile(self.ublock, "w") as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("js/background.js", "source")
        self.ublock_hash = hashlib.sha256(self.ublock.read_bytes()).hexdigest()
        self.build_id = "20260904000000"
        self.version_code = VERIFY.expected_version_code(
            VERIFY.parse_build_id(self.build_id)
        )
        global AAPT_OUTPUT
        self.aapt_output = AAPT_OUTPUT.replace("310390400", str(self.version_code))
        self.apk = self.root / (
            f"navis-0.2.0-dev-android-arm64-v8a-test-candidate-{self.build_id}.apk"
        )
        self.xul = (
            elf_aarch64(bytes.fromhex("11" * 20), self.build_id)
            + VERIFY.NAVIS_NATIVE_WINDOW_OPEN_JNI_SIGNATURE
        )
        self.mozglue = elf_aarch64(
            bytes.fromhex("22" * 20), app_build_ids=[self.build_id]
        )
        self.compose_native = elf_aarch64(bytes.fromhex("33" * 20))
        self.dex = dex_build_config(self.build_id)
        self.native_root = self.root / "staged-native"
        self.native_dir = self.native_root / VERIFY.EXPECTED_ABI
        self.native_dir.mkdir(parents=True)
        (self.native_dir / "libxul.so").write_bytes(self.xul)
        (self.native_dir / "libmozglue.so").write_bytes(self.mozglue)
        self.write_apk()

    def write_apk(
        self,
        abi: str = "arm64-v8a",
        runtime_build_id: str | None = None,
        xul: bytes | None = None,
        mozglue: bytes | None = None,
        dex_build_ids: list[str] | None = None,
        dex_members: list[tuple[str, bytes]] | None = None,
        dex_extra_strings: tuple[str, ...] = (),
        include_compose_native: bool = True,
        extra_native: tuple[str, bytes] | None = None,
        include_navis_prefs: bool = True,
        packaged_navis_prefs: bytes | None = None,
        manifest_bytes: bytes = b"manifest",
        include_licenses: bool = True,
        license_text: bytes | None = None,
    ) -> None:
        with zipfile.ZipFile(self.apk, "w") as archive:
            archive.writestr("AndroidManifest.xml", manifest_bytes)
            if include_licenses:
                archive.writestr("assets/navis-licenses/android-dependencies.txt",
                                 self.license_text if license_text is None else license_text)
                archive.writestr("assets/navis-licenses/android-dependencies.json", json.dumps(self.license_report))
            if dex_members is not None:
                for name, data in dex_members:
                    archive.writestr(name, data)
            elif dex_build_ids is None:
                archive.writestr(
                    "classes.dex",
                    dex_build_config(self.build_id, dex_extra_strings),
                )
            else:
                for index, build_id in enumerate(dex_build_ids, 1):
                    suffix = "" if index == 1 else str(index)
                    archive.writestr(
                        f"classes{suffix}.dex", dex_build_config(build_id)
                    )
            archive.writestr(
                "assets/omni.ja",
                omni(
                    runtime_build_id or self.build_id,
                    (
                        packaged_navis_prefs
                        if packaged_navis_prefs is not None
                        else self.navis_prefs.read_bytes()
                    )
                    if include_navis_prefs
                    else None,
                ),
            )
            archive.writestr(
                f"lib/{abi}/libxul.so", self.xul if xul is None else xul
            )
            archive.writestr(
                f"lib/{abi}/libmozglue.so",
                self.mozglue if mozglue is None else mozglue,
            )
            if include_compose_native:
                archive.writestr(
                    f"lib/{abi}/libandroidx.graphics.path.so", self.compose_native
                )
            if extra_native is not None:
                archive.writestr(f"lib/{abi}/{extra_native[0]}", extra_native[1])
            with zipfile.ZipFile(self.ublock) as source:
                for name in source.namelist():
                    archive.writestr(f"{VERIFY.UBLOCK_PREFIX}{name}", source.read(name))

    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            apk=self.apk,
            source_freeze=self.root / "source-freeze.json",
            build_id=self.build_id,
            version_code=self.version_code,
            aapt2=self.root / "aapt2",
            apksigner=self.root / "apksigner",
            native_dir=self.native_root,
            artifact_name=self.apk.name,
            json_output=None,
            workspace=self.root,
            acceptance_api=34,
        )

    def inspect_candidate(self) -> dict[str, object]:
        with mock.patch.object(
            VERIFY, "EXPECTED_UBLOCK_SHA256", self.ublock_hash
        ):
            return VERIFY.inspect_apk(self.root, self.apk, self.build_id)

    def test_parses_product_badging(self) -> None:
        badging = VERIFY.parse_aapt_badging(self.aapt_output)
        self.assertEqual("org.navis.browser.debug", badging["application_id"])
        self.assertEqual(26, badging["min_sdk"])
        self.assertEqual(["arm64-v8a"], badging["abis"])

    def test_license_report_required_and_tamper_rejected(self) -> None:
        for change in ({"include_licenses": False}, {"license_text": b"tampered"}):
            with self.subTest(change=change):
                self.write_apk(**change)
                with self.assertRaisesRegex(VERIFY.PackageError, "license report"):
                    self.inspect_candidate()

    def test_unreviewed_dependency_and_wrong_variant_rejected(self) -> None:
        self.license_report["variant"] = "release"
        self.write_apk()
        with self.assertRaisesRegex(VERIFY.PackageError, "variant"):
            self.inspect_candidate()
        self.license_report["variant"] = "debug"
        self.license_report["components"][0]["coordinate"] = "unknown:library:2"
        self.write_apk()
        with self.assertRaisesRegex(VERIFY.PackageError, "unreviewed"):
            self.inspect_candidate()

    def test_omitted_dependency_rejected(self) -> None:
        policy_path = self.root / "../platform/android/licenses/dependencies.json"
        policy = json.loads(policy_path.read_text())
        policy["variants"]["debug"].append("example:another:1@jar")
        policy_path.write_text(json.dumps(policy))
        self.write_apk()
        with self.assertRaisesRegex(VERIFY.PackageError, "dependency set"):
            self.inspect_candidate()

    def test_parses_legacy_sdk_version_badging(self) -> None:
        badging = VERIFY.parse_aapt_badging(
            self.aapt_output.replace("minSdkVersion:'26'", "sdkVersion:'26'")
        )
        self.assertEqual(26, badging["min_sdk"])

    def test_rejects_badging_without_minimum_sdk(self) -> None:
        with self.assertRaisesRegex(VERIFY.PackageError, "lacks package, SDK"):
            VERIFY.parse_aapt_badging(
                self.aapt_output.replace("minSdkVersion:'26'\n", "")
            )

    def test_rejects_signature_without_v2_or_newer(self) -> None:
        with self.assertRaisesRegex(VERIFY.PackageError, "v2 or newer"):
            VERIFY.parse_apksigner(
                SIGNER_OUTPUT.replace(
                    "Verified using v2 scheme (APK Signature Scheme v2): true\n"
                    "Verified using v3 scheme (APK Signature Scheme v3): true\n",
                    "",
                ).replace("false", "true", 1)
            )

    def test_parses_legacy_signer_certificate_labels(self) -> None:
        legacy = SIGNER_OUTPUT.replace(
            "V2 Signer: certificate", "Signer #1 certificate"
        )
        signing = VERIFY.parse_apksigner(legacy)
        self.assertEqual("a" * 64, signing["certificate_sha256"])

    def test_rejects_wrong_native_abi(self) -> None:
        self.write_apk("x86_64")
        with mock.patch.object(
            VERIFY, "EXPECTED_UBLOCK_SHA256", self.ublock_hash
        ), self.assertRaisesRegex(VERIFY.PackageError, "ABI inventory"):
            VERIFY.inspect_apk(self.root, self.apk, self.build_id)

    def test_exact_candidate_closure_passes(self) -> None:
        freeze = {
            "manifest": "source-freeze.json",
            "manifest_sha256": "b" * 64,
            "aggregate_sha256": "c" * 64,
            "file_count": 20,
            "android_file_count": 4,
            "gecko_commit": "d" * 40,
            "semantic_port_count": 44,
        }
        with mock.patch.object(
            VERIFY, "EXPECTED_UBLOCK_SHA256", self.ublock_hash
        ), mock.patch.object(
            VERIFY, "run_tool", side_effect=[self.aapt_output, SIGNER_OUTPUT]
        ), mock.patch.object(VERIFY, "load_source_freeze", return_value=freeze):
            report = VERIFY.verify(self.args())
        self.assertEqual(self.apk.name, report["artifact"]["name"])
        self.assertEqual(["arm64-v8a"], report["inventory"]["native_abis"])
        self.assertEqual(self.build_id, report["inventory"]["runtime_build_id"])
        self.assertEqual(
            self.build_id, report["inventory"]["native_runtime_build_id"]
        )
        self.assertEqual(
            self.build_id, report["inventory"]["native_application_build_id"]
        )
        self.assertEqual(
            self.build_id, report["inventory"]["geckoview_build_id"]
        )
        self.assertEqual(
            "classes.dex", report["inventory"]["geckoview_build_config_dex"]
        )
        self.assertEqual([], report["inventory"]["geckoview_lifecycle_classes"])
        self.assertEqual(
            [], report["inventory"]["geckoview_owner_type_references"]
        )
        self.assertTrue(report["inventory"]["staged_native_match"])
        self.assertEqual(
            "11" * 20,
            report["inventory"]["native_libraries"]
            ["lib/arm64-v8a/libxul.so"]["gnu_build_id"],
        )
        self.assertEqual(
            hashlib.sha256(self.xul).hexdigest(),
            report["inventory"]["native_libraries"]
            ["lib/arm64-v8a/libxul.so"]["sha256"],
        )
        self.assertEqual("android-debug-test-candidate", report["signing"]["policy"])

    def test_rejects_runtime_build_id_drift(self) -> None:
        self.write_apk(runtime_build_id="20260904000001")
        with mock.patch.object(
            VERIFY, "EXPECTED_UBLOCK_SHA256", self.ublock_hash
        ), self.assertRaisesRegex(VERIFY.PackageError, "runtime MOZ_BUILDID differs"):
            VERIFY.inspect_apk(self.root, self.apk, self.build_id)

    def test_rejects_missing_packaged_navis_defaults(self) -> None:
        self.write_apk(include_navis_prefs=False)
        with self.assertRaisesRegex(VERIFY.PackageError, "Navis runtime assets"):
            self.inspect_candidate()

    def test_rejects_google_mobile_services_manifest_metadata(self) -> None:
        self.write_apk(
            manifest_bytes=(
                '<meta-data android:name="com.google.android.gms.version" />'
            ).encode("utf-16le")
        )
        with self.assertRaisesRegex(
            VERIFY.PackageError, "manifest imports Google Mobile Services"
        ):
            self.inspect_candidate()

    def test_rejects_google_mobile_services_dex_reference(self) -> None:
        gms_reference = dex_build_config(
            self.build_id,
            class_descriptor="Lorg/example/GmsConsumer;",
            field_type_descriptor=(
                "Lcom/google/android/gms/common/api/GoogleApiActivity;"
            ),
        )
        self.write_apk(
            dex_members=[
                ("classes.dex", dex_build_config(self.build_id)),
                ("classes2.dex", gms_reference),
            ]
        )
        with self.assertRaisesRegex(
            VERIFY.PackageError, "DEX imports Google Mobile Services"
        ):
            self.inspect_candidate()

    def test_rejects_packaged_navis_defaults_drift(self) -> None:
        self.write_apk(packaged_navis_prefs=b"// stale defaults\n")
        with self.assertRaisesRegex(VERIFY.PackageError, "defaults differ"):
            self.inspect_candidate()

    def test_rejects_unlocked_built_in_extension_registry(self) -> None:
        unlocked = VERIFY.LOCKED_BUILTIN_IDS_PREF.replace(", locked);", ");")
        self.navis_prefs.write_text(unlocked + "\n", encoding="utf-8")
        self.write_apk()
        with self.assertRaisesRegex(VERIFY.PackageError, "locked built-in"):
            self.inspect_candidate()

    def test_rejects_required_native_library_without_build_id(self) -> None:
        self.write_apk(xul=elf_aarch64(None))
        with mock.patch.object(
            VERIFY, "EXPECTED_UBLOCK_SHA256", self.ublock_hash
        ), self.assertRaisesRegex(VERIFY.PackageError, "lacks a GNU Build ID"):
            VERIFY.inspect_apk(self.root, self.apk, self.build_id)

    def test_rejects_native_runtime_build_id_drift(self) -> None:
        self.write_apk(
            xul=elf_aarch64(
                bytes.fromhex("11" * 20), "20260904000001"
            )
        )
        with mock.patch.object(
            VERIFY, "EXPECTED_UBLOCK_SHA256", self.ublock_hash
        ), self.assertRaisesRegex(VERIFY.PackageError, "libxul Mozilla BuildID differs"):
            VERIFY.inspect_apk(self.root, self.apk, self.build_id)

    def test_rejects_stale_native_window_open_jni_signature(self) -> None:
        stale_signature = (
            b"(Lorg/mozilla/gecko/navis/NavisAndroidSession$NativeWindow;"
            b"Lorg/mozilla/gecko/navis/NavisAndroidSession$Compositor;"
            b"Lorg/mozilla/gecko/EventDispatcher;JZ)V"
        )
        self.write_apk(
            xul=elf_aarch64(bytes.fromhex("11" * 20), self.build_id)
            + stale_signature
        )
        with self.assertRaisesRegex(
            VERIFY.PackageError, "NativeWindow.open JNI signature"
        ):
            self.inspect_candidate()

    def test_rejects_libmozglue_application_build_id_drift(self) -> None:
        stale = "20260904000001"
        self.write_apk(
            mozglue=elf_aarch64(
                bytes.fromhex("22" * 20), app_build_ids=[stale]
            )
        )
        with self.assertRaisesRegex(
            VERIFY.PackageError, "libmozglue StaticXREAppData BuildID differs"
        ):
            self.inspect_candidate()

    def test_rejects_mixed_libmozglue_application_build_ids(self) -> None:
        self.write_apk(
            mozglue=elf_aarch64(
                bytes.fromhex("22" * 20),
                app_build_ids=[self.build_id, "20260904000001"],
            )
        )
        with self.assertRaisesRegex(
            VERIFY.PackageError, "conflicting StaticXREAppData BuildIDs"
        ):
            self.inspect_candidate()

    def test_rejects_unstructured_libmozglue_build_id_string(self) -> None:
        mozglue = (
            elf_aarch64(bytes.fromhex("22" * 20))
            + self.build_id.encode("ascii")
            + b"\0"
        )
        self.write_apk(mozglue=mozglue)
        with self.assertRaisesRegex(
            VERIFY.PackageError, "libmozglue has no ELF load segments"
        ):
            self.inspect_candidate()

    def test_rejects_geckoview_build_config_id_drift(self) -> None:
        self.write_apk(dex_build_ids=["20260904000001"])
        with self.assertRaisesRegex(
            VERIFY.PackageError, "BuildConfig MOZ_APP_BUILDID differs"
        ):
            self.inspect_candidate()

    def test_rejects_conflicting_geckoview_build_config_definitions(self) -> None:
        self.write_apk(
            dex_build_ids=[self.build_id, "20260904000001"]
        )
        with self.assertRaisesRegex(
            VERIFY.PackageError, "conflicting GeckoView BuildConfig definitions"
        ):
            self.inspect_candidate()

    def test_rejects_duplicate_geckoview_build_config_definitions(self) -> None:
        self.write_apk(dex_build_ids=[self.build_id, self.build_id])
        with self.assertRaisesRegex(
            VERIFY.PackageError, "duplicate GeckoView BuildConfig definitions"
        ):
            self.inspect_candidate()

    def test_ignores_unrelated_dex_certificate_dates(self) -> None:
        self.write_apk(
            dex_extra_strings=("20500101000000", "20541231235959")
        )
        inventory = self.inspect_candidate()
        self.assertEqual(self.build_id, inventory["geckoview_build_id"])

    def test_ignores_forbidden_descriptor_in_dex_string_pool_only(self) -> None:
        self.write_apk(
            dex_extra_strings=(VERIFY.FORBIDDEN_GECKOVIEW_LIFECYCLE_CLASSES[0],)
        )
        inventory = self.inspect_candidate()
        self.assertEqual([], inventory["geckoview_lifecycle_classes"])

    def test_rejects_forbidden_lifecycle_class_in_secondary_dex(self) -> None:
        for descriptor in VERIFY.FORBIDDEN_GECKOVIEW_LIFECYCLE_CLASSES:
            with self.subTest(descriptor=descriptor):
                forbidden = dex_build_config(
                    self.build_id,
                    class_descriptor=descriptor,
                )
                self.write_apk(
                    dex_members=[
                        ("classes.dex", dex_build_config(self.build_id)),
                        ("classes2.dex", forbidden),
                    ]
                )
                with self.assertRaisesRegex(
                    VERIFY.PackageError, "forbidden GeckoView lifecycle classes"
                ):
                    self.inspect_candidate()

    def test_rejects_forbidden_lifecycle_inner_class(self) -> None:
        forbidden = dex_build_config(
            self.build_id,
            class_descriptor="Lorg/mozilla/geckoview/GeckoRuntime$Builder;",
        )
        self.write_apk(
            dex_members=[
                ("classes.dex", dex_build_config(self.build_id)),
                ("classes2.dex", forbidden),
            ]
        )
        with self.assertRaisesRegex(
            VERIFY.PackageError, "GeckoRuntime\\$Builder"
        ):
            self.inspect_candidate()

    def test_rejects_forbidden_owner_type_reference(self) -> None:
        for descriptor in (
            "Lorg/mozilla/geckoview/GeckoWebExecutor;",
            "Lorg/mozilla/geckoview/PanZoomController;",
            "[Lorg/mozilla/geckoview/SessionTextInput;",
            "Lorg/mozilla/geckoview/WebAuthnTokenManager;",
        ):
            with self.subTest(descriptor=descriptor):
                owner_reference = dex_build_config(
                    self.build_id,
                    class_descriptor="Lorg/example/EnginePrimitive;",
                    field_type_descriptor=descriptor,
                )
                self.write_apk(
                    dex_members=[
                        ("classes.dex", dex_build_config(self.build_id)),
                        ("classes2.dex", owner_reference),
                    ]
                )
                with self.assertRaisesRegex(
                    VERIFY.PackageError, "forbidden GeckoView owner type references"
                ):
                    self.inspect_candidate()

    def test_allows_product_neutral_geckoview_utility_class(self) -> None:
        utility = dex_build_config(
            self.build_id,
            class_descriptor="Lorg/mozilla/geckoview/GeckoViewInputStream;",
        )
        self.write_apk(
            dex_members=[
                ("classes.dex", dex_build_config(self.build_id)),
                ("classes2.dex", utility),
            ]
        )
        inventory = self.inspect_candidate()
        self.assertEqual([], inventory["geckoview_lifecycle_classes"])

    def test_rejects_dex_without_geckoview_build_config_class(self) -> None:
        dex = dex_build_config(
            self.build_id, class_descriptor="Lorg/example/BuildConfig;"
        )
        self.write_apk(dex_members=[("classes.dex", dex)])
        with self.assertRaisesRegex(
            VERIFY.PackageError, "lacks GeckoView BuildConfig.MOZ_APP_BUILDID"
        ):
            self.inspect_candidate()

    def test_rejects_build_id_field_owned_by_wrong_class(self) -> None:
        dex = dex_build_config(
            self.build_id,
            field_class_descriptor="Lorg/example/BuildConfig;",
        )
        self.write_apk(dex_members=[("classes.dex", dex)])
        with self.assertRaisesRegex(
            VERIFY.PackageError, "lacks one MOZ_APP_BUILDID field"
        ):
            self.inspect_candidate()

    def test_rejects_build_id_field_with_wrong_type(self) -> None:
        dex = dex_build_config(self.build_id, field_type_descriptor="I")
        self.write_apk(dex_members=[("classes.dex", dex)])
        with self.assertRaisesRegex(
            VERIFY.PackageError, "lacks one MOZ_APP_BUILDID field"
        ):
            self.inspect_candidate()

    def test_rejects_build_id_field_without_static_final_flags(self) -> None:
        dex = dex_build_config(self.build_id, access_flags=0x09)
        self.write_apk(dex_members=[("classes.dex", dex)])
        with self.assertRaisesRegex(VERIFY.PackageError, "is not static final"):
            self.inspect_candidate()

    def test_rejects_build_id_value_with_invalid_value_arg(self) -> None:
        dex = dex_build_config(self.build_id, target_value=b"\x97")
        self.write_apk(dex_members=[("classes.dex", dex)])
        with self.assertRaisesRegex(VERIFY.PackageError, "invalid width"):
            self.inspect_candidate()

    def test_rejects_excess_build_config_static_values(self) -> None:
        dex = dex_build_config(self.build_id, trailing_value=b"\x1f")
        self.write_apk(dex_members=[("classes.dex", dex)])
        with self.assertRaisesRegex(VERIFY.PackageError, "excess static values"):
            self.inspect_candidate()

    def test_rejects_truncation_after_build_id_value(self) -> None:
        dex = dex_build_config(
            self.build_id,
            include_trailing_field=True,
            trailing_value=b"\x77",
        )
        self.write_apk(dex_members=[("classes.dex", dex)])
        with self.assertRaisesRegex(VERIFY.PackageError, "truncated DEX"):
            self.inspect_candidate()

    def test_rejects_overflowing_dex_uleb128(self) -> None:
        dex = bytearray(dex_build_config(self.build_id))
        class_defs_offset = struct.unpack_from("<I", dex, 100)[0]
        class_data_offset = struct.unpack_from("<I", dex, class_defs_offset + 24)[0]
        dex[class_data_offset : class_data_offset + 5] = b"\x80\x80\x80\x80\x10"
        self.write_apk(dex_members=[("classes.dex", bytes(dex))])
        with self.assertRaisesRegex(VERIFY.PackageError, "overflowing DEX ULEB128"):
            self.inspect_candidate()

    def test_rejects_missing_primary_dex(self) -> None:
        self.write_apk(
            dex_members=[("classes2.dex", dex_build_config(self.build_id))]
        )
        with self.assertRaisesRegex(VERIFY.PackageError, "lacks primary classes.dex"):
            self.inspect_candidate()

    def test_rejects_noncanonical_dex_member(self) -> None:
        self.write_apk(
            dex_members=[
                ("classes.dex", dex_build_config(self.build_id)),
                ("classes01.dex", dex_build_config(self.build_id)),
            ]
        )
        with self.assertRaisesRegex(VERIFY.PackageError, "non-canonical DEX"):
            self.inspect_candidate()

    def test_accepts_direct_native_abi_directory(self) -> None:
        inventory = self.inspect_candidate()
        count = VERIFY.verify_staged_native_libraries(
            self.native_dir, inventory["native_libraries"]
        )
        self.assertEqual(2, count)

    def test_rejects_native_library_mixed_into_staging_root(self) -> None:
        (self.native_root / "libmixed.so").write_bytes(
            elf_aarch64(bytes.fromhex("44" * 20))
        )
        inventory = self.inspect_candidate()
        with self.assertRaisesRegex(VERIFY.PackageError, "unexpected path"):
            VERIFY.verify_staged_native_libraries(
                self.native_root, inventory["native_libraries"]
            )

    def test_rejects_other_staged_native_abi(self) -> None:
        other = self.native_root / "x86_64"
        other.mkdir()
        (other / "libother.so").write_bytes(
            elf_aarch64(bytes.fromhex("44" * 20))
        )
        inventory = self.inspect_candidate()
        with self.assertRaisesRegex(VERIFY.PackageError, "unexpected path"):
            VERIFY.verify_staged_native_libraries(
                self.native_root, inventory["native_libraries"]
            )

    def test_rejects_missing_packaged_only_native_library(self) -> None:
        self.write_apk(include_compose_native=False)
        with self.assertRaisesRegex(
            VERIFY.PackageError, "libandroidx.graphics.path.so"
        ):
            self.inspect_candidate()

    def test_rejects_unlisted_packaged_native_library(self) -> None:
        self.write_apk(
            extra_native=("libunexpected.so", elf_aarch64(bytes.fromhex("44" * 20)))
        )
        inventory = self.inspect_candidate()
        with self.assertRaisesRegex(
            VERIFY.PackageError, "packaged-only allowlist"
        ):
            VERIFY.verify_staged_native_libraries(
                self.native_root, inventory["native_libraries"]
            )

    def test_rejects_packaged_only_native_library_in_staging(self) -> None:
        (self.native_dir / "libandroidx.graphics.path.so").write_bytes(
            self.compose_native
        )
        inventory = self.inspect_candidate()
        with self.assertRaisesRegex(VERIFY.PackageError, "packaged-only native"):
            VERIFY.verify_staged_native_libraries(
                self.native_root, inventory["native_libraries"]
            )

    def test_rejects_native_library_that_differs_from_staged_output(self) -> None:
        (self.native_dir / "libxul.so").write_bytes(
            elf_aarch64(bytes.fromhex("33" * 20), self.build_id)
        )
        with mock.patch.object(
            VERIFY, "EXPECTED_UBLOCK_SHA256", self.ublock_hash
        ):
            inventory = VERIFY.inspect_apk(self.root, self.apk, self.build_id)
        with self.assertRaisesRegex(
            VERIFY.PackageError, "differs from staged output"
        ):
            VERIFY.verify_staged_native_libraries(
                self.native_dir, inventory["native_libraries"]
            )

    def test_rejects_version_code_not_derived_from_build_id(self) -> None:
        args = self.args()
        args.version_code += 1
        with self.assertRaisesRegex(VERIFY.PackageError, "build ID derivation"):
            VERIFY.verify(args)

    def test_rejects_artifact_name_without_explicit_candidate_scope(self) -> None:
        args = self.args()
        args.artifact_name = "navis.apk"
        with mock.patch.object(
            VERIFY, "EXPECTED_UBLOCK_SHA256", self.ublock_hash
        ), mock.patch.object(
            VERIFY, "run_tool", side_effect=[self.aapt_output, SIGNER_OUTPUT]
        ), mock.patch.object(
            VERIFY,
            "load_source_freeze",
            return_value={"android_file_count": 1},
        ), self.assertRaisesRegex(VERIFY.PackageError, "artifact name differs"):
            VERIFY.verify(args)


if __name__ == "__main__":
    unittest.main()
