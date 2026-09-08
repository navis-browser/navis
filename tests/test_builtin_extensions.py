from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import zipfile


WORKSPACE = pathlib.Path(__file__).resolve().parents[1]
VERIFIER = WORKSPACE / "scripts" / "verify-builtin-extensions.py"
SIGNATURE_FILES = (
    "META-INF/cose.manifest",
    "META-INF/cose.sig",
    "META-INF/manifest.mf",
    "META-INF/mozilla.rsa",
    "META-INF/mozilla.sf",
)


class BuiltinExtensionVerifierTests(unittest.TestCase):
    def make_extension(
        self,
        root: pathlib.Path,
        *,
        addon_id: str,
        name: str,
        default_toolbar_pinned: bool,
        permissions: list[str] | None = None,
    ) -> tuple[dict, str]:
        directory_name = addon_id.replace("@", "-").replace(".", "-")
        extension_dir = root / "product" / "builtin" / directory_name
        extension_dir.mkdir(parents=True)
        artifact = extension_dir / f"{addon_id}.xpi"
        manifest = {
            "manifest_version": 2,
            "name": name,
            "version": "1.0",
            "permissions": permissions or [],
            "browser_specific_settings": {"gecko": {"id": addon_id}},
        }
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("LICENSE.txt", "test license")
            for signature in SIGNATURE_FILES:
                archive.writestr(signature, "signature fixture")

        payload = artifact.read_bytes()
        metadata = {
            "schema": 1,
            "id": addon_id,
            "name": name,
            "version": "1.0",
            "artifact": artifact.name,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "release": "https://example.test/release",
            "download": "https://example.test/download.xpi",
            "source_repository": "https://example.test/source",
            "source_commit": "a" * 40,
            "license": "MPL-2.0",
            "required_permissions": permissions or [],
        }
        metadata_path = extension_dir / "metadata.json"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return (
            {
                "required": True,
                "default_toolbar_pinned": default_toolbar_pinned,
                "metadata": f"{directory_name}/metadata.json",
            },
            f'{directory_name}/{artifact.name}',
        )

    def make_workspace(
        self, permissions: list[str] | None = None
    ) -> tuple[pathlib.Path, pathlib.Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = pathlib.Path(temporary.name)
        entries = []
        artifacts = []
        for addon_id, name, default_pinned in (
            ("builtin-one@example.test", "Built-in One", True),
            ("companion@example.test", "Companion", False),
        ):
            entry, artifact = self.make_extension(
                root,
                addon_id=addon_id,
                name=name,
                default_toolbar_pinned=default_pinned,
                permissions=permissions if addon_id == "companion@example.test" else None,
            )
            entries.append(entry)
            artifacts.append(artifact)

        builtin = root / "product" / "builtin"
        registry = {"schema": 3, "extensions": entries}
        (builtin / "extensions.json").write_text(
            json.dumps(registry), encoding="utf-8"
        )
        (builtin / "moz.build").write_text(
            "\n".join(f'    "{artifact}",' for artifact in artifacts),
            encoding="utf-8",
        )
        profile = root / "product" / "app" / "profile" / "navis.js"
        profile.parent.mkdir(parents=True)
        profile.write_text(
            'pref("extensions.applicationBuiltins.allowedIds", '
            '"builtin-one@example.test,companion@example.test", locked);\n'
            'pref("navis.extensions.toolbar.pinned", '
            '"[\\"builtin-one@example.test\\"]");\n',
            encoding="utf-8",
        )
        installer = root / "product" / "installer" / "package-manifest.in"
        installer.parent.mkdir(parents=True)
        installer.write_text(
            "@RESPATH@/extensions/*.xpi\n"
            "@RESPATH@/builtin-extensions/extensions.json\n",
            encoding="utf-8",
        )
        for mozconfig_name in (
            "mozconfig.runtime",
            "mozconfig.runtime.release",
            "mozconfig.runtime.no-webrtc.sccache",
            "mozconfig.runtime.no-webrtc.tests.sccache",
            "mozconfig.win64",
            "mozconfig.win64.release",
        ):
            (root / mozconfig_name).write_text(
                "ac_add_options --enable-webextensions-runtime\n",
                encoding="utf-8",
            )

        package = root / "package"
        (package / "extensions").mkdir(parents=True)
        (package / "builtin-extensions").mkdir()
        (package / "builtin-extensions" / "extensions.json").write_bytes(
            (builtin / "extensions.json").read_bytes()
        )
        for entry in entries:
            metadata_path = builtin / entry["metadata"]
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            source = metadata_path.parent / metadata["artifact"]
            (package / "extensions" / source.name).write_bytes(source.read_bytes())
        return root, package

    def run_verifier(
        self, root: pathlib.Path, package: pathlib.Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(VERIFIER), str(root)]
        if package is not None:
            command.extend(("--package-root", str(package)))
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def add_application_locale(
        self, root: pathlib.Path, package: pathlib.Path
    ) -> tuple[pathlib.Path, pathlib.Path]:
        addon_id = "langpack-zh-CN@firefox.mozilla.org"
        artifact_name = f"{addon_id}.xpi"
        locale_dir = root / "product" / "locales" / "zh-CN"
        locale_dir.mkdir(parents=True)
        source = locale_dir / artifact_name
        source.write_bytes(b"official Mozilla zh-CN locale fixture")
        payload = source.read_bytes()
        (locale_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "locale": "zh-CN",
                    "id": addon_id,
                    "artifact": artifact_name,
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        locale_registry = locale_dir.parent / "locales.json"
        locale_registry.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "locales": [
                        {"required": True, "metadata": "zh-CN/metadata.json"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        profile = root / "product" / "app" / "profile" / "navis.js"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace(
                "builtin-one@example.test,companion@example.test",
                f"builtin-one@example.test,companion@example.test,{addon_id}",
            ),
            encoding="utf-8",
        )
        packaged = package / "extensions" / artifact_name
        packaged.write_bytes(payload)
        return source, packaged

    def test_registry_and_package_support_multiple_builtins(self) -> None:
        root, package = self.make_workspace()
        result = self.run_verifier(root, package)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "builtin-one@example.test 1.0 default-pinned=true", result.stdout
        )
        self.assertIn(
            "companion@example.test 1.0 default-pinned=false", result.stdout
        )

    def test_package_rejects_an_unregistered_xpi(self) -> None:
        root, package = self.make_workspace()
        (package / "extensions" / "unregistered@example.test.xpi").write_bytes(
            b"not reviewed"
        )
        result = self.run_verifier(root, package)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "packaged application XPI set differs from reviewed registries",
            result.stderr,
        )

    def test_package_accepts_byte_identical_application_locale(self) -> None:
        root, package = self.make_workspace()
        self.add_application_locale(root, package)
        result = self.run_verifier(root, package)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "Application locale verified: langpack-zh-CN@firefox.mozilla.org",
            result.stdout,
        )

    def test_package_rejects_modified_application_locale(self) -> None:
        root, package = self.make_workspace()
        _source, packaged = self.add_application_locale(root, package)
        payload = bytearray(packaged.read_bytes())
        payload[-1] ^= 1
        packaged.write_bytes(payload)
        result = self.run_verifier(root, package)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("packaged application locale hash differs", result.stderr)

    def test_registry_rejects_native_messaging_permission(self) -> None:
        root, _package = self.make_workspace(["nativeMessaging"])
        result = self.run_verifier(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("forbidden nativeMessaging permission", result.stderr)

    def test_production_profile_cannot_disable_builtin_runtime(self) -> None:
        root, _package = self.make_workspace()
        (root / "mozconfig.win64").write_text(
            "ac_add_options --disable-webextensions-runtime\n",
            encoding="utf-8",
        )
        result = self.run_verifier(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "production profile does not retain built-ins: mozconfig.win64",
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
