#!/usr/bin/env bash

set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
objdir="${NAVIS_WIN64_OBJDIR:-$workspace_dir/gecko/obj-navis-win64}"
build_id="${NAVIS_BUILD_ID:-$(date -u +%Y%m%d%H%M%S)}"
export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.94.1}"

python3 "$workspace_dir/scripts/verify-core-abi.py"
python3 "$workspace_dir/scripts/verify-deferred-web-apis.py"
python3 "$workspace_dir/scripts/verify-remote-settings-policy.py"
python3 "$workspace_dir/scripts/verify-clean-links-policy.py"
python3 "$workspace_dir/scripts/verify-spellcheck.py"
python3 "$workspace_dir/scripts/verify-webauthn.py"

if [[ "$objdir" != /* ]]; then
  objdir="$workspace_dir/$objdir"
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
export NAVIS_MOZCONFIG="${NAVIS_MOZCONFIG:-$workspace_dir/mozconfig.win64}"
export WINE="$wine_binary"
export WINEDEBUG="${WINEDEBUG:--all}"
export WINEPREFIX="${WINEPREFIX:-$HOME/.mozbuild/navis-system-wine}"

printf 'Building Navis Win64 with BuildID %s...\n' "$build_id"
"$workspace_dir/scripts/mach.sh" build

# application.ini and toolkit/library/buildid.cpp are generated in separate
# recursive phases. Force the latter once after buildid.h has the fixed ID,
# then let mach relink the final runtime.
make -C "$objdir/toolkit/library" -B .deps/buildid.cpp.stub
"$workspace_dir/scripts/mach.sh" build

dist_dir="$objdir/dist/bin"
application_build_id="$(awk -F= '$1 == "BuildID" { print $2; exit }' \
  "$dist_dir/application.ini")"
if [[ "$application_build_id" != "$build_id" ]]; then
  printf 'application.ini BuildID mismatch: expected %s, found %s\n' \
    "$build_id" "$application_build_id" >&2
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
