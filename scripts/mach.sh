#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0


set -euo pipefail

navis_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "$navis_dir/.." && pwd)"
runtime_dir="$workspace_dir/runtime"
default_gecko_dir="$runtime_dir/gecko"
gecko_dir="${NAVIS_GECKO_DIR:-$default_gecko_dir}"

# A build host may expose a Mozilla bootstrap state directory outside the
# invoking user's default location. Keep that choice explicit, then make the
# tool paths and WASI sysroot deterministic for every Mach subcommand.
mozbuild_state_dir="${NAVIS_MOZBUILD_STATE_PATH:-${MOZBUILD_STATE_PATH:-}}"
if [[ -n "$mozbuild_state_dir" ]]; then
  if [[ "$mozbuild_state_dir" != /* || ! -d "$mozbuild_state_dir" ]]; then
    printf 'Navis Mozilla build-state directory is invalid: %s\n' \
      "$mozbuild_state_dir" >&2
    exit 1
  fi
  export MOZBUILD_STATE_PATH="$mozbuild_state_dir"
  navis_tool_path=""
  for relative_dir in sccache clang/bin cbindgen node/bin nasm; do
    candidate_dir="$mozbuild_state_dir/$relative_dir"
    if [[ -d "$candidate_dir" ]]; then
      navis_tool_path+="$candidate_dir:"
    fi
  done
  export PATH="${navis_tool_path}${PATH}"
  if [[ -z "${WASI_SYSROOT:-}" && \
    -d "$mozbuild_state_dir/sysroot-wasm32-wasi" ]]; then
    export WASI_SYSROOT="$mozbuild_state_dir/sysroot-wasm32-wasi"
  fi
fi

if [[ "$gecko_dir" != /* ]]; then
  gecko_dir="$runtime_dir/$gecko_dir"
fi
if [[ ! -e "$gecko_dir/.git" || ! -x "$gecko_dir/mach" ]]; then
  printf 'Navis Gecko source tree is invalid: %s\n' "$gecko_dir" >&2
  exit 1
fi
gecko_dir="$(cd "$gecko_dir" && pwd)"

mach_python="${NAVIS_PYTHON:-}"

if [[ -z "$mach_python" ]] && command -v python3.12 >/dev/null 2>&1; then
  mach_python="$(command -v python3.12)"
elif [[ -z "$mach_python" ]]; then
  mach_python="$(command -v python3)"
fi

if [[ "$gecko_dir" == "$default_gecko_dir" ]]; then
  "$navis_dir/scripts/prepare-gecko.sh"
elif [[ ! -e "$gecko_dir/navis" || ! -e "$gecko_dir/desktop-embedder" ]]; then
  printf 'Alternate Gecko tree is missing the Navis/embedder mounts: %s\n' \
    "$gecko_dir" >&2
  exit 1
fi
requested_mozconfig="${NAVIS_MOZCONFIG:-$runtime_dir/mozconfig.runtime}"
if [[ "$requested_mozconfig" = /* ]]; then
  export MOZCONFIG="$requested_mozconfig"
else
  export MOZCONFIG="$runtime_dir/$requested_mozconfig"
fi

if [[ ! -f "$MOZCONFIG" ]]; then
  printf 'Navis mozconfig does not exist: %s\n' "$MOZCONFIG" >&2
  exit 1
fi

# Navis builds are deliberately non-telemetry invocations. Bypass Mach's
# asynchronous Glean initialization so disabled collection cannot hold up
# interpreter shutdown.
export MACH_MAIN_PID="${MACH_MAIN_PID:-0}"

# Host resource limits vary without changing the target/configuration identity.
# client.mk also reads MOZ_MAKE_FLAGS from the profile. Pass that variable as
# a make command-line override so recursive make cannot restore its -j16.
# Keep the profile default when no override is given.
if [[ "${1:-}" == "build" && -n "${NAVIS_BUILD_JOBS:-}" ]]; then
  if [[ ! "$NAVIS_BUILD_JOBS" =~ ^[1-9][0-9]*$ ]]; then
    printf 'NAVIS_BUILD_JOBS must be a positive integer.\n' >&2
    exit 1
  fi
  export MAKEFLAGS="${MAKEFLAGS:+$MAKEFLAGS }MOZ_MAKE_FLAGS=-j$NAVIS_BUILD_JOBS"
  shift
  set -- build "--jobs=$NAVIS_BUILD_JOBS" "$@"
fi

cd "$gecko_dir"
exec "$mach_python" "$gecko_dir/mach" "$@"
