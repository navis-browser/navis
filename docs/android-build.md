# Android candidate builds

Prepare matching sibling sources and the pinned Gecko Android toolchain as described in the project README. Set `ANDROID_SDK_ROOT`, `ANDROID_NDK_ROOT` and `JAVA_HOME` to absolute existing toolchain directories. Candidate builds require the pinned Rust toolchain, an explicit UTC `NAVIS_BUILD_ID` and a reviewed `NAVIS_SOURCE_FREEZE`. Keep `AUTOCLOBBER` unset and preserve existing configured objects and caches.

The default Android candidate is Debug (`org.navis.browser.debug`) with optimized native Gecko. For the Release application (`org.navis.browser`), set the following inputs before configuring or building:

```sh
export NAVIS_ANDROID_VARIANT=release
export NAVIS_MOZCONFIG="$PWD/runtime/mozconfig.android-aarch64.release"
export NAVIS_BUILD_ID=YYYYMMDDhhmmss  # replace with the selected UTC build identity
export NAVIS_SOURCE_FREEZE=/absolute/path/to/reviewed-source-freeze.json
export NAVIS_ARTIFACT_DIR=/absolute/path/to/new-artifacts
export NAVIS_ANDROID_KEYSTORE=/absolute/path/to/candidate.keystore
export NAVIS_ANDROID_KEY_ALIAS=candidate
export NAVIS_ANDROID_KEYSTORE_PASSWORD_FILE=/absolute/private/path/to/store-password
export NAVIS_ANDROID_SIGNER_SHA256=lowercase_sha256_of_selected_certificate
# Set NAVIS_ANDROID_KEY_PASSWORD_FILE if the key has a different password.
```

Use private regular files for signing inputs; keep keys/passwords outside source and logs. The signing certificate fingerprint must be pinned independently. An explicit test certificate is acceptable for local performance testing; its identity does not turn a Debug compilation into Release. Official distribution signing requires its own key policy. To use existing offline dependency caches, set `GRADLE_FLAGS` to include `--offline` before configure; a missing cached dependency must fail visibly.

Create and review the complete source freeze with `python3 navis/scripts/verify-source-freeze.py --create "$NAVIS_SOURCE_FREEZE"`. For an explicitly approved first build of the empty Release graph, run:

```sh
bash navis/scripts/mach.sh configure
bash navis/scripts/mach.sh build
```

Then use the public incremental wrapper, which verifies source identity and configuration before building, signing and auditing the APK:

```sh
bash navis/scripts/package-android-candidate.sh
bash navis/scripts/mach.sh gradle :navis-runtime-android:testReleaseUnitTest
bash navis/scripts/mach.sh gradle :navis:testReleaseUnitTest
```

The wrapper rejects missing/mismatched graphs and existing output artifacts. It requires a non-debuggable manifest, the Release app ID, matching Java/native/resource BuildIDs, reviewed Release dependency notices, the exact signer and real R8 outputs under the object directory's `gradle/build/navis-android/intermediates/mapping/release/minifyReleaseWithR8/`. Preserve configuration, mapping, usage and seeds files for diagnosis. The final APK has `release-test-candidate` in its name. Debug remains separately installable with its original data.

Release enables R8 optimization, code/resource shrinking and obfuscation, with precise JNI/reflection keep rules. The native profile disables debug/developer options and enables optimization. These settings do not claim PGO or LTO. Measure startup, scrolling, tab switching, memory and representative pages on the same physical device, with comparable data and thermal state. Package/JVM audits cannot establish actual device smoothness.

Release package inspection also reads the build's generated JNI wrapper headers and requires every Navis/Gecko native class lookup, the Core bridge and the pinned handwritten WebRTC camera JNI lookup classes to remain under their original names in the final DEX. An optimized mapping alone cannot prove JNI compatibility: member keep rules must retain the owning class and the types in its descriptors. Physical Release checks include media-device enumeration before permission, camera permission/capture and stop/reopen; JVM tests do not exercise those native lookups.

The package gate checks literal module dependencies of the Android chrome host and Navis-owned shared modules against the actual APK's `omni.ja`. Shared permission-origin helpers must be packaged for Android as well as desktop. Runtime JVM tests cover dispatcher cleanup before native attachment and repeated close. On a real device, verify initialization completes and content navigation works, then close and reopen a tab and restart the application; process survival or successful installation alone does not establish host readiness.
