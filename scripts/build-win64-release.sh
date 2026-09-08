#!/usr/bin/env bash

set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_mozconfig="$workspace_dir/../runtime/mozconfig.win64.release"
release_objdir="$workspace_dir/../runtime/gecko/obj-navis-win64-release"

if (( EUID == 0 )); then
  printf 'Navis Release builds must run as an unprivileged build user.\n' >&2
  exit 1
fi

source_freeze="${NAVIS_SOURCE_FREEZE:-}"
if [[ -z "$source_freeze" ]]; then
  printf 'NAVIS_SOURCE_FREEZE must name the reviewed project-source manifest.\n' >&2
  exit 1
fi
python3 "$workspace_dir/scripts/verify-source-freeze.py" \
  --manifest "$source_freeze"

python3 "$workspace_dir/scripts/verify-release-profiles.py"

NAVIS_MOZCONFIG="$release_mozconfig" \
NAVIS_WIN64_OBJDIR="$release_objdir" \
  "$workspace_dir/scripts/build-win64.sh"
