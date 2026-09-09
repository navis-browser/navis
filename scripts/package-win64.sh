#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0


set -euo pipefail

navis_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$navis_dir/.." && pwd)"
runtime_dir="$workspace_dir/runtime"
gecko_dir="${NAVIS_GECKO_DIR:-$runtime_dir/gecko}"
objdir="${NAVIS_WIN64_OBJDIR:-$runtime_dir/gecko/obj-navis-win64}"
output_dir="${NAVIS_ARTIFACT_DIR:-$workspace_dir/work/artifacts}"
capability_profile="${NAVIS_CAPABILITY_PROFILE:-desktop-embedder-core}"
artifact_variant="${NAVIS_ARTIFACT_VARIANT:-}"
export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.94.1}"

if [[ "$gecko_dir" != /* ]]; then
  gecko_dir="$runtime_dir/$gecko_dir"
fi
if [[ ! -d "$gecko_dir" ]]; then
  printf 'Navis Gecko source tree does not exist: %s\n' "$gecko_dir" >&2
  exit 1
fi
gecko_dir="$(cd "$gecko_dir" && pwd)"

if [[ -n "$artifact_variant" && ! "$artifact_variant" =~ ^[A-Za-z0-9._-]+$ ]]; then
  printf 'NAVIS_ARTIFACT_VARIANT contains unsafe characters: %s\n' \
    "$artifact_variant" >&2
  exit 1
fi

python3 "$navis_dir/../runtime/scripts/verify-core-abi.py"
python3 "$navis_dir/scripts/verify-deferred-web-apis.py"
python3 "$navis_dir/scripts/verify-remote-settings-policy.py"
python3 "$navis_dir/scripts/verify-clean-links-policy.py"
python3 "$navis_dir/scripts/verify-builtin-extensions.py"
python3 "$navis_dir/scripts/verify-webdriver-boundary.py"
python3 "$navis_dir/scripts/verify-native-services-boundary.py"
python3 "$navis_dir/scripts/verify-spellcheck.py"
python3 "$navis_dir/scripts/verify-webauthn.py"

if [[ ! -f "$objdir/dist/bin/application.ini" || \
  ! -f "$objdir/dist/bin/navis.exe" ]]; then
  printf 'No complete Navis Win64 build found in %s.\n' \
    "$objdir/dist/bin" >&2
  exit 1
fi

if [[ ! -f "$objdir/navis/installer/Makefile" ]]; then
  printf 'Win64 packaging metadata is stale; run build-win64.sh first.\n' >&2
  exit 1
fi

objdir="$(cd "$objdir" && pwd)"
source_dir="$objdir/dist/navis"

seven_zip="${NAVIS_7Z:-$(command -v 7z || true)}"
if [[ -z "$seven_zip" || ! -x "$seven_zip" ]]; then
  printf '7z is required. On Arch Linux, install extra/7zip.\n' >&2
  exit 1
fi

if [[ -e "$source_dir" || -L "$source_dir" ]]; then
  rm -r -- "$source_dir"
fi
# Unlike the build wrappers, stage-package invokes make directly rather than
# mach.sh. Verify the prepared Gecko tree here so an unowned tracked resource
# edit between the build and package phases cannot bypass the source freeze.
python3 "$runtime_dir/scripts/prepare-desktop-embedder.py" \
  --source-root "$navis_dir" --runtime-root "$runtime_dir" \
  --gecko "$gecko_dir" --no-clone --no-mount
make -C "$objdir" -s stage-package
if [[ -n "${NAVIS_SOURCE_FREEZE:-}" ]]; then
  python3 "$navis_dir/scripts/verify-source-freeze.py" \
    --manifest "$NAVIS_SOURCE_FREEZE"
fi
python3 "$navis_dir/scripts/verify-builtin-extensions.py" \
  --package-root "$source_dir"
python3 "$navis_dir/scripts/verify-webdriver-boundary.py" \
  --runtime "$source_dir"
python3 "$navis_dir/scripts/verify-native-services-boundary.py" \
  --runtime "$source_dir"
python3 "$navis_dir/scripts/audit-runtime-source-domains.py" \
  --gecko "$gecko_dir" --objdir "$objdir"
python3 "$navis_dir/scripts/verify-runtime-capabilities.py" \
  --objdir "$objdir" --runtime "$source_dir" --scope all \
  --profile "$capability_profile"
"$navis_dir/scripts/verify-runtime-package.sh" "$source_dir"

version="$(awk -F= '$1 == "Version" { print $2; exit }' \
  "$source_dir/application.ini")"
build_id="$(awk -F= '$1 == "BuildID" { print $2; exit }' \
  "$source_dir/application.ini")"
source_version="$(tr -d '\r\n' < \
  "$workspace_dir/platform/gecko-chrome/config/version.txt")"
if [[ -z "$version" || ! "$build_id" =~ ^[0-9]{14}$ ]]; then
  printf 'Invalid Version or BuildID in packaged application.ini.\n' >&2
  exit 1
fi
if [[ "$version" != "$source_version" ]]; then
  printf 'application.ini Version mismatch: expected %s, found %s\n' \
    "$source_version" "$version" >&2
  exit 1
fi
if [[ -n "${NAVIS_BUILD_ID:-}" && "$build_id" != "$NAVIS_BUILD_ID" ]]; then
  printf 'application.ini BuildID mismatch: expected %s, found %s\n' \
    "$NAVIS_BUILD_ID" "$build_id" >&2
  exit 1
fi
python3 "$navis_dir/scripts/verify-desktop-build-id.py" \
  --runtime "$source_dir" --platform win64 --build-id "$build_id"

variant_suffix=""
if [[ -n "$artifact_variant" ]]; then
  variant_suffix="-$artifact_variant"
fi
archive_name="navis-${version}-win64${variant_suffix}-${build_id}.zip"
archive_path="$output_dir/$archive_name"
if [[ -e "$archive_path" ]]; then
  printf 'Refusing to overwrite existing artifact: %s\n' "$archive_path" >&2
  exit 1
fi

staging_root="$(mktemp -d /tmp/navis-win64-archive.XXXXXX)"
cleanup() {
  if [[ -d "$staging_root" && "$staging_root" == \
    /tmp/navis-win64-archive.* ]]; then
    rm -r -- "$staging_root"
  fi
}
trap cleanup EXIT

mkdir -p "$output_dir"
printf 'Creating %s...\n' "$archive_name"
(
  cd "$objdir/dist"
  "$seven_zip" a -tzip -mx=7 "$staging_root/$archive_name" navis
  "$seven_zip" t "$staging_root/$archive_name"
)

mv -- "$staging_root/$archive_name" "$archive_path"
sha256sum "$archive_path"
printf 'Portable Win64 artifact: %s\n' "$archive_path"
