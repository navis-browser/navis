#!/usr/bin/env bash

set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gecko_dir="${NAVIS_GECKO_DIR:-$workspace_dir/gecko}"
objdir="${NAVIS_RUNTIME_OBJDIR:-$gecko_dir/obj-navis-runtime}"
mozconfig="${NAVIS_MOZCONFIG:-$workspace_dir/mozconfig.runtime}"
output_dir="${NAVIS_ARTIFACT_DIR:-$workspace_dir/artifacts}"
capability_profile="${NAVIS_CAPABILITY_PROFILE:-desktop-embedder-core}"
artifact_variant="${NAVIS_ARTIFACT_VARIANT:-}"
build_id="${NAVIS_BUILD_ID:-$(date -u +%Y%m%d%H%M%S)}"
export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.94.1}"

if [[ "$gecko_dir" != /* ]]; then
  gecko_dir="$workspace_dir/$gecko_dir"
fi
if [[ "$objdir" != /* ]]; then
  objdir="$workspace_dir/$objdir"
fi
if [[ ! -d "$gecko_dir" ]]; then
  printf 'Navis Gecko source tree does not exist: %s\n' "$gecko_dir" >&2
  exit 1
fi
gecko_dir="$(cd "$gecko_dir" && pwd)"

if [[ ! -f "$objdir/config.status" ]]; then
  printf 'No configured Navis runtime object directory found: %s\n' \
    "$objdir" >&2
  exit 1
fi
objdir="$(cd "$objdir" && pwd)"
case "$objdir" in
  "$gecko_dir"/obj-*) ;;
  *)
    printf 'Refusing to refresh an object directory outside %s: %s\n' \
      "$gecko_dir" "$objdir" >&2
    exit 1
    ;;
esac

if [[ -n "$artifact_variant" && ! "$artifact_variant" =~ ^[A-Za-z0-9._-]+$ ]]; then
  printf 'NAVIS_ARTIFACT_VARIANT contains unsafe characters: %s\n' \
    "$artifact_variant" >&2
  exit 1
fi
if [[ ! "$build_id" =~ ^[0-9]{14}$ ]]; then
  printf 'NAVIS_BUILD_ID must contain 14 digits: %s\n' "$build_id" >&2
  exit 1
fi

# One immutable product identity must cover the build, staged runtime and
# archive name. Without this pin, separate `mach build` and `mach package`
# invocations can generate different timestamps while reusing the already
# linked application binary.
export MOZ_BUILD_DATE="$build_id"

python3 "$workspace_dir/scripts/verify-core-abi.py"
python3 "$workspace_dir/scripts/verify-deferred-web-apis.py"
python3 "$workspace_dir/scripts/verify-remote-settings-policy.py"
python3 "$workspace_dir/scripts/verify-clean-links-policy.py"
python3 "$workspace_dir/scripts/verify-builtin-extensions.py"
python3 "$workspace_dir/scripts/verify-webdriver-boundary.py"
python3 "$workspace_dir/scripts/verify-native-services-boundary.py"
python3 "$workspace_dir/scripts/verify-spellcheck.py"
python3 "$workspace_dir/scripts/verify-webauthn.py"

# Gecko's incremental install manifests do not always remove chrome entries
# after a feature changes from enabled to excluded. Recreate only the derived
# distribution staging, then let the existing object files repopulate it. This
# prevents a valid reduced graph from being repackaged with stale UI or modules.
dist_dir="$objdir/dist"
rm -rf -- "${dist_dir:?}/bin" "${dist_dir:?}/navis"
NAVIS_MOZCONFIG="$mozconfig" "$workspace_dir/scripts/mach.sh" build
NAVIS_MOZCONFIG="$mozconfig" "$workspace_dir/scripts/mach.sh" package

runtime_dir="$objdir/dist/navis"
python3 "$workspace_dir/scripts/verify-builtin-extensions.py" \
  --package-root "$runtime_dir"
python3 "$workspace_dir/scripts/verify-webdriver-boundary.py" \
  --runtime "$runtime_dir"
python3 "$workspace_dir/scripts/verify-native-services-boundary.py" \
  --runtime "$runtime_dir"
python3 "$workspace_dir/scripts/audit-runtime-source-domains.py" \
  --gecko "$gecko_dir" --objdir "$objdir"
python3 "$workspace_dir/scripts/verify-runtime-capabilities.py" \
  --objdir "$objdir" --runtime "$runtime_dir" --scope all \
  --profile "$capability_profile"
"$workspace_dir/scripts/verify-runtime-package.sh" "$runtime_dir"

package_name_file="$objdir/dist/package_name.txt"
if [[ ! -f "$package_name_file" ]]; then
  printf 'Package name was not generated in %s.\n' "$objdir/dist" >&2
  exit 1
fi

package_name="$(<"$package_name_file")"
if [[ -z "$package_name" || "$package_name" != "${package_name##*/}" ]]; then
  printf 'Invalid generated package name: %s\n' "$package_name" >&2
  exit 1
fi

source_archive="$objdir/dist/$package_name"
if [[ ! -f "$source_archive" ]]; then
  printf 'Generated package does not exist: %s\n' "$source_archive" >&2
  exit 1
fi

version="$(awk -F= '$1 == "Version" { print $2; exit }' \
  "$runtime_dir/application.ini")"
packaged_build_id="$(awk -F= '$1 == "BuildID" { print $2; exit }' \
  "$runtime_dir/application.ini")"
if [[ -z "$version" || ! "$packaged_build_id" =~ ^[0-9]{14}$ ]]; then
  printf 'Invalid Version or BuildID in packaged application.ini.\n' >&2
  exit 1
fi
if [[ "$packaged_build_id" != "$build_id" ]]; then
  printf 'application.ini BuildID mismatch: expected %s, found %s\n' \
    "$build_id" "$packaged_build_id" >&2
  exit 1
fi

platform_archive="${package_name#navis-"${version}".en-US.}"
if [[ "$platform_archive" == "$package_name" ]]; then
  printf 'Unexpected generated package name: %s\n' "$package_name" >&2
  exit 1
fi

variant_suffix=""
if [[ -n "$artifact_variant" ]]; then
  variant_suffix="-$artifact_variant"
fi
archive_name="navis-${version}-${platform_archive%.tar.xz}${variant_suffix}-${build_id}.tar.xz"
archive_path="$output_dir/$archive_name"
if [[ -e "$archive_path" ]]; then
  printf 'Refusing to overwrite existing artifact: %s\n' "$archive_path" >&2
  exit 1
fi

mkdir -p "$output_dir"
cp --reflink=auto -- "$source_archive" "$archive_path"
sha256sum "$archive_path"
printf 'Portable Linux artifact: %s\n' "$archive_path"
