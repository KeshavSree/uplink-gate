#!/usr/bin/env bash
# Runs one demo vehicle: the ground data system (fprime-gds, headless) plus the flight
# software binary of the currently "flashed" build.
#
# The flashed build is the single letter in ${STATE_DIR}/build (A or B). `reflash <build>`
# rewrites that file and stops the flight software; this loop then starts the other binary.
# The ground data system keeps running across a reflash, as a ground station would.
set -uo pipefail

STATE_DIR="${STATE_DIR:-/var/lib/vehicle}"
BUILDS_DIR="${BUILDS_DIR:-/opt/builds}"
GDS_TTS_PORT="${GDS_TTS_PORT:-50050}"
FSW_PORT="${FSW_PORT:-50000}"

mkdir -p "${STATE_DIR}" /tmp/uplink
[ -f "${STATE_DIR}/build" ] || echo "${VEHICLE_BUILD:-A}" >"${STATE_DIR}/build"
initial_build="$(cat "${STATE_DIR}/build")"

# fprime-gds needs a dictionary to configure framing (spacecraft ID, frame size). Those
# values are the same in every demo build, so the initial build's dictionary is kept.
fprime-gds --no-app --gui none --no-zmq \
    --dictionary "${BUILDS_DIR}/${initial_build}/RefTopologyDictionary.json" \
    --tts-addr 0.0.0.0 --tts-port "${GDS_TTS_PORT}" \
    --ip-address 127.0.0.1 --ip-port "${FSW_PORT}" \
    --logs "${STATE_DIR}/gds-logs" --log-directly \
    --file-storage-directory "${STATE_DIR}/gds-files" &
gds_pid=$!

fsw_pid=""
shutdown() {
    [ -n "${fsw_pid}" ] && kill "${fsw_pid}" 2>/dev/null
    kill "${gds_pid}" 2>/dev/null
    wait 2>/dev/null
    exit 0
}
trap shutdown TERM INT

cd "${STATE_DIR}"
while kill -0 "${gds_pid}" 2>/dev/null; do
    build="$(cat "${STATE_DIR}/build")"
    echo "vehicle ${VEHICLE_NAME:-?}: starting flight software build ${build} ($(grep -o '"project_version": *"[^"]*"' "${BUILDS_DIR}/${build}/build.json"))"
    # The flight software keeps retrying its connection until the ground data system listens.
    "${BUILDS_DIR}/${build}/Ref" -a 127.0.0.1 -p "${FSW_PORT}" &
    fsw_pid=$!
    echo "${fsw_pid}" >"${STATE_DIR}/fsw.pid"
    # Returns when either process exits: a reflash stops the flight software, and a dead
    # ground data system ends the container.
    wait -n "${fsw_pid}" "${gds_pid}"
    kill "${fsw_pid}" 2>/dev/null
    wait "${fsw_pid}" 2>/dev/null
    sleep 1
done
echo "vehicle ${VEHICLE_NAME:-?}: ground data system exited" >&2
exit 1
