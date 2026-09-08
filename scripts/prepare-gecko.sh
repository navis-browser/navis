#!/usr/bin/env bash

set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gecko_dir="$workspace_dir/../runtime/gecko"
product_dir="$workspace_dir/../platform/gecko-chrome"

python3 "$workspace_dir/../runtime/scripts/prepare-desktop-embedder.py" \
  --source-root "$workspace_dir/../runtime" --gecko "$gecko_dir"

overlay_path="$gecko_dir/navis"
if [[ -e "$overlay_path" && ! -L "$overlay_path" ]]; then
  printf '%s exists and is not the Navis overlay link.\n' "$overlay_path" >&2
  exit 1
fi

ln -sfn "$product_dir" "$overlay_path"
actual_commit="$(git -C "$gecko_dir" rev-parse HEAD)"
printf 'Gecko %s is ready with the embedder and Navis product mounted.\n' \
  "$actual_commit"
