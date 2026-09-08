#!/usr/bin/env bash

set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gecko_dir="${NAVIS_GECKO_DIR:-$workspace_dir/../runtime/gecko}"
objdir="${NAVIS_WIN64_OBJDIR:-$workspace_dir/../runtime/gecko/obj-navis-win64}"
output_dir="${NAVIS_ARTIFACT_DIR:-$workspace_dir/../work/artifacts}"
capability_profile="${NAVIS_CAPABILITY_PROFILE:-desktop-embedder-core}"
artifact_variant="${NAVIS_ARTIFACT_VARIANT:-}"
export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.94.1}"

if [[ "$gecko_dir" != /* ]]; then
  gecko_dir="$workspace_dir/$gecko_dir"
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

python3 "$workspace_dir/../runtime/scripts/verify-core-abi.py"
python3 "$workspace_dir/scripts/verify-deferred-web-apis.py"
python3 "$workspace_dir/scripts/verify-remote-settings-policy.py"
python3 "$workspace_dir/scripts/verify-clean-links-policy.py"
python3 "$workspace_dir/scripts/verify-builtin-extensions.py"
python3 "$workspace_dir/scripts/verify-webdriver-boundary.py"
python3 "$workspace_dir/scripts/verify-native-services-boundary.py"
python3 "$workspace_dir/scripts/verify-spellcheck.py"
python3 "$workspace_dir/scripts/verify-webauthn.py"

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
make -C "$objdir" -s stage-package
python3 "$workspace_dir/scripts/verify-builtin-extensions.py" \
  --package-root "$source_dir"
python3 "$workspace_dir/scripts/verify-webdriver-boundary.py" \
  --runtime "$source_dir"
python3 "$workspace_dir/scripts/verify-native-services-boundary.py" \
  --runtime "$source_dir"
python3 "$workspace_dir/scripts/audit-runtime-source-domains.py" \
  --gecko "$gecko_dir" --objdir "$objdir"
python3 "$workspace_dir/scripts/verify-runtime-capabilities.py" \
  --objdir "$objdir" --runtime "$source_dir" --scope all \
  --profile "$capability_profile"
"$workspace_dir/scripts/verify-runtime-package.sh" "$source_dir"

version="$(awk -F= '$1 == "Version" { print $2; exit }' \
  "$source_dir/application.ini")"
build_id="$(awk -F= '$1 == "BuildID" { print $2; exit }' \
  "$source_dir/application.ini")"
if [[ -z "$version" || ! "$build_id" =~ ^[0-9]{14}$ ]]; then
  printf 'Invalid Version or BuildID in packaged application.ini.\n' >&2
  exit 1
fi

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
