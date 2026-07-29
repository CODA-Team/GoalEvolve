#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="${PROJECT_ROOT}/artifact_evaluation/lineage/openroad_power/p0/source/etc/DependencyInstaller.sh"
P0_GENERATED_DEPS_FILE="${PROJECT_ROOT}/artifact_evaluation/lineage/openroad_power/p0/source/etc/openroad_deps_prefixes.txt"
TOOLCHAIN_ROOT="${PROJECT_ROOT}/outputs/toolchain"
TOOLCHAIN_DEPS_FILE="${TOOLCHAIN_ROOT}/openroad_deps_prefixes.txt"
VENV_ROOT="${PROJECT_ROOT}/.venv"
JOBS=8
INSTALL_SYSTEM_DEPS=0
INSTALL_CODEX_CLI=0

is_debian_family() {
    [[ -f /etc/os-release ]] || return 1
    . /etc/os-release
    [[ "${ID:-}" == "ubuntu" || "${ID:-}" == "debian" || "${ID_LIKE:-}" == *debian* ]]
}

eigen_version() {
    local macros=$1
    local world major
    world=$(awk '/#define EIGEN_WORLD_VERSION/{print $3}' "${macros}")
    major=$(awk '/#define EIGEN_MAJOR_VERSION/{print $3}' "${macros}")
    printf '%s.%s' "${world}" "${major}"
}

prepare_debian_compatibility_dependencies() {
    is_debian_family || return 0

    run_privileged() {
        if [[ ${EUID} -eq 0 ]]; then
            "$@"
        else
            sudo "$@"
        fi
    }

    # Ubuntu installs Eigen to /usr/include, while the frozen OpenROAD
    # installer checks /usr/local/include before trying GitLab. Reuse the
    # distribution package when it supplies the required 3.4 headers.
    if [[ ! -f /usr/include/eigen3/Eigen/src/Core/util/Macros.h ]]; then
        printf '%s\n' '[INFO] Installing Ubuntu/Debian Eigen headers for the frozen OpenROAD dependency check.'
        run_privileged apt-get update
        run_privileged apt-get install -y libeigen3-dev
    fi

    local system_eigen=/usr/include/eigen3/Eigen/src/Core/util/Macros.h
    if [[ -f "${system_eigen}" && "$(eigen_version "${system_eigen}")" == "3.4" ]]; then
        if [[ ! -e /usr/local/include/eigen3 ]]; then
            printf '%s\n' '[INFO] Exposing system Eigen 3.4 at /usr/local/include for the frozen OpenROAD installer.'
            run_privileged mkdir -p /usr/local/include
            run_privileged ln -s /usr/include/eigen3 /usr/local/include/eigen3
        fi
    else
        printf '%s\n' '[WARN] System Eigen 3.4 was not found; the frozen installer will attempt its upstream GitLab download.' >&2
    fi

    return 0
}

ensure_python_venv_support() {
    python3 -c 'import ensurepip' >/dev/null 2>&1 && return
    if [[ ${INSTALL_SYSTEM_DEPS} -eq 1 ]] && is_debian_family; then
        printf '%s\n' '[INFO] Installing Python venv support.'
        if [[ ${EUID} -eq 0 ]]; then
            apt-get update
            apt-get install -y python3-venv
        else
            sudo apt-get update
            sudo apt-get install -y python3-venv
        fi
        python3 -c 'import ensurepip' >/dev/null 2>&1 && return
    fi
    printf '%s\n' 'Python venv support is unavailable. On Ubuntu/Debian run: sudo apt-get install -y python3-venv' >&2
    exit 1
}

remove_stale_p0_dependency_prefixes() {
    [[ -e "${P0_GENERATED_DEPS_FILE}" ]] || return 0
    printf '[INFO] Removing generated dependency-prefix file from the immutable p0 snapshot.\n'
    if [[ ${EUID} -eq 0 ]]; then
        rm -f "${P0_GENERATED_DEPS_FILE}"
    else
        sudo rm -f "${P0_GENERATED_DEPS_FILE}"
    fi
}

usage() {
    cat <<'EOF'
Usage: scripts/human/setup.sh [--jobs N] [--install-system-deps] [--install-codex-cli]

Creates the project Python virtual environment and installs pytest.
--install-system-deps explicitly permits sudo execution of the frozen
OpenROAD DependencyInstaller.sh -all command.
--install-codex-cli installs or updates @openai/codex with npm. It does not
create, read, or configure any API key.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --jobs)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            JOBS=$2
            shift 2
            ;;
        --install-system-deps)
            INSTALL_SYSTEM_DEPS=1
            shift
            ;;
        --install-codex-cli)
            INSTALL_CODEX_CLI=1
            shift
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

command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'python3 is required' >&2; exit 1; }
ensure_python_venv_support

if [[ ${INSTALL_SYSTEM_DEPS} -eq 1 ]]; then
    [[ -x "${INSTALLER}" ]] || { printf 'Missing dependency installer: %s\n' "${INSTALLER}" >&2; exit 1; }
    printf '%s\n' '[INFO] Installing frozen OpenROAD system and common dependencies.'
    if [[ ${EUID} -ne 0 ]]; then
        command -v sudo >/dev/null 2>&1 || { printf '%s\n' 'sudo is required for --install-system-deps' >&2; exit 1; }
    fi
    prepare_debian_compatibility_dependencies
    remove_stale_p0_dependency_prefixes
    # Keep upstream dependency provenance in the machine-local state directory,
    # outside the immutable p0 source snapshot. AE-2 uses the separate,
    # compatible prefix created by make build-tools.
    mkdir -p "${TOOLCHAIN_ROOT}"
    if [[ ${EUID} -eq 0 ]]; then
        "${INSTALLER}" -all "-threads=${JOBS}" -save-deps-prefixes="${TOOLCHAIN_DEPS_FILE}"
    else
        sudo "${INSTALLER}" -all "-threads=${JOBS}" -save-deps-prefixes="${TOOLCHAIN_DEPS_FILE}"
    fi
    [[ -s "${TOOLCHAIN_DEPS_FILE}" ]] || {
        printf 'OpenROAD dependency prefix record was not created: %s\n' "${TOOLCHAIN_DEPS_FILE}" >&2
        exit 1
    }
    printf '[OK] OpenROAD CMake prefix record: %s\n' "${TOOLCHAIN_DEPS_FILE}"
fi

if [[ ${INSTALL_CODEX_CLI} -eq 1 ]]; then
    NPM_BIN="${CODEX_NPM_BIN:-npm}"
    command -v "${NPM_BIN}" >/dev/null 2>&1 || {
        printf 'npm is required to install the Codex CLI; set CODEX_NPM_BIN if npm is not on PATH.\n' >&2
        exit 1
    }
    printf '%s\n' '[INFO] Installing or updating the Codex CLI package.'
    "${NPM_BIN}" install --global @openai/codex
    command -v codex >/dev/null 2>&1 || {
        printf '%s\n' 'Codex CLI installed but is not on PATH; add npm bin -g to PATH.' >&2
        exit 1
    }
    printf '[OK] Codex CLI: %s\n' "$(command -v codex)"
fi

if [[ -x "${VENV_ROOT}/bin/python" ]] && ! "${VENV_ROOT}/bin/python" -m pip --version >/dev/null 2>&1; then
    printf '[INFO] Recreating incomplete Python virtual environment: %s\n' "${VENV_ROOT}"
    rm -rf "${VENV_ROOT}"
fi
if [[ ! -x "${VENV_ROOT}/bin/python" ]]; then
    printf '[INFO] Creating Python virtual environment: %s\n' "${VENV_ROOT}"
    python3 -m venv "${VENV_ROOT}"
fi

printf '%s\n' '[INFO] Installing test tooling in the project virtual environment.'
"${VENV_ROOT}/bin/python" -m pip install --upgrade pip pytest

printf '%s\n' '[OK] Python environment is ready.'
printf 'Run: source .venv/bin/activate && make check\n'
