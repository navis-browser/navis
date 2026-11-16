#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


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
ac_add_options --with-ccache=sccache
ac_add_options --enable-desktop-embedder
ac_add_options --enable-webdriver
ac_add_options --disable-observability-runtime
ac_add_options --disable-web-push
ac_add_options --disable-web-device-apis
ac_add_options --disable-web-identity
ac_add_options --disable-web-payments
ac_add_options --disable-webxr
"""


class ReleaseProfileVerifierTests(unittest.TestCase):
    def make_workspace(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "navis/scripts").mkdir(parents=True)
        (root / "platform/gecko-chrome/config").mkdir(parents=True)
        (root / "runtime").mkdir()
        (root / "platform/gecko-chrome/config/version.txt").write_text(
            "0.3.1\n", encoding="utf-8"
        )
        (root / "platform/gecko-chrome/config/version_display.txt").write_text(
            "0.3.1\n", encoding="utf-8"
        )
        (root / "runtime/mozconfig.runtime.release").write_text(
            COMMON
            + "ac_add_options --host=x86_64-unknown-linux-gnu\n"
            + "ac_add_options --target=x86_64-unknown-linux-gnu\n"
            + "mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-navis-runtime-release\n"
            + "mk_add_options AUTOCLOBBER=\n",
            encoding="utf-8",
        )
        (root / "runtime/mozconfig.win64.release").write_text(
            COMMON
            + "ac_add_options --target=x86_64-pc-windows-msvc\n"
            + "ac_add_options --enable-bootstrap\n"
            + "ac_add_options --disable-bits-download\n"
            + "ac_add_options --with-redist\n"
            + "mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-navis-win64-release\n"
            + "mk_add_options AUTOCLOBBER=\n",
            encoding="utf-8",
        )
        (root / "runtime/mozconfig.android-aarch64.sccache").write_text(
            "export MOZ_REQUIRE_SIGNING=1\n"
            + "ac_add_options --enable-project=mobile/android\n"
            + "ac_add_options --host=x86_64-unknown-linux-gnu\n"
            + "ac_add_options --target=aarch64-linux-android\n"
            + "ac_add_options --with-navis-product-version-file-path=navis/config\n"
            + "ac_add_options --enable-android-subproject=navis\n"
            + "ac_add_options --enable-navis-core\n"
            + "ac_add_options --disable-tests\n"
            + "ac_add_options --disable-release\n"
            + "ac_add_options --disable-cargo-incremental\n"
            + "ac_add_options --with-ccache=sccache\n"
            + "ac_add_options --disable-web-push\n"
            + "ac_add_options --disable-web-device-apis\n"
            + "ac_add_options --disable-web-identity\n"
            + "ac_add_options --disable-web-payments\n"
            + "ac_add_options --disable-webxr\n"
            + "mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/"
            + "obj-navis-android-aarch64-sccache\n"
            + "mk_add_options AUTOCLOBBER=\n",
            encoding="utf-8",
        )
        release = (root / "runtime/mozconfig.android-aarch64.sccache").read_text().replace(
            "ac_add_options --disable-release\n",
            "ac_add_options --enable-release\nac_add_options --disable-debug\nac_add_options --enable-optimize\n",
        ).replace("obj-navis-android-aarch64-sccache", "obj-navis-android-aarch64-release")
        (root / "runtime/mozconfig.android-aarch64.release").write_text(release)
        return root

    def run_verifier(self, root: Path) -> tuple[int, str]:
        output = io.StringIO()
        with mock.patch.object(MODULE, "WORKSPACE", root), contextlib.redirect_stdout(
            output
        ), contextlib.redirect_stderr(output):
            result = MODULE.main()
        return result, output.getvalue()

    def test_accepts_sccache_backed_shipping_and_android_candidate_graphs(self) -> None:
        result, output = self.run_verifier(self.make_workspace())
        self.assertEqual(result, 0, output)
        self.assertIn("Windows x86_64", output)
        self.assertIn("Android aarch64", output)

    def test_rejects_android_release_using_developer_options(self) -> None:
        root = self.make_workspace()
        path = root / "runtime/mozconfig.android-aarch64.release"
        path.write_text(path.read_text().replace("--enable-release", "--disable-release"))
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("mozconfig.android-aarch64.release lacks ac_add_options --enable-release", output)

    def test_rejects_desktop_release_without_sccache(self) -> None:
        root = self.make_workspace()
        path = root / "runtime/mozconfig.runtime.release"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "ac_add_options --with-ccache=sccache\n", ""
            ),
            encoding="utf-8",
        )
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("lacks ac_add_options --with-ccache=sccache", output)

    def test_rejects_product_version_drift(self) -> None:
        root = self.make_workspace()
        (root / "platform/gecko-chrome/config/version_display.txt").write_text(
            "0.3.1-preview\n", encoding="utf-8"
        )
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("must be 0.3.1", output)

    def test_rejects_android_candidate_without_source_signing_policy(self) -> None:
        root = self.make_workspace()
        path = root / "runtime/mozconfig.android-aarch64.sccache"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "export MOZ_REQUIRE_SIGNING=1\n", ""
            ),
            encoding="utf-8",
        )
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("lacks export MOZ_REQUIRE_SIGNING=1", output)

    def test_rejects_development_release_flag(self) -> None:
        root = self.make_workspace()
        path = root / "runtime/mozconfig.win64.release"
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

    def test_rejects_windows_release_without_app_local_runtime(self) -> None:
        root = self.make_workspace()
        path = root / "runtime/mozconfig.win64.release"
        path.write_text(path.read_text().replace("ac_add_options --with-redist\n", ""))
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("lacks ac_add_options --with-redist", output)

    def test_rejects_release_without_immutable_addon_signing(self) -> None:
        root = self.make_workspace()
        path = root / "runtime/mozconfig.runtime.release"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "export MOZ_REQUIRE_SIGNING=1\n", ""
            ),
            encoding="utf-8",
        )
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("lacks export MOZ_REQUIRE_SIGNING=1", output)

    def test_rejects_candidate_profile_that_can_auto_clobber(self) -> None:
        root = self.make_workspace()
        path = root / "runtime/mozconfig.runtime.release"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "mk_add_options AUTOCLOBBER=\n",
                "mk_add_options AUTOCLOBBER=1\n",
            ),
            encoding="utf-8",
        )
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("automatic object-directory clobbering", output)
        self.assertIn("enables automatic clobbering", output)

    def test_rejects_candidate_profile_with_second_object_directory(self) -> None:
        root = self.make_workspace()
        path = root / "runtime/mozconfig.win64.release"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-hidden-cold-graph\n",
            encoding="utf-8",
        )
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("must own exactly MOZ_OBJDIR", output)

    def test_rejects_desktop_release_without_web_push_cut(self) -> None:
        root = self.make_workspace()
        path = root / "runtime/mozconfig.runtime.release"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "ac_add_options --disable-web-push\n", ""
            ),
            encoding="utf-8",
        )
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("lacks ac_add_options --disable-web-push", output)

    def test_rejects_android_candidate_with_disabled_webrtc(self) -> None:
        root = self.make_workspace()
        path = root / "runtime/mozconfig.android-aarch64.sccache"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "ac_add_options --disable-webrtc\n",
            encoding="utf-8",
        )
        result, output = self.run_verifier(root)
        self.assertEqual(result, 1)
        self.assertIn("contains ac_add_options --disable-webrtc", output)



if __name__ == "__main__":
    unittest.main()
