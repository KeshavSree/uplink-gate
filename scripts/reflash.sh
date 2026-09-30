#!/usr/bin/env bash
# Live swap: switch a running demo vehicle to another flight software build.
#   ./scripts/reflash.sh sc1 B
set -euo pipefail
vehicle="${1:?usage: reflash.sh <vehicle> <build: A|B>}"
build="${2:?usage: reflash.sh <vehicle> <build: A|B>}"
cd "$(dirname "$0")/.."
docker compose exec -T "${vehicle}" reflash "${build}"
