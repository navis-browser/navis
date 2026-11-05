# SPDX-License-Identifier: MPL-2.0

"""Portable packages must not rely on an installed VC Redistributable."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify-windows-runtime.py"
SPEC = importlib.util.spec_from_file_location("windows_runtime", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HEADER = "Format: COFF-x86-64\n  Machine: IMAGE_FILE_MACHINE_AMD64 (0x8664)\n"


class WindowsRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = json.loads(MODULE.POLICY.read_text())
        for name in self.policy["redist"]["files"]:
            data = name.encode()
            (self.root / name).write_bytes(data)
            self.policy["redist"]["files"][name] = hashlib.sha256(data).hexdigest()
        (self.root / "navis.exe").write_bytes(b"application")
        self.graph = {name: [] for name in self.policy["redist"]["files"]}
        self.graph["navis.exe"] = [{"kind": "Import", "name": "vcruntime140.dll"}]
        inspector = patch.object(MODULE, "inspect_pe", side_effect=lambda path, tool: self.graph.get(path.name, []))
        inspector.start()
        self.addCleanup(inspector.stop)

    def verify(self):
        return MODULE.verify(self.root, self.policy, "llvm-readobj")

    def test_accepts_app_local_runtime_and_case_insensitive_imports(self):
        output = MODULE.parse_imports(HEADER + "Import {\n  Name: VCRUNTIME140.dll\n}\n")
        self.assertEqual(output, [{"kind": "Import", "name": "vcruntime140.dll"}])
        self.assertEqual(self.verify()["pe_count"], 5)

    def test_each_runtime_dll_is_required_even_with_system_runtime_installed(self):
        for name in self.policy["redist"]["files"]:
            path = self.root / name
            data = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaisesRegex(MODULE.VerificationError, "missing app-local runtime"):
                self.verify()
            path.write_bytes(data)

    def test_rejects_tampered_or_unreviewed_redist(self):
        (self.root / "msvcp140.dll").write_bytes(b"different version")
        with self.assertRaisesRegex(MODULE.VerificationError, "unreviewed Redist bytes"):
            self.verify()

    def test_rejects_runtime_symlink_to_external_installation(self):
        path = self.root / "msvcp140.dll"
        path.unlink()
        path.symlink_to("/usr/lib/msvcp140.dll")
        with self.assertRaisesRegex(MODULE.VerificationError, "missing app-local runtime"):
            self.verify()

    def test_rejects_indirect_dependency_of_bundled_runtime(self):
        self.graph["msvcp140.dll"] = [{"kind": "Import", "name": "missing.dll"}]
        with self.assertRaisesRegex(MODULE.VerificationError, "msvcp140.dll: unresolved Import missing.dll"):
            self.verify()

    def test_rejects_delay_dependency_in_any_packaged_library(self):
        (self.root / "codec.dll").write_bytes(b"codec")
        self.graph["codec.dll"] = [{"kind": "DelayImport", "name": "missing.dll"}]
        with self.assertRaisesRegex(MODULE.VerificationError, "unresolved DelayImport missing.dll"):
            self.verify()

    def test_accepts_reviewed_system_api_set_and_delay_import(self):
        self.graph["navis.exe"] += MODULE.parse_imports(
            HEADER + "DelayImport {\n  Name: api-ms-win-crt-runtime-l1-1-0.dll\n}\n")
        self.assertTrue(self.verify()["passed"])

    def test_rejects_unreviewed_api_set_instead_of_trusting_prefix(self):
        self.graph["navis.exe"] = [{"kind": "Import", "name": "api-ms-win-invented.dll"}]
        with self.assertRaisesRegex(MODULE.VerificationError, "unresolved Import"):
            self.verify()

    def test_rejects_system_library_shipped_in_package(self):
        (self.root / "kernel32.dll").write_bytes(b"shadow")
        with self.assertRaisesRegex(MODULE.VerificationError, "shadows Windows"):
            self.verify()

    def test_rejects_extra_unpinned_runtime(self):
        (self.root / "msvcp140_1.dll").write_bytes(b"unreviewed runtime")
        with self.assertRaisesRegex(MODULE.VerificationError, "unpinned VC Runtime"):
            self.verify()

    def test_explicit_missing_inspection_tool_fails_closed(self):
        with patch.dict("os.environ", {"NAVIS_LLVM_READOBJ": "/nonexistent/llvm-readobj"}):
            with self.assertRaisesRegex(MODULE.VerificationError, "llvm-readobj is required"):
                MODULE.readobj_path()

    def test_rejects_case_colliding_modules(self):
        (self.root / "NAVIS.exe").write_bytes(b"duplicate")
        with self.assertRaisesRegex(MODULE.VerificationError, "case-colliding"):
            self.verify()

    def test_nested_dll_cannot_satisfy_application_import(self):
        (self.root / "nested").mkdir()
        (self.root / "nested/missing.dll").write_bytes(b"wrong search directory")
        with self.assertRaisesRegex(MODULE.VerificationError, "outside application directory"):
            self.verify()

    def test_rejects_wrong_architecture_and_non_pe(self):
        for output in ("not PE", HEADER.replace("COFF-x86-64", "COFF-i386"),
                       HEADER.replace("AMD64 (0x8664)", "ARM64 (0xAA64)")):
            with self.subTest(output=output), self.assertRaisesRegex(MODULE.VerificationError, "AMD64"):
                MODULE.parse_imports(output)

    def test_rejects_malformed_import_descriptor(self):
        for body in ("Import {\n  Name: x.dll\n", "Import {\n}\n",
                     "Import {\n  Name: ../x.dll\n}\n"):
            with self.subTest(body=body), self.assertRaises(MODULE.VerificationError):
                MODULE.parse_imports(HEADER + body)

    def test_policy_cannot_classify_vc_runtime_as_windows(self):
        self.policy["system_libraries"].append("vcruntime140.dll")
        with self.assertRaisesRegex(MODULE.VerificationError, "must not be classified"):
            self.verify()

    def test_configured_gate_requires_explicit_release_redist(self):
        config = {"OS_TARGET": "WINNT", "MOZ_NO_DEBUG_RTL": "1",
                  "WIN32_REDIST_DIR": str(self.root), **MODULE.REDIST_VARIABLES}
        path = self.root / "config.status.json"
        path.write_text(json.dumps({"substs": config}))
        self.assertEqual(MODULE.configured_redist(self.root), self.root)
        del config["WIN32_REDIST_DIR"]
        path.write_text(json.dumps({"substs": config}))
        with self.assertRaisesRegex(MODULE.VerificationError, "did not enable"):
            MODULE.configured_redist(self.root)


if __name__ == "__main__":
    unittest.main()
