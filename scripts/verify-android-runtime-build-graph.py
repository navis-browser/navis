#!/usr/bin/env python3

"""Verify that Navis Android owns its Gradle and staged-engine build graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
PORT_ID = "direct-navis-android-runtime-build-graph"
PORT_PATH = "patches/gecko/0046-own-navis-android-runtime-build-graph.patch"
WORKSPACE_PORT_PATH = f"runtime/{PORT_PATH}"
PRIMITIVE_LIST_PATH = "runtime/android/gecko-java-primitives.list"
GECKO_JAVA_SOURCE_ROOT = (
    "runtime/gecko/mobile/android/geckoview/src/main/java/org/mozilla/gecko"
)
ACCESSIBILITY_PRIMITIVE_PATH = (
    "runtime/gecko/mobile/android/geckoview/src/main/java/org/mozilla/geckoview/"
    "AndroidAccessibilityBridge.java"
)
WEB_EXECUTOR_PRIMITIVE_PATH = (
    "runtime/gecko/mobile/android/geckoview/src/main/java/org/mozilla/geckoview/"
    "AndroidWebExecutorBridge.java"
)
WEB_EXECUTOR_NATIVE_PATH = "runtime/gecko/widget/android/WebExecutorSupport.cpp"
FORBIDDEN_LIFECYCLE_SOURCES = (
    "org/mozilla/geckoview/GeckoRuntime.java",
    "org/mozilla/geckoview/GeckoSession.java",
    "org/mozilla/geckoview/GeckoView.java",
    "org/mozilla/geckoview/GeckoDisplay.java",
    "org/mozilla/geckoview/SessionAccessibility.java",
)
FORBIDDEN_DIRECT_OWNER_SOURCES = (
    "org/mozilla/geckoview/GeckoWebExecutor.java",
    "org/mozilla/geckoview/WebAuthnTokenManager.java",
)
REVIEWED_GECKOVIEW_PRIMITIVES = frozenset(
    {
        "org/mozilla/geckoview/AllowOrDeny.java",
        "org/mozilla/geckoview/AndroidAccessibilityBridge.java",
        "org/mozilla/geckoview/AndroidWebExecutorBridge.java",
        "org/mozilla/geckoview/Base64Utils.java",
        "org/mozilla/geckoview/ContentInputStream.java",
        "org/mozilla/geckoview/CrashHandler.java",
        "org/mozilla/geckoview/CrashReport.java",
        "org/mozilla/geckoview/GeckoInputStream.java",
        "org/mozilla/geckoview/GeckoResult.java",
        "org/mozilla/geckoview/GeckoVRManager.java",
        "org/mozilla/geckoview/GeckoViewInputStream.java",
        "org/mozilla/geckoview/Image.java",
        "org/mozilla/geckoview/NavisAndroidAccessibility.java",
        "org/mozilla/geckoview/NavisAndroidWebExecutor.java",
        "org/mozilla/geckoview/WebMessage.java",
        "org/mozilla/geckoview/WebNotification.java",
        "org/mozilla/geckoview/WebNotificationAction.java",
        "org/mozilla/geckoview/WebRequest.java",
        "org/mozilla/geckoview/WebRequestError.java",
        "org/mozilla/geckoview/WebResponse.java",
        "org/mozilla/geckoview/internal/NavisCoreBridge.java",
        "org/mozilla/geckoview/internal/NavisCoreSnapshot.java",
    }
)
LEGACY_WRAPPER_STEMS = (
    "GeckoRuntime",
    "GeckoSession",
    "GeckoWebExecutor",
    "PanZoomController",
    "SessionTextInput",
    "WebAuthnTokenManager",
)
NAVIS_WRAPPER_STEMS = (
    "NavisAndroidRuntime",
    "NavisAndroidSession",
)
FORBIDDEN_PRODUCT_OWNER_TYPES = (
    "GeckoDisplay",
    "GeckoRuntime",
    "GeckoSession",
    "GeckoView",
    "GeckoWebExecutor",
    "PanZoomController",
    "SessionAccessibility",
    "SessionTextInput",
    "WebAuthnTokenManager",
)


def read(root: Path, relative: str, failures: list[str]) -> str:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except OSError as error:
        failures.append(f"cannot read {relative}: {error}")
        return ""


def require(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def require_markers(
    failures: list[str], label: str, source: str, markers: tuple[str, ...]
) -> None:
    for marker in markers:
        require(failures, marker in source, f"{label} lacks {marker}")


def without_comments(source: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)


def verify_port(
    root: Path, failures: list[str], ledger_source: str, patch_source: str
) -> None:
    require_markers(
        failures,
        PORT_PATH,
        patch_source,
        (
            "diff --git a/settings.gradle b/settings.gradle",
            "diff --git a/mobile/android/gradle.configure b/mobile/android/gradle.configure",
            "diff --git a/mobile/android/installer/Makefile.in b/mobile/android/installer/Makefile.in",
            "diff --git a/mobile/android/moz.build b/mobile/android/moz.build",
            "MachTasksPlugin.kt",
            "navis-runtime-android:generateSDKBindings",
            "dist/${stageProject}",
            "MOZ_PKG_DIR = navis-android",
            "@BINPATH@/@PREF_DIR@/@ANDROID_CPU_ARCH@/navis-prefs.js",
        ),
    )
    try:
        ledger = json.loads(ledger_source)
        ports = ledger.get("ports", ledger)
        entries = [entry for entry in ports if entry.get("id") == PORT_ID]
        require(
            failures,
            len(entries) == 1,
            f"semantic port {PORT_ID} is missing or ambiguous",
        )
        if len(entries) == 1:
            digest = hashlib.sha256((root / WORKSPACE_PORT_PATH).read_bytes()).hexdigest()
            require(
                failures,
                entries[0].get("patch") == PORT_PATH,
                f"semantic port {PORT_ID} points at the wrong patch",
            )
            require(
                failures,
                entries[0].get("sha256") == digest,
                f"semantic port {PORT_ID} hash does not own its patch bytes",
            )
    except (OSError, json.JSONDecodeError, AttributeError, TypeError) as error:
        failures.append(f"cannot verify semantic port {PORT_ID}: {error}")


def verify_settings(failures: list[str], settings: str) -> None:
    require_markers(
        failures,
        "gecko/settings.gradle",
        settings,
        (
            'def isNavisAndroid = gradle.ext.mozconfig.substs.MOZ_ANDROID_SUBPROJECT == "navis"',
            "include ':annotations'",
            "include ':navis-runtime-android'",
            '"${gradle.mozconfig.topsrcdir}/navis-runtime-android"',
            "include ':navis'",
        ),
    )
    selection = re.search(
        r"if \(isNavisAndroid\) \{(?P<navis>.*?)\}\s*else \{(?P<upstream>.*?)\}",
        settings,
        re.DOTALL,
    )
    require(
        failures,
        selection is not None,
        "settings.gradle lacks an explicit Navis/upstream project split",
    )
    if selection is None:
        return
    navis = selection.group("navis")
    upstream = selection.group("upstream")
    require(
        failures,
        "include ':navis-runtime-android'" in navis and "include ':navis'" in navis,
        "Navis settings branch does not include both owned projects",
    )
    for forbidden in (":geckoview", ":android-components", ":messaging_example"):
        require(
            failures,
            forbidden not in navis,
            f"Navis settings branch includes foreign project {forbidden}",
        )
    require(
        failures,
        "include ':geckoview'" in upstream,
        "upstream settings branch lost GeckoView",
    )
    require(
        failures,
        settings.count("include ':geckoview'") == 1,
        "GeckoView is included outside the isolated upstream settings branch",
    )


def verify_modules(
    failures: list[str],
    app_build: str,
    runtime_build: str,
    primitive_list: str,
    proguard: str,
) -> None:
    require_markers(
        failures,
        "android/build.gradle",
        app_build,
        (
            "def navisWorkspaceRoot = project.projectDir.canonicalFile.parentFile.parentFile",
            '"platform/gecko-chrome/config/${fileName}"',
            "'platform/gecko-chrome/builtin/ublock-origin/uBlock0@raymondhill.net.xpi'",
        ),
    )
    for legacy_product_path in ("product/config/", "product/builtin/"):
        require(
            failures,
            legacy_product_path not in app_build,
            "Navis Android application still reads the desktop product through "
            f"the transition view: {legacy_product_path}",
        )
    require(
        failures,
        app_build.count("implementation project(':navis-runtime-android')") == 1,
        "Navis application does not have exactly one owned runtime dependency",
    )
    for forbidden in (
        "implementation project(':geckoview')",
        "org.mozilla.geckoview:geckoview",
        ".aar",
    ):
        require(
            failures,
            forbidden not in app_build,
            f"Navis application still consumes GeckoView packaging: {forbidden}",
        )

    for forbidden in (
        "libs.play.services.",
        "com.google.android.gms:",
        "com.google.firebase:",
    ):
        require(
            failures,
            forbidden not in app_build and forbidden not in runtime_build,
            "Navis Android ownership graph imports Google Mobile Services: "
            f"{forbidden}",
        )

    require_markers(
        failures,
        "android-runtime/build.gradle",
        runtime_build,
        (
            "alias(libs.plugins.kotlin.android)",
            "id 'com.android.library'",
            "evaluationDependsOn(':annotations')",
            "geckoPrimitiveSourceIncludes = file('gecko-java-primitives.list')",
            "tasks.register('stageGeckoPrimitiveSources', Sync)",
            "generated/source/navisGeckoPrimitives",
            'from("${topsrcdir}/mobile/android/geckoview/src/main/java")',
            'from("${topobjdir}/mobile/android/navis-runtime-android/src/main/java")',
            "duplicatesStrategy = DuplicatesStrategy.FAIL",
            "srcDir stagedGeckoPrimitiveSources",
            "include geckoPrimitiveSourceIncludes",
            "dependsOn stageGeckoPrimitiveSources",
            'aidl.srcDir "${topsrcdir}/mobile/android/geckoview/src/main/aidl"',
            'assets.srcDir "${topobjdir}/dist/navis-android/assets"',
            'jniLibs.srcDir "${topobjdir}/dist/navis-android/lib"',
            "consumerProguardFiles 'proguard-rules.pro'",
            "configureLibraryVariantWithJNIWrappers(",
            "GRADLE_ANDROID_NAVIS_RUNTIME_VARIANT_NAME",
            "tasks.register('generateSDKBindings', JavaExec)",
            "exclude 'org/mozilla/geckoview/GeckoRuntime.java'",
            "exclude 'org/mozilla/geckoview/GeckoSession.java'",
            "exclude 'org/mozilla/geckoview/GeckoView.java'",
            "exclude 'org/mozilla/geckoview/GeckoDisplay.java'",
            "exclude 'org/mozilla/geckoview/SessionAccessibility.java'",
            "exclude 'org/mozilla/geckoview/GeckoWebExecutor.java'",
            "exclude 'org/mozilla/geckoview/PanZoomController.java'",
            "exclude 'org/mozilla/geckoview/SessionTextInput.java'",
            "exclude 'org/mozilla/geckoview/WebAuthnTokenManager.java'",
            "exclude 'org/mozilla/geckoview/WebExtensionController.java'",
            "aidl.include 'org/mozilla/gecko/**/*.aidl'",
        ),
    )
    for forbidden in (
        "project(':geckoview')",
        "org.mozilla.geckoview:geckoview",
        "dist/geckoview",
        "useLibrary 'android.test",
        'srcDir "${topsrcdir}/mobile/android/geckoview/src/main/java"',
        'srcDir "${topobjdir}/mobile/android/navis-runtime-android/src/main/java"',
    ):
        require(
            failures,
            forbidden not in runtime_build,
            f"owned runtime module retains a GeckoView packaging dependency: {forbidden}",
        )
    require(
        failures,
        runtime_build.count("include geckoPrimitiveSourceIncludes") == 3,
        "runtime staging lacks complete include geckoPrimitiveSourceIncludes coverage",
    )
    require(
        failures,
        "-keep class org.mozilla.geckoview.**" not in proguard,
        "runtime shrinker policy blanket-keeps the historical GeckoView API",
    )

    source_entries = [
        line.strip()
        for line in primitive_list.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    source_patterns = set(source_entries)
    require(
        failures,
        len(source_entries) == len(source_patterns),
        "runtime primitive list contains duplicate source patterns",
    )
    require(
        failures,
        "org/mozilla/gecko/**/*.java" in source_patterns,
        "runtime primitive list lacks the low-level org.mozilla.gecko JNI substrate",
    )
    require(
        failures,
        "org/navis/**/*.java" in source_patterns,
        "runtime primitive list lacks Navis-owned Java peers",
    )
    geckoview_patterns = {
        pattern
        for pattern in source_patterns
        if pattern.startswith("org/mozilla/geckoview/")
    }
    require(
        failures,
        geckoview_patterns == REVIEWED_GECKOVIEW_PRIMITIVES,
        "runtime primitive list differs from the reviewed exact GeckoView closure: "
        f"unexpected={sorted(geckoview_patterns - REVIEWED_GECKOVIEW_PRIMITIVES)}, "
        f"missing={sorted(REVIEWED_GECKOVIEW_PRIMITIVES - geckoview_patterns)}",
    )
    for required in (
        "org/mozilla/geckoview/AndroidAccessibilityBridge.java",
        "org/mozilla/geckoview/AndroidWebExecutorBridge.java",
        "org/mozilla/geckoview/NavisAndroidWebExecutor.java",
        "org/mozilla/geckoview/GeckoResult.java",
        "org/mozilla/geckoview/GeckoVRManager.java",
        "org/mozilla/geckoview/CrashHandler.java",
        "org/mozilla/geckoview/internal/NavisCoreBridge.java",
    ):
        require(
            failures,
            required in source_patterns,
            f"runtime primitive list lacks required engine helper {required}",
        )
    for pattern in source_patterns:
        require(
            failures,
            pattern.endswith(".java")
            and not pattern.startswith("/")
            and ".." not in Path(pattern).parts
            and "\\" not in pattern,
            f"runtime primitive list has an invalid source pattern: {pattern}",
        )
        require(
            failures,
            not (
                pattern.startswith("org/mozilla/geckoview/")
                and ("*" in pattern or "?" in pattern or "[" in pattern)
            ),
            "runtime primitive list broadly imports the historical GeckoView API: "
            f"{pattern}",
        )
    for forbidden in FORBIDDEN_LIFECYCLE_SOURCES:
        require(
            failures,
            forbidden not in source_patterns,
            f"runtime primitive list includes forbidden lifecycle owner {forbidden}",
        )
    for forbidden in FORBIDDEN_DIRECT_OWNER_SOURCES:
        require(
            failures,
            forbidden not in source_patterns,
            f"runtime primitive list includes forbidden direct owner {forbidden}",
        )


def verify_gecko_primitive_dependencies(root: Path, failures: list[str]) -> None:
    source_root = root / GECKO_JAVA_SOURCE_ROOT
    if not source_root.is_dir():
        return
    reviewed = {
        Path(path).stem for path in REVIEWED_GECKOVIEW_PRIMITIVES
    } | {"BuildConfig", "R"}
    for path in source_root.rglob("*.java"):
        source = path.read_text(encoding="utf-8")
        google_imports = sorted(
            set(
                re.findall(
                    r"^\s*import\s+com\.google\.(?:android\.gms|firebase)\.[^;]+",
                    source,
                    flags=re.MULTILINE,
                )
            )
        )
        require(
            failures,
            not google_imports,
            f"{path.relative_to(root).as_posix()} imports Google Mobile Services "
            f"inside the Navis runtime primitive closure: {google_imports}",
        )
        imported = set(
            re.findall(
                r"^\s*import\s+(?:static\s+)?org\.mozilla\.geckoview\.([A-Za-z_$][\w$]*)",
                source,
                flags=re.MULTILINE,
            )
        )
        unexpected = sorted(imported - reviewed)
        require(
            failures,
            not unexpected,
            f"{path.relative_to(root).as_posix()} imports GeckoView types outside "
            f"the staged primitive closure: {unexpected}",
        )


def verify_wrapper_graph(failures: list[str], widget_mozbuild: str) -> None:
    legacy_selection = re.search(
        r'if not CONFIG\["MOZ_NAVIS_ANDROID_RUNTIME"\]:\s+'
        r"classes_with_WrapForJNI \+= \[(?P<body>.*?)\]",
        widget_mozbuild,
        re.DOTALL,
    )
    require(
        failures,
        legacy_selection is not None,
        "widget Android wrapper graph lacks a non-Navis lifecycle-owner branch",
    )
    legacy_body = legacy_selection.group("body") if legacy_selection else ""
    for stem in LEGACY_WRAPPER_STEMS:
        marker = f'"{stem}"'
        require(
            failures,
            marker in legacy_body and widget_mozbuild.count(marker) == 1,
            f"legacy Android wrapper {stem} is not isolated from Navis",
        )

    navis_selection = re.search(
        r'if CONFIG\["MOZ_NAVIS_ANDROID_RUNTIME"\]:\s+'
        r"classes_with_WrapForJNI \+= \[(?P<body>.*?)\]",
        widget_mozbuild,
        re.DOTALL,
    )
    require(
        failures,
        navis_selection is not None,
        "widget Android wrapper graph lacks a Navis-owned peer branch",
    )
    navis_body = navis_selection.group("body") if navis_selection else ""
    for stem in NAVIS_WRAPPER_STEMS:
        marker = f'"{stem}"'
        require(
            failures,
            marker in navis_body and widget_mozbuild.count(marker) == 1,
            f"Navis Android wrapper graph lacks the owned {stem} peer",
        )

    for stem in (
        "AndroidAccessibilityBridge",
        "AndroidWebExecutorBridge",
        "GeckoVRManager",
    ):
        marker = f'"{stem}"'
        require(
            failures,
            widget_mozbuild.count(marker) == 1 and marker not in legacy_body,
            f"engine primitive wrapper {stem} is not shared without a lifecycle owner",
        )


def verify_product_source_boundary(
    root: Path, failures: list[str], primitive_list: str
) -> None:
    source_root = root / "platform/android/src/main"
    if not source_root.is_dir():
        return
    exact_geckoview_classes = tuple(
        entry.removesuffix(".java").replace("/", ".")
        for entry in (
            line.strip()
            for line in primitive_list.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        if entry.startswith("org/mozilla/geckoview/")
        and not any(token in entry for token in "*?[")
    )
    wildcard = re.compile(r"\bimport\s+org\.mozilla\.geckoview\.\*")
    imported_type = re.compile(
        r"(?m)^\s*import\s+(?:static\s+)?"
        r"(?P<name>org\.mozilla\.geckoview\.[A-Za-z_$][\w.$]*)"
    )
    owner = re.compile(
        r"\borg\.mozilla\.geckoview\."
        r"(?P<owner>" + "|".join(FORBIDDEN_PRODUCT_OWNER_TYPES) + r")\b"
    )
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.suffix not in {".java", ".kt"}:
            continue
        try:
            code = without_comments(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as error:
            failures.append(f"cannot inspect Android product source {path}: {error}")
            continue
        relative = path.relative_to(root)
        require(
            failures,
            wildcard.search(code) is None,
            f"Android product source imports the GeckoView package wholesale: {relative}",
        )
        owners = sorted({match.group("owner") for match in owner.finditer(code)})
        if owners:
            failures.append(
                "Android product source retains excluded GeckoView owner types "
                f"{', '.join(owners)}: {relative}"
            )
        unresolved = sorted(
            {
                imported
                for imported in (
                    match.group("name") for match in imported_type.finditer(code)
                )
                if not any(
                    imported == allowed or imported.startswith(allowed + ".")
                    for allowed in exact_geckoview_classes
                )
            }
        )
        if unresolved:
            failures.append(
                "Android product source imports types outside the reviewed runtime "
                f"closure {', '.join(unresolved)}: {relative}"
            )


def verify_accessibility_primitive(
    root: Path, failures: list[str], accessibility_source: str
) -> None:
    code = without_comments(accessibility_source)
    require(
        failures,
        re.search(r"\bclass\s+AndroidAccessibilityBridge\b", code) is not None,
        "Android accessibility primitive class is missing",
    )
    require(
        failures,
        re.search(
            r"\bpublic\s+(?:abstract\s+|final\s+)?class\s+"
            r"AndroidAccessibilityBridge\b",
            code,
        )
        is None,
        "AndroidAccessibilityBridge is exposed as a public product API",
    )
    for owner in ("GeckoSession", "PanZoomController", "SessionTextInput"):
        require(
            failures,
            re.search(rf"\b{owner}\b", code) is None,
            f"AndroidAccessibilityBridge retains legacy owner type {owner}",
        )

    owner_roots = (
        root / "platform/android/src/main",
        root
        / "runtime/gecko/mobile/android/geckoview/src/main/java/org/mozilla/gecko/navis",
        root / "runtime/gecko/mobile/shared/chrome/navis",
    )
    owner_files: list[Path] = []
    for owner_root in owner_roots:
        if owner_root.is_dir():
            owner_files.extend(
                path
                for path in owner_root.rglob("*")
                if path.is_file()
                and path.suffix in {".cpp", ".h", ".java", ".js", ".kt", ".mjs"}
            )
    native_owner = root / "runtime/gecko/widget/android/NavisAndroidSupport.cpp"
    if native_owner.is_file():
        owner_files.append(native_owner)
    for path in owner_files:
        try:
            owner_source = without_comments(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as error:
            failures.append(f"cannot inspect direct Android owner {path}: {error}")
            continue
        require(
            failures,
            re.search(r"\bAndroidAccessibilityBridge\b", owner_source) is None,
            "render-only direct owner prematurely attaches Android accessibility: "
            f"{path.relative_to(root)}",
        )


def verify_web_executor_primitive(
    failures: list[str], bridge_source: str, native_source: str
) -> None:
    code = without_comments(bridge_source)
    require(
        failures,
        re.search(r"\bclass\s+AndroidWebExecutorBridge\b", code) is not None,
        "Android web executor primitive class is missing",
    )
    require(
        failures,
        re.search(
            r"\bpublic\s+(?:abstract\s+|final\s+)?class\s+"
            r"AndroidWebExecutorBridge\b",
            code,
        )
        is None,
        "AndroidWebExecutorBridge is exposed as a public product API",
    )
    require(
        failures,
        re.search(r"\bGeckoRuntime\b", code) is None,
        "AndroidWebExecutorBridge retains the legacy GeckoRuntime owner",
    )
    require(
        failures,
        "java::GeckoWebExecutor" not in without_comments(native_source),
        "WebExecutorSupport still requires the excluded GeckoWebExecutor wrapper",
    )


def verify_generated_graph(
    failures: list[str], gradle_configure: str, android_mozbuild: str
) -> None:
    require_markers(
        failures,
        "gecko/mobile/android/gradle.configure",
        gradle_configure,
        (
            "GRADLE_ANDROID_NAVIS_RUNTIME_VARIANT_NAME",
            '@depends(android_subproject)',
            'if subproject == "navis":',
            '"navis-runtime-android:generateSDKBindings"',
            '"navis-runtime-android:generateJNIWrappersForGenerated{navis.variant.name}"',
            '"navis:assemble{navis.variant.name}"',
        ),
    )
    require_markers(
        failures,
        "gecko/mobile/android/moz.build",
        android_mozbuild,
        (
            '"navis-runtime-android" if CONFIG["MOZ_NAVIS_ANDROID_RUNTIME"] else "geckoview"',
            '"%s/src/main/AndroidManifest_overlay.xml" % android_runtime_project',
            "GeckoChildProcessServices.java",
            "XPCOMError.java",
            "CrashReport.java",
        ),
    )
    require(
        failures,
        re.search(
            r"@depends\(gradle_android_build_config, android_subproject\)\s+"
            r"def gradle_android_archive_geckoview_tasks\(build_config, subproject\):"
            r'.*?if subproject == "navis":\s+return \[\s+'
            r'"navis:assemble\{navis\.variant\.name\}"\.format\('
            r"\s+navis=build_config\.navis_runtime\s+\),\s+\]",
            gradle_configure,
            re.DOTALL,
        )
        is not None,
        "Android archive tier does not assemble the owned Navis application",
    )


def verify_stage(
    failures: list[str],
    installer: str,
    package_manifest: str,
    mach_tasks: str,
    package_wrapper: str,
    package_verifier: str,
) -> None:
    require(
        failures,
        re.search(
            r"ifdef MOZ_NAVIS_ANDROID_RUNTIME\s+"
            r"DEFINES \+= -DMOZ_NAVIS_ANDROID_RUNTIME=1\s+"
            r"MOZ_PKG_DIR = navis-android\s+"
            r"else\s+MOZ_PKG_DIR = geckoview\s+endif",
            installer,
        )
        is not None,
        "Android installer does not give Navis an owned stage directory",
    )
    require(
        failures,
        "DEFINES += -DMOZ_NAVIS_ANDROID_RUNTIME=1" in installer,
        "Android installer does not expose the direct-runtime package condition",
    )
    require(
        failures,
        "#ifdef MOZ_NAVIS_ANDROID_RUNTIME\n"
        "@BINPATH@/chrome/navis-android@JAREXT@\n"
        "@BINPATH@/chrome/navis-android.manifest\n#else\n"
        "@BINPATH@/components/GeckoView.manifest\n#endif"
        in package_manifest,
        "direct Android package does not replace the GeckoView component manifest with its owned chrome host",
    )
    require(
        failures,
        "@BINPATH@/@PREF_DIR@/@ANDROID_CPU_ARCH@/geckoview-prefs.js\n"
        "#ifdef MOZ_NAVIS_ANDROID_RUNTIME\n"
        "@BINPATH@/@PREF_DIR@/@ANDROID_CPU_ARCH@/navis-prefs.js\n"
        "#endif\n#else"
        in package_manifest,
        "direct Android package does not include its immutable Navis defaults",
    )
    require_markers(
        failures,
        "MachTasksPlugin.kt",
        mach_tasks,
        (
            'substs["MOZ_NAVIS_ANDROID_RUNTIME"].isTruthy()',
            '"navis-android"',
            'outputs.dir("${topobjdir}/dist/${stageProject}/assets")',
            'outputs.dir("${topobjdir}/dist/${stageProject}/lib")',
        ),
    )
    require(
        failures,
        '--native-dir "$candidate_objdir/dist/navis-android/lib"' in package_wrapper,
        "Android candidate verifier does not inspect the owned native stage",
    )
    require(
        failures,
        "verify-android-runtime-build-graph.py" in package_wrapper,
        "Android candidate preflight does not enforce the runtime build graph",
    )
    require(
        failures,
        "dist/geckoview" not in package_wrapper,
        "Android candidate wrapper still consumes the GeckoView stage",
    )
    require_markers(
        failures,
        "scripts/verify-android-package.py",
        package_verifier,
        (
            "FORBIDDEN_GECKOVIEW_LIFECYCLE_CLASSES",
            "FORBIDDEN_GECKOVIEW_OWNER_TYPE_REFERENCES",
            '"Lorg/mozilla/geckoview/SessionAccessibility;"',
            "def class_descriptors(self) -> set[bytes]:",
            "inspect_geckoview_lifecycle_classes(",
            "inspect_geckoview_owner_type_references(",
            '"geckoview_lifecycle_classes": geckoview_lifecycle_classes',
            '"geckoview_owner_type_references": geckoview_owner_type_references',
        ),
    )


def verify_manifest(root: Path, failures: list[str]) -> None:
    path = root / "runtime/android/src/main/AndroidManifest.xml"
    try:
        manifest = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        failures.append(f"cannot parse runtime manifest: {error}")
        return
    android = "{http://schemas.android.com/apk/res/android}"
    permissions = {
        node.get(android + "name") for node in manifest.findall("uses-permission")
    }
    for permission in (
        "android.permission.INTERNET",
        "android.permission.ACCESS_NETWORK_STATE",
        "android.permission.WAKE_LOCK",
    ):
        require(
            failures,
            permission in permissions,
            f"runtime manifest lacks {permission}",
        )
    require(
        failures,
        any(
            node.get(android + "glEsVersion") == "0x00020000"
            and node.get(android + "required") == "true"
            for node in manifest.findall("uses-feature")
        ),
        "runtime manifest lacks the GLES 2 requirement",
    )


def verify(root: Path) -> list[str]:
    failures: list[str] = []
    ledger = read(root, "navis/config/gecko-semantic-ports.json", failures)
    patch = read(root, WORKSPACE_PORT_PATH, failures)
    settings = read(root, "runtime/gecko/settings.gradle", failures)
    app_build = read(root, "platform/android/build.gradle", failures)
    runtime_build = read(root, "runtime/android/build.gradle", failures)
    primitive_list = read(root, PRIMITIVE_LIST_PATH, failures)
    accessibility_source = read(root, ACCESSIBILITY_PRIMITIVE_PATH, failures)
    web_executor_source = read(root, WEB_EXECUTOR_PRIMITIVE_PATH, failures)
    web_executor_native = read(root, WEB_EXECUTOR_NATIVE_PATH, failures)
    proguard = read(root, "runtime/android/proguard-rules.pro", failures)
    gradle_configure = read(
        root, "runtime/gecko/mobile/android/gradle.configure", failures
    )
    android_mozbuild = read(root, "runtime/gecko/mobile/android/moz.build", failures)
    widget_mozbuild = read(root, "runtime/gecko/widget/android/moz.build", failures)
    installer = read(
        root, "runtime/gecko/mobile/android/installer/Makefile.in", failures
    )
    package_manifest = read(
        root,
        "runtime/gecko/mobile/android/installer/package-manifest.in",
        failures,
    )
    mach_tasks = read(
        root,
        "runtime/gecko/mobile/android/gradle/plugins/conventions/src/main/java/"
        "org/mozilla/conventions/MachTasksPlugin.kt",
        failures,
    )
    package_wrapper = read(
        root, "navis/scripts/package-android-candidate.sh", failures
    )
    package_verifier = read(root, "navis/scripts/verify-android-package.py", failures)

    verify_port(root, failures, ledger, patch)
    verify_settings(failures, settings)
    verify_modules(failures, app_build, runtime_build, primitive_list, proguard)
    verify_accessibility_primitive(root, failures, accessibility_source)
    verify_web_executor_primitive(
        failures, web_executor_source, web_executor_native
    )
    verify_gecko_primitive_dependencies(root, failures)
    verify_wrapper_graph(failures, widget_mozbuild)
    verify_product_source_boundary(root, failures, primitive_list)
    verify_generated_graph(failures, gradle_configure, android_mozbuild)
    verify_stage(
        failures,
        installer,
        package_manifest,
        mach_tasks,
        package_wrapper,
        package_verifier,
    )
    verify_manifest(root, failures)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    failures = verify(args.root.resolve())
    if failures:
        print("Navis Android runtime build graph verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Navis Android runtime build graph verified.")
    print("- :navis consumes only the owned :navis-runtime-android engine module")
    print("- Navis configuration excludes the :geckoview project and AAR")
    print("- Runtime Java inputs are a reviewed primitive allowlist")
    print("- JNI generation, native staging and candidate inspection use Navis-owned paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
