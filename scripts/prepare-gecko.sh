#!/usr/bin/env bash

set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gecko_dir="$workspace_dir/gecko"
product_dir="$workspace_dir/product"
android_dir="$workspace_dir/android"

python3 "$workspace_dir/scripts/prepare-desktop-embedder.py" \
  --source-root "$workspace_dir" --gecko "$gecko_dir"

mount_product_overlay() {
  local navis_source_path="$1"
  local navis_overlay_path="$2"
  local navis_label="$3"
  local navis_current_target=""
  local navis_overlay_parent=""
  local navis_relative_source=""

  navis_source_path="$(readlink -f -- "$navis_source_path")"
  if [[ -L "$navis_overlay_path" ]]; then
    navis_current_target="$(readlink -f -- "$navis_overlay_path" || true)"
    if [[ "$navis_current_target" == "$navis_source_path" ]]; then
      return
    fi
    unlink -- "$navis_overlay_path"
  elif [[ -e "$navis_overlay_path" ]]; then
    printf '%s exists and is not the %s overlay link.\n' \
      "$navis_overlay_path" "$navis_label" >&2
    exit 1
  fi
  navis_overlay_parent="$(readlink -f -- "$(dirname -- "$navis_overlay_path")")"
  navis_relative_source="$(realpath --relative-to="$navis_overlay_parent" -- "$navis_source_path")"
  ln -s -- "$navis_relative_source" "$navis_overlay_path"
}

mount_product_overlay "$product_dir" "$gecko_dir/navis" "Navis"
mount_product_overlay "$android_dir" "$gecko_dir/navis-android" "Navis Android"
actual_commit="$(git -C "$gecko_dir" rev-parse HEAD)"
printf 'Gecko %s is ready with the embedder and Navis products mounted.\n' \
  "$actual_commit"
