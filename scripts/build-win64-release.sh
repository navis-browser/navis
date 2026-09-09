#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0


set -euo pipefail

navis_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$navis_dir/.." && pwd)"
release_mozconfig="$workspace_dir/runtime/mozconfig.win64.release"
release_objdir="$workspace_dir/runtime/gecko/obj-navis-win64-release"

if (( EUID == 0 )); then
  printf 'Navis Release builds must run as an unprivileged build user.\n' >&2
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
build_id="${NAVIS_BUILD_ID:-}"
if [[ ! "$build_id" =~ ^[0-9]{14}$ ]]; then
  printf 'NAVIS_BUILD_ID must explicitly name the shared 14-digit candidate ID.\n' >&2
  exit 1
fi
export MOZ_BUILD_DATE="$build_id"
python3 "$navis_dir/scripts/verify-source-freeze.py" \
  --manifest "$source_freeze"

python3 "$navis_dir/scripts/verify-release-profiles.py"
python3 "$navis_dir/scripts/verify-product-identity.py"

wine_binary="${WINE:-$(command -v wine || true)}"
if [[ -z "$wine_binary" && -x /usr/lib/wine/wine64 ]]; then
  wine_binary=/usr/lib/wine/wine64
fi
if [[ -z "$wine_binary" || ! -x "$wine_binary" ]]; then
  printf 'Wine is required before the guarded Win64 configure phase.\n' >&2
  exit 1
fi
export WINE="$wine_binary"
export WINEDEBUG="${WINEDEBUG:--all}"
export WINEPREFIX="${WINEPREFIX:-$HOME/.mozbuild/navis-system-wine}"

python3 "$navis_dir/scripts/verify-incremental-objdir.py" \
  --platform win64 --phase identity
NAVIS_MOZCONFIG="$release_mozconfig" \
  "$navis_dir/scripts/mach.sh" configure
python3 "$navis_dir/scripts/verify-incremental-objdir.py" \
  --platform win64 --phase configured
python3 "$navis_dir/scripts/verify-source-freeze.py" \
  --manifest "$source_freeze"

NAVIS_MOZCONFIG="$release_mozconfig" \
NAVIS_WIN64_OBJDIR="$release_objdir" \
NAVIS_BUILD_ID="$build_id" \
  "$navis_dir/scripts/build-win64.sh"

python3 "$navis_dir/scripts/verify-source-freeze.py" \
  --manifest "$source_freeze"
