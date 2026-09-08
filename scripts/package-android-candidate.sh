#!/usr/bin/env bash

set -euo pipefail

navis_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$navis_dir/.." && pwd)"
candidate_mozconfig="$workspace_dir/runtime/mozconfig.android-aarch64.sccache"
candidate_objdir="$workspace_dir/runtime/gecko/obj-navis-android-aarch64-sccache"
output_dir="${NAVIS_ARTIFACT_DIR:-$workspace_dir/work/artifacts}"
build_id="${NAVIS_BUILD_ID:-}"

if (( EUID == 0 )); then
  printf 'Navis Android candidate builds must run as an unprivileged build user.\n' >&2
  exit 1
fi
if [[ -n "${AUTOCLOBBER:-}" ]]; then
  printf 'AUTOCLOBBER must be unset for an incremental candidate build.\n' >&2
  exit 1
fi
unset AUTOCLOBBER MOZCONFIG MOZ_OBJDIR NAVIS_GECKO_DIR
if [[ -n "${RUSTUP_TOOLCHAIN:-}" && "$RUSTUP_TOOLCHAIN" != "1.94.1" ]]; then
  printf 'RUSTUP_TOOLCHAIN must be unset or exactly 1.94.1 for candidates.\n' >&2
  exit 1
fi
export RUSTUP_TOOLCHAIN=1.94.1
rustup_bin="${NAVIS_RUSTUP:-$HOME/.cargo/bin/rustup}"
if [[ ! -x "$rustup_bin" ]]; then
  printf 'Pinned Rust toolchain manager is not executable: %s\n' "$rustup_bin" >&2
  exit 1
fi
export RUSTC="$("$rustup_bin" which --toolchain "$RUSTUP_TOOLCHAIN" rustc)"
export CARGO="$("$rustup_bin" which --toolchain "$RUSTUP_TOOLCHAIN" cargo)"
if [[ ! -x "$RUSTC" || ! -x "$CARGO" ]]; then
  printf 'Pinned Rust 1.94.1 compiler tools are not executable.\n' >&2
  exit 1
fi

source_freeze="${NAVIS_SOURCE_FREEZE:-}"
if [[ -z "$source_freeze" ]]; then
  printf 'NAVIS_SOURCE_FREEZE must name the reviewed project-source manifest.\n' >&2
  exit 1
fi
if [[ ! "$build_id" =~ ^[0-9]{14}$ ]]; then
  printf 'NAVIS_BUILD_ID must explicitly name the shared 14-digit candidate ID.\n' >&2
  exit 1
fi
for name in ANDROID_SDK_ROOT ANDROID_NDK_ROOT JAVA_HOME; do
  value="${!name:-}"
  if [[ "$value" != /* || ! -d "$value" ]]; then
    printf '%s must name an absolute existing directory.\n' "$name" >&2
    exit 1
  fi
done
java_bin="$JAVA_HOME/bin/java"
if [[ ! -x "$java_bin" ]]; then
  printf 'Android JDK java launcher is not executable: %s\n' "$java_bin" >&2
  exit 1
fi
# Android's apksigner wrapper invokes `java` by name and does not consult
# JAVA_HOME. Pin the selected JDK at the front of PATH for both Gradle and the
# final package verifier instead of relying on the caller's login-shell PATH.
export PATH="$JAVA_HOME/bin${PATH:+:$PATH}"

source_date_epoch="$(date -u -d \
  "${build_id:0:8} ${build_id:8:2}:${build_id:10:2}:${build_id:12:2}" +%s)"
if [[ "$(date -u -d "@$source_date_epoch" +%Y%m%d%H%M%S)" != "$build_id" ]]; then
  printf 'NAVIS_BUILD_ID is not a valid UTC timestamp: %s\n' "$build_id" >&2
  exit 1
fi
version_code=$((100000000 + source_date_epoch - 1577836800))
if (( version_code < 1 || version_code > 2100000000 )); then
  printf 'Derived Android versionCode is outside the supported range: %s\n' \
    "$version_code" >&2
  exit 1
fi

python3 "$navis_dir/scripts/verify-source-freeze.py" \
  --manifest "$source_freeze"
python3 "$navis_dir/scripts/verify-release-profiles.py"
python3 "$navis_dir/scripts/verify-product-identity.py"
python3 "$navis_dir/scripts/verify-android-runtime-build-graph.py"

export MOZ_BUILD_DATE="$build_id"
export SOURCE_DATE_EPOCH="$source_date_epoch"
export NAVIS_ANDROID_VERSION_CODE="$version_code"
export NAVIS_MOZCONFIG="$candidate_mozconfig"

python3 "$navis_dir/scripts/verify-incremental-objdir.py" \
  --platform android --phase identity
"$navis_dir/scripts/mach.sh" configure
python3 "$navis_dir/scripts/verify-incremental-objdir.py" \
  --platform android --phase configured

# Gradle's machBuildFaster/machStagePackage tasks refresh packaged resources,
# but they do not compile or relink Gecko's native libraries.  Close the
# exact-source graph explicitly under the candidate BuildID before Gradle can
# stage libxul and libmozglue from the object directory.
python3 "$navis_dir/scripts/verify-source-freeze.py" \
  --manifest "$source_freeze"
# Android deliberately does not refresh buildid.h on every incremental build,
# and buildid.cpp does not directly depend on that header.  Their actual
# generator recipes are attached to the .stub stamps; the output targets have
# empty recipes.  Invalidate only those stamps so the selected MOZ_BUILD_DATE
# reaches AppConstants and libxul without deleting outputs or clobbering the
# sccache-backed object directory.
generated_build_id_stamps=(
  "$candidate_objdir/.deps/buildid.h.stub"
  "$candidate_objdir/toolkit/library/.deps/buildid.cpp.stub"
)
for stamp in "${generated_build_id_stamps[@]}"; do
  if [[ "$stamp" != "$candidate_objdir"/* || -L "$stamp" ]]; then
    printf 'Unsafe generated BuildID stamp path: %s\n' "$stamp" >&2
    exit 1
  fi
done
rm -f -- "${generated_build_id_stamps[@]}"
"$navis_dir/scripts/mach.sh" build buildid.h
generated_build_id_header="$candidate_objdir/buildid.h"
expected_build_id_header="#define MOZ_BUILDID $build_id"
if [[ ! -f "$generated_build_id_header" || \
  "$(tr -d '\r\n' < "$generated_build_id_header")" != \
  "$expected_build_id_header" ]]; then
  printf 'Generated Android BuildID header does not match %s.\n' \
    "$build_id" >&2
  exit 1
fi
"$navis_dir/scripts/mach.sh" build
python3 "$navis_dir/scripts/verify-source-freeze.py" \
  --manifest "$source_freeze"

"$navis_dir/scripts/mach.sh" gradle :navis:assembleDebug

source_apk="$candidate_objdir/gradle/build/navis-android/outputs/apk/debug/navis-debug.apk"
if [[ ! -s "$source_apk" ]]; then
  printf 'Android candidate APK was not generated: %s\n' "$source_apk" >&2
  exit 1
fi

python3 "$navis_dir/scripts/verify-source-freeze.py" \
  --manifest "$source_freeze"

build_tools_dir="$(find "$ANDROID_SDK_ROOT/build-tools" -mindepth 2 -maxdepth 2 \
  -type f -name aapt2 -printf '%h\n' | sort -V | tail -n 1)"
if [[ -z "$build_tools_dir" || ! -x "$build_tools_dir/aapt2" || \
  ! -x "$build_tools_dir/apksigner" ]]; then
  printf 'Android SDK build tools with aapt2 and apksigner were not found.\n' >&2
  exit 1
fi

base_version="$(tr -d '\r\n' < \
  "$workspace_dir/platform/gecko-chrome/config/version.txt")"
display_version="$(tr -d '\r\n' < \
  "$workspace_dir/platform/gecko-chrome/config/version_display.txt")"
if [[ "$display_version" != "$base_version" && \
  "$display_version" != "$base_version"-* ]]; then
  printf 'Invalid product version relationship: %s / %s\n' \
    "$base_version" "$display_version" >&2
  exit 1
fi
archive_name="navis-${display_version}-android-arm64-v8a-test-candidate-${build_id}.apk"
archive_path="$output_dir/$archive_name"
manifest_path="$archive_path.manifest.json"
if [[ -e "$archive_path" || -e "$manifest_path" ]]; then
  printf 'Refusing to overwrite an existing Android candidate artifact.\n' >&2
  exit 1
fi

mkdir -p "$output_dir"
staging_dir="$(mktemp -d "$output_dir/.navis-android-candidate.XXXXXX")"
cleanup() {
  if [[ -d "$staging_dir" && "$staging_dir" == \
    "$output_dir"/.navis-android-candidate.* ]]; then
    rm -r -- "$staging_dir"
  fi
}
trap cleanup EXIT

staged_apk="$staging_dir/$archive_name"
staged_manifest="$staging_dir/$archive_name.manifest.json"
cp --reflink=auto -- "$source_apk" "$staged_apk"
python3 "$navis_dir/scripts/verify-android-package.py" \
  --apk "$staged_apk" \
  --source-freeze "$source_freeze" \
  --build-id "$build_id" \
  --version-code "$version_code" \
  --aapt2 "$build_tools_dir/aapt2" \
  --apksigner "$build_tools_dir/apksigner" \
  --native-dir "$candidate_objdir/dist/navis-android/lib" \
  --artifact-name "$archive_name" \
  --json-output "$staged_manifest"

mv -- "$staged_apk" "$archive_path"
mv -- "$staged_manifest" "$manifest_path"
sha256sum "$archive_path"
printf 'Installable Android arm64 test candidate: %s\n' "$archive_path"
printf 'Android candidate manifest: %s\n' "$manifest_path"
