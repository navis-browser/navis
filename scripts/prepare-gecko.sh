#!/usr/bin/env bash

set -euo pipefail

navis_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$navis_dir/.." && pwd)"
runtime_dir="$workspace_dir/runtime"
gecko_dir="$runtime_dir/gecko"
android_runtime_dir="$runtime_dir/android"
product_dir="$workspace_dir/platform/gecko-chrome"
android_dir="$workspace_dir/platform/android"

python3 "$runtime_dir/scripts/prepare-desktop-embedder.py" \
  --source-root "$navis_dir" --runtime-root "$runtime_dir" --gecko "$gecko_dir"

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
mount_product_overlay \
  "$android_runtime_dir" "$gecko_dir/navis-runtime-android" "Navis Android Runtime"
actual_commit="$(git -C "$gecko_dir" rev-parse HEAD)"
printf 'Gecko %s is ready with the embedder and Navis products mounted.\n' \
  "$actual_commit"
