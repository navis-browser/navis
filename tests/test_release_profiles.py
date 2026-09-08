#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock


VERIFIER = Path(__file__).resolve().parents[1] / "scripts/verify-release-profiles.py"
SPEC = importlib.util.spec_from_file_location("verify_release_profiles", VERIFIER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


COMMON = """\
export MOZ_REQUIRE_SIGNING=1
ac_add_options --enable-project=navis
ac_add_options --disable-tests
ac_add_options --enable-release
ac_add_options --disable-cargo-incremental
ac_add_options --enable-desktop-embedder
ac_add_options --enable-webextensions-runtime
ac_add_options --enable-webdriver
ac_add_options --disable-observability-runtime
ac_add_options --disable-webrtc
"""


class ReleaseProfileVerifierTests(unittest.TestCase):
    def make_workspace(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "navis"
        root.mkdir()
        (root.parent / "runtime").mkdir()
        (root / "scripts").mkdir()
        (root / "../runtime/mozconfig.runtime.release").write_text(
            COMMON
            + "ac_add_options --host=x86_64-unknown-linux-gnu\n"
            + "ac_add_options --target=x86_64-unknown-linux-gnu\n"
            + "mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-navis-runtime-release\n",
            encoding="utf-8",
        )
        (root / "../runtime/mozconfig.win64.release").write_text(
            COMMON
            + "ac_add_options --target=x86_64-pc-windows-msvc\n"
            + "ac_add_options --enable-bootstrap\n"
            + "ac_add_options --disable-bits-download\n"
            + "mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-navis-win64-release\n",
            encoding="utf-8",
        )
        return root

    def run_verifier(self, root: Path) -> tuple[int, str]:
        output = io.StringIO()
        with mock.patch.object(MODULE, "WORKSPACE", root), contextlib.redirect_stdout(
            output
        ), contextlib.redirect_stderr(output):
            result = MODULE.main()
        return result, output.getvalue()

    def test_accepts_independent_cache_free_shipping_graphs(self) -> None:
        result, output = self.run_verifier(self.make_workspace())
        self.assertEqual(result, 0, output)
        self.assertIn("Windows x86_64", output)

    def test_rejects_development_release_flag(self) -> None:
        root = self.make_workspace()
        path = root / "../runtime/mozconfig.win64.release"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "ac_add_options --enable-release",
                "ac_add_options --disable-release",
            ),
            encoding="utf-8",
        )
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("lacks ac_add_options --enable-release", output)
        self.assertIn("contains ac_add_options --disable-release", output)

    def test_rejects_release_without_immutable_addon_signing(self) -> None:
        root = self.make_workspace()
        path = root / "../runtime/mozconfig.runtime.release"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "export MOZ_REQUIRE_SIGNING=1\n", ""
            ),
            encoding="utf-8",
        )
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("lacks export MOZ_REQUIRE_SIGNING=1", output)



if __name__ == "__main__":
    unittest.main()
