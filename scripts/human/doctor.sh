#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="${PROJECT_ROOT}/artifact_evaluation/lineage/openroad_power/p0/source/etc/DependencyInstaller.sh"
missing=0

note() {
    printf '[INFO] %s\n' "$*"
}

ok() {
    printf '[OK] %s\n' "$*"
}

warn() {
    printf '[MISSING] %s\n' "$*" >&2
    missing=1
}

require_command() {
    local command=$1
    if command -v "${command}" >/dev/null 2>&1; then
        ok "${command}: $(command -v "${command}")"
    else
        warn "${command}"
    fi
}

printf '%s\n' '=============================================='
printf '%s\n' ' GoalEvolve Environment Doctor'
printf '%s\n' '=============================================='
printf 'OS:   %s\n' "$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-unknown}" || printf unknown)"
printf 'Arch: %s\n' "$(uname -m)"
printf 'Root: %s\n\n' "${PROJECT_ROOT}"

note 'Required host commands'
for command in git make python3 cmake gcc g++ c++ bison flex swig pkg-config; do
    require_command "${command}"
done

printf '\n'
note 'Project inputs'
if [[ -x "${INSTALLER}" ]]; then
    ok "OpenROAD dependency installer: ${INSTALLER}"
else
    warn "OpenROAD dependency installer: ${INSTALLER}"
fi
if [[ -f "${PROJECT_ROOT}/artifact_evaluation/lineage/openroad_power/p0/source/CMakeLists.txt" ]]; then
    ok 'shared OpenROAD p0 source'
else
    warn 'shared OpenROAD p0 source'
fi
if [[ -w "${PROJECT_ROOT}" ]]; then
    ok 'project root is writable'
else
    warn 'project root is not writable'
fi

printf '\n'
note 'Optional AE-3 command'
if command -v codex >/dev/null 2>&1; then
    ok "codex: $(command -v codex)"
else
    note 'codex is not installed; AE-1/AE-2 remain available.'
    printf '  Install it with: make setup INSTALL_CODEX_CLI=1\n'
fi

printf '\n'
if [[ ${missing} -ne 0 ]]; then
    note 'Install missing system prerequisites, then run:'
    printf '  make setup INSTALL_SYSTEM_DEPS=1 JOBS=8\n'
    exit 1
fi

ok 'host prerequisite commands are available'
printf 'Next: make setup, then make check\n'
