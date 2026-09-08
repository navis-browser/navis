#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


VERIFIER = (
    Path(__file__).resolve().parents[1] / "scripts/verify-incremental-objdir.py"
)
SPEC = importlib.util.spec_from_file_location("verify_incremental_objdir", VERIFIER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class IncrementalObjectDirectoryTests(unittest.TestCase):
    def write_input_record(self, root: Path, objdir: Path, status: Path) -> Path:
        record = objdir / ".mozconfig.json"
        profile = MODULE.PROFILES["win64"]
        record.write_text(json.dumps({
            "topsrcdir": str(root / "runtime/gecko"),
            "topobjdir": str(objdir),
            "mozconfig": {
                "path": str(root / "runtime" / profile.mozconfig_name),
                "topobjdir": str(objdir),
                "configure_args": list(profile.required_options),
            },
        }), encoding="utf-8")
        os.utime(status, ns=(record.stat().st_mtime_ns, record.stat().st_mtime_ns))
        return record

    def make_normalized_release(self) -> tuple[Path, Path, Path, Path]:
        options = tuple(value for value in MODULE.PROFILES["win64"].required_options
                        if value != "--enable-release")
        root, objdir, status = self.make_workspace("win64", options=options)
        record = self.write_input_record(root, objdir, status)
        return root, objdir, status, record

    def make_workspace(
        self,
        platform: str = "linux",
        *,
        options: tuple[str, ...] | None = None,
        substs: dict[str, str] | None = None,
    ) -> tuple[Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "navis").mkdir()
        (root / "platform").mkdir()
        gecko = root / "runtime/gecko"
        gecko.mkdir(parents=True)
        profile = MODULE.PROFILES[platform]
        mozconfig = root / "runtime" / profile.mozconfig_name
        mozconfig.write_text("# candidate profile\n", encoding="utf-8")
        objdir = gecko / profile.object_name
        objdir.mkdir()
        (objdir / "Makefile").write_text("all:\n", encoding="utf-8")
        (objdir / "backend.RecursiveMakeBackend").write_text(
            "backend\n", encoding="utf-8"
        )
        values = {
            "MOZ_BUILD_APP": profile.build_app,
            "MOZ_WIDGET_TOOLKIT": profile.widget,
            "TARGET_CPU": profile.cpu,
            "MOZ_CONFIGURE_OPTIONS": " ".join(options or profile.required_options),
            **dict(profile.required_substs),
        }
        if substs:
            values.update(substs)
        rendered = [
            f"mozconfig = {str(mozconfig)!r}",
            "substs = {",
        ]
        for key, value in sorted(values.items()):
            if key == "TARGET_CPU":
                rendered.append(f"    {key!r}: CPU({value!r}),")
            else:
                rendered.append(f"    {key!r}: {value!r},")
        rendered.extend(
            (
                "}",
                f"topobjdir = {str(objdir)!r}",
                f"topsrcdir = {str(gecko)!r}",
            )
        )
        status = objdir / "config.status"
        status.write_text("\n".join(rendered) + "\n", encoding="utf-8")
        return root, objdir, status

    def test_accepts_configured_candidate_graphs(self) -> None:
        for platform in MODULE.PROFILES:
            with self.subTest(platform=platform):
                root, _, _ = self.make_workspace(platform)
                self.assertEqual(MODULE.verify(platform, "configured", root), [])

    def test_transition_navis_root_resolves_to_canonical_workspace(self) -> None:
        root, _, _ = self.make_workspace("linux")
        self.assertEqual(MODULE.verify("linux", "configured", root / "navis"), [])

    def test_accepts_default_release_from_current_owned_input_record(self) -> None:
        root, _, _, _ = self.make_normalized_release()
        self.assertEqual(MODULE.verify("win64", "configured", root), [])

    def test_rejects_developer_options_even_with_release_branch_or_switch(self) -> None:
        for normalized in (False, True):
            with self.subTest(normalized=normalized):
                root, objdir, status = self.make_workspace("win64", substs={
                    "DEVELOPER_OPTIONS": "1", "RELEASE_OR_BETA": "1",
                })
                if normalized:
                    status.write_text(status.read_text().replace("--enable-release", ""))
                    self.write_input_record(root, objdir, status)
                failures = MODULE.verify("win64", "configured", root)
                self.assertTrue(any("DEVELOPER_OPTIONS" in value for value in failures))

    def test_rejects_missing_or_invalid_release_input_record(self) -> None:
        for mutation in ("missing", "invalid_json", "wrong_owner", "wrong_object",
                         "disabled", "release_no", "stale", "not_yet_configured",
                         "invalid_arguments", "symlink"):
            with self.subTest(mutation=mutation):
                root, objdir, status, record = self.make_normalized_release()
                payload = json.loads(record.read_text())
                if mutation == "missing":
                    record.unlink()
                elif mutation == "invalid_json":
                    record.write_text("{")
                elif mutation == "symlink":
                    target = objdir / "other-record.json"
                    record.rename(target)
                    record.symlink_to(target)
                elif mutation in ("stale", "not_yet_configured"):
                    stamp = (
                        root / "runtime/mozconfig.win64.release"
                    ).stat().st_mtime_ns - 1
                    if mutation == "not_yet_configured":
                        stamp = status.stat().st_mtime_ns + 1
                    os.utime(record, ns=(stamp, stamp))
                else:
                    if mutation == "wrong_owner":
                        payload["mozconfig"]["path"] = str(
                            root / "runtime/mozconfig.runtime"
                        )
                    elif mutation == "wrong_object":
                        payload["topobjdir"] = str(root / "other-object")
                    elif mutation in ("disabled", "release_no"):
                        payload["mozconfig"]["configure_args"].append(
                            "--disable-release" if mutation == "disabled" else "--enable-release=no"
                        )
                    elif mutation == "invalid_arguments":
                        payload["mozconfig"]["configure_args"] = "--enable-release"
                    record.write_text(json.dumps(payload))
                if mutation not in ("missing", "stale", "not_yet_configured"):
                    stamp = record.stat().st_mtime_ns
                    os.utime(status, ns=(stamp, stamp))
                failures = MODULE.verify("win64", "configured", root)
                self.assertTrue(any("--enable-release" in value for value in failures))

    def test_identity_phase_allows_reviewed_target_to_be_reconfigured(self) -> None:
        root, _, _ = self.make_workspace("win64", options=("--disable-release",))
        self.assertEqual(MODULE.verify("win64", "identity", root), [])
        failures = MODULE.verify("win64", "configured", root)
        self.assertTrue(any("--enable-release" in item for item in failures))
        self.assertTrue(any("--disable-release" in item for item in failures))

    def test_missing_linux_release_graph_does_not_reuse_development_graph(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "runtime/gecko/obj-navis-runtime").mkdir(parents=True)
        (root / "runtime/gecko/obj-navis-runtime/config.status").write_text(
            "development\n", encoding="utf-8"
        )
        (root / "runtime/mozconfig.runtime.release").write_text(
            "# release\n", encoding="utf-8"
        )
        failures = MODULE.verify("linux", "identity", root)
        self.assertTrue(any("candidate object directory is missing" in x for x in failures))
        self.assertTrue(any("cannot stand in" in x for x in failures))

    def test_rejects_symlinked_object_directory(self) -> None:
        root, objdir, _ = self.make_workspace("linux")
        moved = objdir.with_name("actual-object")
        objdir.rename(moved)
        objdir.symlink_to(moved, target_is_directory=True)
        failures = MODULE.verify("linux", "identity", root)
        self.assertTrue(any("symlink" in item for item in failures))

    def test_rejects_wrong_config_status_owner_paths(self) -> None:
        root, _, status = self.make_workspace("android")
        status.write_text(
            status.read_text(encoding="utf-8").replace(
                f"mozconfig = {str(root / 'runtime/mozconfig.android-aarch64.sccache')!r}",
                f"mozconfig = {str(root / 'runtime/mozconfig.runtime')!r}",
            ),
            encoding="utf-8",
        )
        failures = MODULE.verify("android", "identity", root)
        self.assertTrue(any("mozconfig differs" in item for item in failures))

    def test_rejects_wrong_target_identity(self) -> None:
        root, _, status = self.make_workspace("android")
        status.write_text(
            status.read_text(encoding="utf-8").replace(
                "'TARGET_CPU': CPU('aarch64')",
                "'TARGET_CPU': CPU('x86_64')",
            ),
            encoding="utf-8",
        )
        failures = MODULE.verify("android", "identity", root)
        self.assertTrue(any("TARGET_CPU differs" in item for item in failures))

    def test_rejects_missing_backend_metadata(self) -> None:
        root, objdir, _ = self.make_workspace("win64")
        (objdir / "backend.RecursiveMakeBackend").unlink()
        failures = MODULE.verify("win64", "identity", root)
        self.assertTrue(any("backend.RecursiveMakeBackend" in item for item in failures))

    def test_rejects_configured_graph_without_sccache(self) -> None:
        profile = MODULE.PROFILES["linux"]
        options = tuple(
            option for option in profile.required_options if option != "--with-ccache=sccache"
        )
        root, _, _ = self.make_workspace(
            "linux", options=options, substs={"MOZ_USING_SCCACHE": ""}
        )
        failures = MODULE.verify("linux", "configured", root)
        self.assertTrue(any("--with-ccache=sccache" in item for item in failures))
        self.assertTrue(any("MOZ_USING_SCCACHE" in item for item in failures))

    def test_rejects_configured_graph_from_another_rust_toolchain(self) -> None:
        root, _, _ = self.make_workspace(
            "android", substs={"RUSTC_VERSION": "1.98.0"}
        )
        failures = MODULE.verify("android", "configured", root)
        self.assertTrue(any("RUSTC_VERSION" in item for item in failures))

    def test_rejects_config_status_older_than_candidate_profile(self) -> None:
        root, _, status = self.make_workspace("linux")
        mozconfig = root / "runtime/mozconfig.runtime.release"
        older = mozconfig.stat().st_mtime_ns - 1_000_000_000
        os.utime(status, ns=(older, older))
        failures = MODULE.verify("linux", "configured", root)
        self.assertTrue(any("predates" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
