from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
VERIFIER = ROOT / "scripts/verify-webauthn.py"
class WebAuthnPolicyTests(unittest.TestCase):
    def run_verifier(
        self, runtime: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        command = ["python3", str(VERIFIER)]
        if runtime is not None:
            command.extend(["--runtime", str(runtime)])
        return subprocess.run(command, cwd=ROOT, text=True, capture_output=True)

    def make_runtime(
        self,
        root: Path,
        *,
        conditional_binding: bool = True,
        related_hook: bool = True,
    ) -> Path:
        required = {
            "modules/DesktopWebAuthnPrompt.sys.mjs": (
                'const WEB_AUTHN_TOPIC = "webauthn-prompt";'
            ),
            "modules/DesktopCredentialChild.sys.mjs": (
                '"DesktopCredential:ConditionalWebAuthn"'
                if conditional_binding
                else "credential-only"
            ),
            "modules/DesktopCredentialParent.sys.mjs": (
                (
                    'QueryInterface: ChromeUtils.generateQI(["nsIObserver"]),\n'
                    "selectAutoFillEntry"
                )
                if conditional_binding
                else "credential-only"
            ),
            "modules/WebAuthnRelatedOriginFetcher.sys.mjs": (
                "desktopEmbedderWebAuthnRelatedOriginPrompt"
                if related_hook
                else "upstream-only"
            ),
            "modules/DesktopEngine.sys.mjs": "webAuthn: true",
            "defaults/pref/navis.js": (
                'pref("security.webauth.webauthn", true, locked);\n'
                'pref("security.webauthn.enable_conditional_mediation", true, locked);\n'
                'pref("security.webauth.webauthn_enable_softtoken", false, locked);\n'
                'pref("security.webauth.webauthn_enable_usbtoken", true, locked);'
            ),
            "chrome/navis/content/main.mjs": "webAuthnResolvers",
            "chrome/navis/content/locales/en-US.mjs": '"webauthn.title"',
            "chrome/navis/content/locales/zh-CN.mjs": '"webauthn.title"',
        }
        with zipfile.ZipFile(root / "omni.ja", "w") as archive:
            for name, source in required.items():
                archive.writestr(name, source)
        return root

    def test_source_policy_is_self_consistent(self) -> None:
        result = self.run_verifier()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bounded_runtime_inventory_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.make_runtime(Path(temporary))
            result = self.run_verifier(runtime)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_without_related_origin_hook_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.make_runtime(Path(temporary), related_hook=False)
            result = self.run_verifier(runtime)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("runtime related-origin hook is absent", result.stderr)

    def test_runtime_without_conditional_binding_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = self.make_runtime(Path(temporary), conditional_binding=False)
            result = self.run_verifier(runtime)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "runtime conditional WebAuthn binding is absent", result.stderr
            )



if __name__ == "__main__":
    unittest.main()
