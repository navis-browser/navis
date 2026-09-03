#!/usr/bin/env bash

set -euo pipefail

workspace_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$#" -lt 2 || "$#" -gt 3 ]]; then
  printf 'Usage: %s INPUT-OMNI.JA OUTPUT-OMNI.JA [PACKAGE-NAME]\n' \
    "${0##*/}" >&2
  exit 2
fi

input_omni="$(realpath "$1")"
output_parent="$(realpath "$(dirname "$2")")"
output_omni="$output_parent/$(basename "$2")"
package_name="${3:-desktop-embedder-test-shell}"
shell_dir_name="$package_name"

if [[ ! "$package_name" =~ ^[a-z0-9-]+$ ]]; then
  printf 'Omnijar package name is invalid: %s\n' "$package_name" >&2
  exit 2
fi
if [[ ! -f "$input_omni" ]]; then
  printf 'Input omnijar is missing: %s\n' "$input_omni" >&2
  exit 1
fi
if [[ "$input_omni" == "$output_omni" ]]; then
  printf 'Input and output omnijars must be different.\n' >&2
  exit 2
fi
for tool in unzip zip; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf '%s is required to prepare a packaged shell runtime.\n' \
      "$tool" >&2
    exit 1
  fi
done

staging_dir="$(mktemp -d -t desktop-embedder-omnijar.XXXXXX)"
temporary_output="$staging_dir/output.omni.ja"
cleanup() {
  case "$staging_dir" in
    /tmp/desktop-embedder-omnijar.*) rm -rf -- "$staging_dir" ;;
  esac
}
trap cleanup EXIT HUP INT TERM

unzip -q "$input_omni" -d "$staging_dir/unpacked"
manifest="$staging_dir/unpacked/chrome/chrome.manifest"
shell_dir="$staging_dir/unpacked/chrome/$shell_dir_name"
if [[ ! -f "$manifest" ]]; then
  printf 'Input omnijar has no chrome/chrome.manifest.\n' >&2
  exit 1
fi
if [[ -e "$shell_dir" ]] || grep -Fq "$package_name" "$manifest"; then
  printf 'Input omnijar already contains package %s.\n' "$package_name" >&2
  exit 1
fi

mkdir "$shell_dir"
cp \
  "$workspace_dir/tests/embedder-shell/bootstrap.mjs" \
  "$workspace_dir/tests/embedder-shell/auxiliary.xhtml" \
  "$workspace_dir/tests/embedder-shell/shell.mjs" \
  "$workspace_dir/tests/embedder-shell/shell.xhtml" \
  "$shell_dir/"
printf 'content %s %s/\n' "$package_name" "$shell_dir_name" >> "$manifest"

(
  cd "$staging_dir/unpacked"
  zip -q -0 -r "$temporary_output" .
)
unzip -q -t "$temporary_output"
mv -f -- "$temporary_output" "$output_omni"

input_hash="$(sha256sum "$input_omni" | cut -d' ' -f1)"
output_hash="$(sha256sum "$output_omni" | cut -d' ' -f1)"
printf 'Prepared Desktop Embedder shell omnijar.\n'
printf '  package: %s\n' "$package_name"
printf '  input:   %s (%s)\n' "$input_omni" "$input_hash"
printf '  output:  %s (%s)\n' "$output_omni" "$output_hash"

