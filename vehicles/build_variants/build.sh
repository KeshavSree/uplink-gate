#!/usr/bin/env bash
# Builds one variant of the demo Ref deployment inside the vehicle image.
#
#   build.sh <variant> <project-version-tag> [patch ...]
#
# Each patch is applied and committed on top of the current F Prime checkout, then the commit
# is tagged so that `git describe` (which F Prime stamps into the build as the project
# version) returns exactly <project-version-tag>.
#
# The build runs twice. The first pass produces the dictionary. Its SHA-256 is then written
# into Ref/DictionaryId/DictionaryIdValue.hpp and the second pass compiles that value into the flight
# software, which reports it in the CustomVersion01 telemetry channel. The dictionary is
# generated from the FPP model only, so the second pass must not change it; the script
# fails if it does.
set -euo pipefail

variant="$1"
tag="$2"
shift 2

fprime_root="${FPRIME_ROOT:-/opt/fprime}"
project_root="${fprime_root}/TestDeploymentsProject"
out_dir="${BUILDS_DIR:-/opt/builds}/${variant}"
id_header="${project_root}/Ref/DictionaryId/DictionaryIdValue.hpp"
jobs="${BUILD_JOBS:-$(nproc)}"

write_id_header() {
    printf '// Written by vehicles/build_variants/build.sh. Do not edit.\n#define GATE_DICTIONARY_ID "%s"\n' "$1" >"${id_header}"
}

cd "${fprime_root}"
for patch in "$@"; do
    git apply --whitespace=nowarn "${patch}"
    git add -A
    git -c user.name="uplink-gate demo" -c user.email="demo@example.invalid" \
        commit -q -m "uplink-gate demo: apply $(basename "${patch}")"
done
git tag -f "${tag}"

project_version="$(git describe --tags --always --dirty --broken)"
if [ "${project_version}" != "${tag}" ]; then
    echo "expected project version ${tag}, git describe says ${project_version}" >&2
    exit 1
fi

cd "${project_root}"
write_id_header "dict-sha256:unknown"
if [ ! -d build-fprime-automatic-native ]; then
    # F Prime's default build type. A Release build of v4.3.0 trips a GCC 13
    # -Werror=stringop-overflow false positive in Os/Generic/PriorityMemQueue.cpp.
    fprime-util generate
fi
# F Prime v4.3.0 generates its version files once and never refreshes them on an incremental
# build (cmake/target/version.cmake: a custom command with no dependencies). Without this
# line, variant B would be built from new source but still carry variant A's project version.
rm -f build-fprime-automatic-native/versions/version.*
fprime-util build -j "${jobs}"

dictionary="$(find build-artifacts -name 'RefTopologyDictionary.json' | head -n 1)"
binary="$(find build-artifacts -type f -path '*/bin/Ref' | head -n 1)"
hash_first="$(sha256sum "${dictionary}" | cut -d ' ' -f 1)"

stamped_version="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["metadata"]["projectVersion"])' "${dictionary}")"
if [ "${stamped_version}" != "${tag}" ]; then
    echo "dictionary is stamped with project version ${stamped_version}, expected ${tag}" >&2
    exit 1
fi

write_id_header "dict-sha256:${hash_first:0:16}"
fprime-util build -j "${jobs}"

hash_second="$(sha256sum "${dictionary}" | cut -d ' ' -f 1)"
if [ "${hash_first}" != "${hash_second}" ]; then
    echo "dictionary changed between build passes (${hash_first} -> ${hash_second})" >&2
    exit 1
fi

mkdir -p "${out_dir}"
cp "${binary}" "${out_dir}/Ref"
cp "${dictionary}" "${out_dir}/RefTopologyDictionary.json"
cat >"${out_dir}/build.json" <<EOF
{
  "variant": "${variant}",
  "project_version": "${project_version}",
  "dictionary_sha256": "${hash_first}",
  "dictionary_id": "dict-sha256:${hash_first:0:16}"
}
EOF
echo "built variant ${variant}: project version ${project_version}, dictionary ${hash_first}"
