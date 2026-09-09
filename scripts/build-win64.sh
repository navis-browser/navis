#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0

set -euo pipefail

navis_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$navis_dir/.." && pwd)"
runtime_dir="$workspace_dir/runtime"
objdir="${NAVIS_WIN64_OBJDIR:-$runtime_dir/gecko/obj-navis-win64}"
build_id="${NAVIS_BUILD_ID:-$(date -u +%Y%m%d%H%M%S)}"
export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.94.1}"

python3 "$navis_dir/../runtime/scripts/verify-core-abi.py"
python3 "$navis_dir/scripts/verify-deferred-web-apis.py"
python3 "$navis_dir/scripts/verify-remote-settings-policy.py"
python3 "$navis_dir/scripts/verify-clean-links-policy.py"
python3 "$navis_dir/scripts/verify-spellcheck.py"
python3 "$navis_dir/scripts/verify-webauthn.py"

if [[ "$objdir" != /* ]]; then
  objdir="$runtime_dir/gecko/$objdir"
fi
if [[ -d "$objdir" ]]; then
  objdir="$(cd "$objdir" && pwd -P)"
fi

if [[ ! "$build_id" =~ ^[0-9]{14}$ ]]; then
  printf 'NAVIS_BUILD_ID must contain 14 digits: %s\n' "$build_id" >&2
  exit 1
fi

wine_binary="${WINE:-$(command -v wine || true)}"
if [[ -z "$wine_binary" || ! -x "$wine_binary" ]]; then
  printf 'Wine is required for the Windows cross-build configure probes.\n' >&2
  exit 1
fi

dist_bin="$objdir/dist/bin"
if [[ -e "$dist_bin" || -L "$dist_bin" ]]; then
  if [[ ! -f "$objdir/config.status" || "$objdir" == "/" ]]; then
    printf 'Refusing to clean an invalid Win64 object directory: %s\n' \
      "$objdir" >&2
    exit 1
  fi
  printf 'Refreshing generated Win64 runtime install root: %s\n' "$dist_bin"
  rm -r -- "$dist_bin"
fi

msitools_bin="${NAVIS_MSITOOLS_BIN:-$HOME/.local/navis-toolchain/msitools/usr/bin}"
if [[ -d "$msitools_bin" ]]; then
  export PATH="$msitools_bin:$PATH"
fi

export MOZ_BUILD_DATE="$build_id"
export NAVIS_MOZCONFIG="${NAVIS_MOZCONFIG:-$runtime_dir/mozconfig.win64}"
export WINE="$wine_binary"
export WINEDEBUG="${WINEDEBUG:--all}"
export WINEPREFIX="${WINEPREFIX:-$HOME/.mozbuild/navis-system-wine}"

printf 'Building Navis Win64 with BuildID %s...\n' "$build_id"
# application.ini/buildid.h and toolkit/library/buildid.cpp have separate
# generator stamps.  Refresh both before one incremental native build so the
# PE mozbuildid section cannot retain a prior candidate identity.
generated_build_id_stamps=(
  "$objdir/.deps/buildid.h.stub"
  "$objdir/toolkit/library/.deps/buildid.cpp.stub"
)
for stamp in "${generated_build_id_stamps[@]}"; do
  if [[ "$stamp" != "$objdir"/* || -L "$stamp" ]]; then
    printf 'Unsafe generated BuildID stamp path: %s\n' "$stamp" >&2
    exit 1
  fi
done
rm -f -- "${generated_build_id_stamps[@]}"
"$navis_dir/scripts/mach.sh" build buildid.h
generated_build_id_header="$objdir/buildid.h"
expected_build_id_header="#define MOZ_BUILDID $build_id"
if [[ ! -f "$generated_build_id_header" || \
  "$(tr -d '\r\n' < "$generated_build_id_header")" != \
  "$expected_build_id_header" ]]; then
  printf 'Generated Win64 BuildID header does not match %s.\n' "$build_id" >&2
  exit 1
fi
"$navis_dir/scripts/mach.sh" build

dist_dir="$objdir/dist/bin"
application_build_id="$(awk -F= '$1 == "BuildID" { print $2; exit }' \
  "$dist_dir/application.ini")"
application_version="$(awk -F= '$1 == "Version" { print $2; exit }' \
  "$dist_dir/application.ini")"
source_version="$(tr -d '\r\n' < \
  "$workspace_dir/platform/gecko-chrome/config/version.txt")"
if [[ "$application_build_id" != "$build_id" ]]; then
  printf 'application.ini BuildID mismatch: expected %s, found %s\n' \
    "$build_id" "$application_build_id" >&2
  exit 1
fi
if [[ "$application_version" != "$source_version" ]]; then
  printf 'application.ini Version mismatch: expected %s, found %s\n' \
    "$source_version" "$application_version" >&2
  exit 1
fi

unexpected_launcher="$(find "$dist_dir" -type f \
  \( -iname 'firefox.exe' -o -iname 'desktop-launcher.exe' \) \
  -print -quit)"
if [[ -n "$unexpected_launcher" ]]; then
  printf 'Unexpected Firefox Desktop Launcher artifact: %s\n' \
    "$unexpected_launcher" >&2
  exit 1
fi

printf 'Navis Win64 build is ready: %s\n' "$dist_dir/navis.exe"
