#!/usr/bin/env bash

set -euo pipefail

navis_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$navis_dir/.." && pwd)"
release_mozconfig="$workspace_dir/runtime/mozconfig.win64.release"
release_objdir="$workspace_dir/runtime/gecko/obj-navis-win64-release"

if (( EUID == 0 )); then
  printf 'Navis Release packaging must run as an unprivileged build user.\n' >&2
  exit 1
fi
if [[ -n "${AUTOCLOBBER:-}" ]]; then
  printf 'AUTOCLOBBER must be unset for an incremental candidate package.\n' >&2
  exit 1
fi
unset AUTOCLOBBER MOZCONFIG MOZ_OBJDIR NAVIS_GECKO_DIR
if [[ -n "${RUSTUP_TOOLCHAIN:-}" && "$RUSTUP_TOOLCHAIN" != "1.94.1" ]]; then
  printf 'RUSTUP_TOOLCHAIN must be unset or exactly 1.94.1 for candidates.\n' >&2
  exit 1
fi
export RUSTUP_TOOLCHAIN=1.94.1

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
python3 "$navis_dir/scripts/verify-incremental-objdir.py" \
  --platform win64 --phase configured

NAVIS_MOZCONFIG="$release_mozconfig" \
NAVIS_WIN64_OBJDIR="$release_objdir" \
NAVIS_BUILD_ID="$build_id" \
NAVIS_ARTIFACT_VARIANT=release \
  "$navis_dir/scripts/package-win64.sh"
