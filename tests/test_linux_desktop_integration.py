#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/register-linux-desktop-integration.py"
SPEC = importlib.util.spec_from_file_location("register_linux_desktop_integration", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
INTEGRATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = INTEGRATION
SPEC.loader.exec_module(INTEGRATION)


class LinuxDesktopIntegrationTests(unittest.TestCase):
    def make_runtime(self, parent: Path, remoting_name: str = "navis-default") -> Path:
        runtime = parent / "portable Navis"
        icon_root = runtime / "chrome/icons/default"
        icon_root.mkdir(parents=True)
        binary = runtime / "navis"
        binary.write_bytes(b"#!/bin/sh\n")
        binary.chmod(0o755)
        (runtime / "application.ini").write_text(
            "[App]\nName=Navis\n" f"RemotingName={remoting_name}\n",
            encoding="utf-8",
        )
        generated = ROOT / "product/branding/generated"
        for size in INTEGRATION.ICON_SIZES:
            shutil.copyfile(generated / f"default{size}.png", icon_root / f"default{size}.png")
        return runtime

    def test_registers_matching_entry_and_all_hicolor_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.make_runtime(root)
            data_home = root / "xdg-data"
            desktop, icons, warnings = INTEGRATION.install(
                runtime, data_home, refresh=False
            )

            self.assertEqual(desktop.name, "navis-default.desktop")
            content = desktop.read_text(encoding="utf-8")
            self.assertIn("Name=Navis\n", content)
            self.assertIn("Icon=navis\n", content)
            self.assertIn("StartupWMClass=navis-default\n", content)
            self.assertIn(f'Exec="{runtime / "navis"}" %u\n', content)
            self.assertIn(INTEGRATION.MANAGED_MARKER, content)
            self.assertEqual(warnings, [])
            self.assertEqual(len(icons), len(INTEGRATION.ICON_SIZES))
            for path, size in zip(icons, INTEGRATION.ICON_SIZES, strict=True):
                self.assertEqual(
                    path.read_bytes(),
                    (runtime / "chrome/icons/default" / f"default{size}.png").read_bytes(),
                )

    def test_rejects_an_unmanaged_desktop_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.make_runtime(root)
            desktop = root / "xdg-data/applications/navis-default.desktop"
            desktop.parent.mkdir(parents=True)
            desktop.write_text("[Desktop Entry]\nName=Someone else\n", encoding="utf-8")
            with self.assertRaisesRegex(INTEGRATION.IntegrationError, "unmanaged"):
                INTEGRATION.install(runtime, root / "xdg-data", refresh=False)

    def test_rejects_invalid_runtime_identity_and_icon_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.make_runtime(root, "../unsafe")
            with self.assertRaisesRegex(INTEGRATION.IntegrationError, "unsafe"):
                INTEGRATION.install(runtime, root / "xdg-data", refresh=False)

            runtime = self.make_runtime(root / "second")
            shutil.copyfile(
                ROOT / "product/branding/generated/default32.png",
                runtime / "chrome/icons/default/default16.png",
            )
            with self.assertRaisesRegex(INTEGRATION.IntegrationError, "expected 16x16"):
                INTEGRATION.install(runtime, root / "xdg-data", refresh=False)


if __name__ == "__main__":
    unittest.main()
