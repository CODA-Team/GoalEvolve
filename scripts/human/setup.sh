#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="${PROJECT_ROOT}/artifact_evaluation/lineage/openroad_power/p0/source/etc/DependencyInstaller.sh"
VENV_ROOT="${PROJECT_ROOT}/.venv"
JOBS=8
INSTALL_SYSTEM_DEPS=0

usage() {
    cat <<'EOF'
Usage: scripts/human/setup.sh [--jobs N] [--install-system-deps]

Creates the project Python virtual environment and installs pytest.
--install-system-deps explicitly permits sudo execution of the frozen
OpenROAD DependencyInstaller.sh -all command.
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

if [[ ${INSTALL_SYSTEM_DEPS} -eq 1 ]]; then
    [[ -x "${INSTALLER}" ]] || { printf 'Missing dependency installer: %s\n' "${INSTALLER}" >&2; exit 1; }
    printf '%s\n' '[INFO] Installing frozen OpenROAD system and common dependencies.'
    if [[ ${EUID} -eq 0 ]]; then
        "${INSTALLER}" -all "-threads=${JOBS}"
    else
        command -v sudo >/dev/null 2>&1 || { printf '%s\n' 'sudo is required for --install-system-deps' >&2; exit 1; }
        sudo "${INSTALLER}" -all "-threads=${JOBS}"
    fi
fi

command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'python3 is required' >&2; exit 1; }
if [[ ! -x "${VENV_ROOT}/bin/python" ]]; then
    printf '[INFO] Creating Python virtual environment: %s\n' "${VENV_ROOT}"
    python3 -m venv "${VENV_ROOT}"
fi

printf '%s\n' '[INFO] Installing test tooling in the project virtual environment.'
"${VENV_ROOT}/bin/python" -m pip install --upgrade pip pytest

printf '%s\n' '[OK] Python environment is ready.'
printf 'Run: source .venv/bin/activate && make check\n'
