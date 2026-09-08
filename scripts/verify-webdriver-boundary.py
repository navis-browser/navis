#!/usr/bin/env python3

"""Verify Navis's content-only WebDriver boundary and automation migration."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import zipfile

WORKSPACE = pathlib.Path(__file__).resolve().parent.parent
PRODUCT_MOZCONFIGS = (
    "../runtime/mozconfig.runtime",
    "../runtime/mozconfig.runtime.release",
    "../runtime/mozconfig.runtime.no-webrtc.sccache",
    "../runtime/mozconfig.win64",
    "../runtime/mozconfig.win64.release",
)
PATCH = WORKSPACE / "../runtime/patches/gecko/0013-restrict-desktop-embedder-webdriver.patch"
TEST_BOUNDARY_PATCH = (
    WORKSPACE / "../runtime/patches/gecko/0020-allow-test-build-system-automation.patch"
)
REALM_LIFECYCLE_PATCH = (
    WORKSPACE / "../runtime/patches/gecko/0037-guard-window-realm-navigation-cleanup.patch"
)
PACKAGE_MANIFEST = WORKSPACE / "../platform/gecko-chrome/installer/package-manifest.in"


def require(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def verify_source(failures: list[str]) -> None:
    for relative in PRODUCT_MOZCONFIGS:
        content = (WORKSPACE / relative).read_text(encoding="utf-8")
        require(
            failures,
            "ac_add_options --enable-webdriver" in content,
            f"{relative} does not explicitly enable WebDriver",
        )
        require(
            failures,
            "ac_add_options --disable-webdriver" not in content,
            f"{relative} still disables WebDriver",
        )
        require(
            failures,
            "ac_add_options --enable-tests" not in content,
            f"{relative} enables Gecko test-only product access",
        )

    test_config = (WORKSPACE / "../runtime/mozconfig.runtime.no-webrtc.tests.sccache").read_text(
        encoding="utf-8"
    )
    require(
        failures,
        "ac_add_options --enable-tests" in test_config,
        "the dedicated Gecko harness build does not enable tests explicitly",
    )

    package_manifest = PACKAGE_MANIFEST.read_text(encoding="utf-8")
    for marker in (
        "#ifdef ENABLE_WEBDRIVER",
        "@RESPATH@/chrome/remote@JAREXT@",
        "@RESPATH@/chrome/remote.manifest",
    ):
        require(
            failures,
            marker in package_manifest,
            f"product package manifest lacks {marker}",
        )

    patch = PATCH.read_text(encoding="utf-8")
    for marker in (
        "!AppConstants.MOZ_DESKTOP_EMBEDDER",
        "if (AppConstants.MOZ_DESKTOP_EMBEDDER)",
        "#ifndef MOZ_DESKTOP_EMBEDDER",
        "Addon:Install",
        "Addon:Uninstall",
        "Marionette:SetContext",
        "modules/root/webExtension.sys.mjs",
        "content/shared/Addon.sys.mjs",
        "DesktopEmbedderTabBrowser",
        "DesktopExtensionTabs.sys.mjs",
        "addEventListener(...args)",
        "removeEventListener(...args)",
        "getMostRecentWindow",
        "Desktop Embedder exposes only the default user context",
        'DIRS += ["/devtools/platform"]',
    ):
        require(
            failures, marker in patch, f"WebDriver restriction patch lacks {marker}"
        )

    test_boundary_patch = TEST_BOUNDARY_PATCH.read_text(encoding="utf-8")
    for marker in (
        "AppConstants.ENABLE_TESTS",
        "extensions.applicationBuiltins.testManagedStorage",
        "Marionette:SetContext",
        "MOZ_DESKTOP_EMBEDDER",
    ):
        require(
            failures,
            marker in test_boundary_patch,
            f"test-build automation boundary patch lacks {marker}",
        )

    realm_lifecycle_patch = REALM_LIFECYCLE_PATCH.read_text(encoding="utf-8")
    for marker in (
        "#innerWindowId",
        "window.windowGlobalChild.innerWindowId",
        "WindowGlobalChild.getByInnerWindowId(this.#innerWindowId)",
        "?.isCurrentGlobal",
        "testExecuteInDefaultRealm",
        "before same-origin navigation",
        "before cross-origin navigation",
    ):
        require(
            failures,
            marker in realm_lifecycle_patch,
            f"WindowRealm lifecycle patch lacks {marker}",
        )


def verify_runtime(runtime: pathlib.Path, failures: list[str]) -> None:
    omni = runtime / "omni.ja"
    archive: zipfile.ZipFile | None = None
    if omni.is_file():
        archive = zipfile.ZipFile(omni)
        names = set(archive.namelist())

        def read_entry(name: str) -> bytes:
            assert archive is not None
            return archive.read(name)

    elif (runtime / "chrome" / "remote").is_dir():
        names = {
            path.relative_to(runtime).as_posix()
            for path in runtime.rglob("*")
            if path.is_file()
        }

        def read_entry(name: str) -> bytes:
            return (runtime / name).read_bytes()

    else:
        failures.append(
            f"runtime has neither an omnijar nor an unpacked remote tree: {runtime}"
        )
        return

    try:
        required = {
            "chrome/remote/content/components/Marionette.sys.mjs",
            "chrome/remote/content/components/RemoteAgent.sys.mjs",
            "chrome/remote/content/marionette/driver.sys.mjs",
            "chrome/remote/content/shared/TabManager.sys.mjs",
            "chrome/remote/content/shared/UserContextManager.sys.mjs",
            "chrome/remote/content/shared/Realm.sys.mjs",
            "chrome/toolkit/content/extensions/parent/ext-storage.js",
            "modules/AppConstants.sys.mjs",
            "modules/jsdebugger.sys.mjs",
            "chrome/remote/content/webdriver-bidi/WebDriverBiDi.sys.mjs",
            "chrome/remote/content/webdriver-bidi/modules/ModuleRegistry.sys.mjs",
        }
        forbidden = {
            "chrome/remote/content/shared/Addon.sys.mjs",
            "chrome/remote/content/webdriver-bidi/modules/root/webExtension.sys.mjs",
        }
        for name in sorted(required - names):
            failures.append(f"runtime WebDriver module is missing: {name}")
        for name in sorted(forbidden & names):
            failures.append(f"runtime contains an extension mutation module: {name}")

        if required <= names:
            driver = read_entry(
                "chrome/remote/content/marionette/driver.sys.mjs"
            ).decode("utf-8")
            registry = read_entry(
                "chrome/remote/content/webdriver-bidi/modules/ModuleRegistry.sys.mjs"
            ).decode("utf-8")
            agent = read_entry(
                "chrome/remote/content/components/RemoteAgent.sys.mjs"
            ).decode("utf-8")
            tab_manager = read_entry(
                "chrome/remote/content/shared/TabManager.sys.mjs"
            ).decode("utf-8")
            user_context_manager = read_entry(
                "chrome/remote/content/shared/UserContextManager.sys.mjs"
            ).decode("utf-8")
            realm = read_entry("chrome/remote/content/shared/Realm.sys.mjs").decode(
                "utf-8"
            )
            app_constants = read_entry("modules/AppConstants.sys.mjs").decode("utf-8")
            extension_storage = read_entry(
                "chrome/toolkit/content/extensions/parent/ext-storage.js"
            ).decode("utf-8")
            for pattern, description in (
                (
                    (
                        r"\.\.\.\(AppConstants\.MOZ_DESKTOP_EMBEDDER\s*"
                        r"\?\s*\{\}\s*:\s*\{\s*Addon:"
                    ),
                    "the shared Addon helper getter",
                ),
                (
                    (
                        r"if \(!AppConstants\.MOZ_DESKTOP_EMBEDDER\)\s*"
                        r"\{\s*lazy\.Addon\.cleanupTemporaryAddonFiles\(\);"
                    ),
                    "temporary add-on cleanup",
                ),
                (
                    (
                        r"\.\.\.\(AppConstants\.MOZ_DESKTOP_EMBEDDER\s*"
                        r"\?\s*\{\}\s*:\s*\{[\s\S]*?"
                        r'"Addon:Install"[\s\S]*?"Addon:Uninstall"'
                    ),
                    "Marionette add-on commands",
                ),
            ):
                require(
                    failures,
                    re.search(pattern, driver) is not None,
                    f"Marionette does not gate {description}",
                )
            require(
                failures,
                '"Marionette:SetContext": GeckoDriver.prototype.setContext' in driver,
                "Marionette does not retain the content context command",
            )
            require(
                failures,
                re.search(
                    r"\.\.\.\(AppConstants\.MOZ_DESKTOP_EMBEDDER\s*"
                    r"\?\s*\{\}\s*:\s*\{\s*webExtension:",
                    registry,
                )
                is not None,
                "WebDriver BiDi does not gate the webExtension module",
            )
            require(
                failures,
                re.search(
                    r"\(!AppConstants\.MOZ_DESKTOP_EMBEDDER\s*\|\|\s*"
                    r"AppConstants\.ENABLE_TESTS\)",
                    agent,
                )
                is not None
                and re.search(
                    r"if \(AppConstants\.MOZ_DESKTOP_EMBEDDER\s*&&\s*"
                    r"!AppConstants\.ENABLE_TESTS\)",
                    agent,
                )
                is not None,
                "Remote Agent does not separate test-only system access from production",
            )
            require(
                failures,
                re.search(r"ENABLE_TESTS:[\s\S]{0,120}?\bfalse,", app_constants)
                is not None,
                "production AppConstants does not prove ENABLE_TESTS=false",
            )
            require(
                failures,
                re.search(
                    r"!AppConstants\.MOZ_DESKTOP_EMBEDDER\s*\|\|\s*"
                    r"!AppConstants\.ENABLE_TESTS",
                    extension_storage,
                )
                is not None,
                "managed extension test policy is not compile-time gated from production",
            )
            for marker in (
                "class DesktopEmbedderTabBrowser",
                "DesktopExtensionTabs.sys.mjs",
                "getMostRecentWindow()",
                "AppConstants.MOZ_DESKTOP_EMBEDDER ||",
            ):
                require(
                    failures,
                    marker in tab_manager,
                    f"Desktop Embedder WebDriver tab adapter lacks {marker}",
                )
            for marker in (
                "AppConstants.MOZ_DESKTOP_EMBEDDER",
                "Desktop Embedder exposes only the default user context",
                "if (!AppConstants.MOZ_DESKTOP_EMBEDDER)",
            ):
                require(
                    failures,
                    marker in user_context_manager,
                    f"Desktop Embedder user-context boundary lacks {marker}",
                )
            for marker in (
                "#innerWindowId",
                "window.windowGlobalChild.innerWindowId",
                "WindowGlobalChild.getByInnerWindowId(this.#innerWindowId)",
                "?.isCurrentGlobal",
            ):
                require(
                    failures,
                    marker in realm,
                    f"packaged WindowRealm navigation guard lacks {marker}",
                )
    finally:
        if archive is not None:
            archive.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=pathlib.Path)
    args = parser.parse_args()

    failures: list[str] = []
    verify_source(failures)
    if args.runtime is not None:
        verify_runtime(args.runtime.resolve(), failures)

    if failures:
        print("Navis WebDriver boundary verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("Navis WebDriver boundary verified.")
    print("- direct loopback WebDriver BiDi: retained")
    print("- privileged parent-process access: rejected")
    print("- Marionette/BiDi extension mutation commands: absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
