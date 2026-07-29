#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="${PROJECT_ROOT}/artifact_evaluation/lineage/openroad_power/p0/source/etc/DependencyInstaller.sh"
VENV_ROOT="${PROJECT_ROOT}/.venv"
JOBS=8
INSTALL_SYSTEM_DEPS=0
INSTALL_CODEX_CLI=0

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

command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'python3 is required' >&2; exit 1; }
if [[ ! -x "${VENV_ROOT}/bin/python" ]]; then
    printf '[INFO] Creating Python virtual environment: %s\n' "${VENV_ROOT}"
    python3 -m venv "${VENV_ROOT}"
fi

printf '%s\n' '[INFO] Installing test tooling in the project virtual environment.'
"${VENV_ROOT}/bin/python" -m pip install --upgrade pip pytest

printf '%s\n' '[OK] Python environment is ready.'
printf 'Run: source .venv/bin/activate && make check\n'
