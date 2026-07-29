#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOOLCHAIN_ROOT="${PROJECT_ROOT}/outputs/toolchain"
BOOST_VERSION="1.87.0"
BOOST_PREFIX="${TOOLCHAIN_ROOT}/boost-${BOOST_VERSION}"
BOOST_CONFIG="${BOOST_PREFIX}/lib/cmake/Boost-${BOOST_VERSION}/BoostConfig.cmake"
BOOST_IOSTREAMS_CONFIG="${BOOST_PREFIX}/lib/cmake/boost_iostreams-${BOOST_VERSION}/boost_iostreams-config.cmake"
AE2_CMAKE_ARGS="${TOOLCHAIN_ROOT}/ae2_cmake_args.txt"
JOBS=8

usage() {
    cat <<'EOF'
Usage: scripts/human/build_ae2_toolchain.sh [--jobs N]

Build the Boost 1.87 CMake prefix required by the shipped OR-Tools 9.14
binary. Generated files remain under outputs/toolchain/.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --jobs)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            JOBS=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

[[ "${JOBS}" =~ ^[1-9][0-9]*$ ]] || { printf '%s\n' '--jobs must be a positive integer' >&2; exit 2; }
for command in curl tar sha256sum make c++; do
    command -v "${command}" >/dev/null 2>&1 || { printf 'Missing required command: %s\n' "${command}" >&2; exit 1; }
done

mkdir -p "${TOOLCHAIN_ROOT}"
if [[ ! -f "${BOOST_CONFIG}" || ! -f "${BOOST_IOSTREAMS_CONFIG}" ]]; then
    archive="${TOOLCHAIN_ROOT}/boost_1_87_0.tar.bz2"
    source_root="${TOOLCHAIN_ROOT}/boost_1_87_0"
    expected_sha256="af57be25cb4c4f4b413ed692fe378affb4352ea50fbe294a11ef548f4d527d89"
    if [[ ! -f "${archive}" ]] || [[ "$(sha256sum "${archive}" | awk '{print $1}')" != "${expected_sha256}" ]]; then
        printf '%s\n' '[INFO] Downloading Boost 1.87.0 for the AE-2 toolchain.'
        curl --fail --location --retry 3 --continue-at - --output "${archive}" \
            'https://archives.boost.io/release/1.87.0/source/boost_1_87_0.tar.bz2'
    fi
    printf '%s  %s\n' "${expected_sha256}" "${archive}" | sha256sum --check --status || {
        printf 'Boost archive checksum failed: %s\n' "${archive}" >&2
        exit 1
    }
    if [[ ! -d "${source_root}" ]]; then
        printf '%s\n' '[INFO] Extracting Boost 1.87.0.'
        tar -xjf "${archive}" -C "${TOOLCHAIN_ROOT}"
    fi
    printf '[INFO] Building isolated Boost 1.87.0 with %s jobs.\n' "${JOBS}"
    (
        cd "${source_root}"
        ./bootstrap.sh --prefix="${BOOST_PREFIX}"
        ./b2 install --with-iostreams --with-serialization --with-system --with-thread -j "${JOBS}"
    )
fi

[[ -f "${BOOST_CONFIG}" && -f "${BOOST_IOSTREAMS_CONFIG}" ]] || {
    printf 'Boost 1.87 installation is incomplete: %s\n' "${BOOST_PREFIX}" >&2
    exit 1
}
printf '%s\n' "-DBoost_DIR=${BOOST_PREFIX}/lib/cmake/Boost-${BOOST_VERSION} -DBoost_ROOT=${BOOST_PREFIX}" > "${AE2_CMAKE_ARGS}"
printf '[OK] AE-2 Boost 1.87 prefix: %s\n' "${BOOST_PREFIX}"
printf '[OK] AE-2 CMake arguments: %s\n' "${AE2_CMAKE_ARGS}"
