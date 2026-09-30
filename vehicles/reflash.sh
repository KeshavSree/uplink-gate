#!/usr/bin/env bash
# Runs inside a vehicle container: switch the flight software to another build and reboot it.
#   reflash A|B
set -euo pipefail

STATE_DIR="${STATE_DIR:-/var/lib/vehicle}"
BUILDS_DIR="${BUILDS_DIR:-/opt/builds}"
build="${1:?usage: reflash <build>}"

if [ ! -x "${BUILDS_DIR}/${build}/Ref" ]; then
    echo "unknown build '${build}' (available: $(ls "${BUILDS_DIR}" | tr '\n' ' '))" >&2
    exit 1
fi

echo "${build}" >"${STATE_DIR}/build"
if [ -f "${STATE_DIR}/fsw.pid" ]; then
    kill "$(cat "${STATE_DIR}/fsw.pid")" 2>/dev/null || true
fi
echo "reflashed to build ${build}"
