#!/usr/bin/env bash

set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gecko_dir="${NAVIS_GECKO_DIR:-$workspace_dir/gecko}"
binary="${NAVIS_BINARY:-$gecko_dir/obj-navis-runtime/dist/bin/navis}"

if [[ ! -x "$binary" ]]; then
  printf 'Navis binary is not executable: %s\n' "$binary" >&2
  printf 'Run ./scripts/mach.sh build first or set NAVIS_GECKO_DIR/NAVIS_BINARY.\n' >&2
  exit 1
fi

if [[ -n "${NAVIS_PROFILE_DIR:-}" ]]; then
  if [[ ! -d "$NAVIS_PROFILE_DIR" ]]; then
    printf 'NAVIS_PROFILE_DIR is not a directory: %s\n' \
      "$NAVIS_PROFILE_DIR" >&2
    exit 1
  fi
  exec "$binary" -profile "$NAVIS_PROFILE_DIR" --no-remote "$@"
fi

profile_dir="$(mktemp -d -t navis-profile.XXXXXX)"
cleanup_profile() {
  case "$profile_dir" in
    */navis-profile.*) rm -rf -- "$profile_dir" ;;
  esac
}
trap cleanup_profile EXIT HUP INT TERM

"$binary" -profile "$profile_dir" --no-remote "$@"
