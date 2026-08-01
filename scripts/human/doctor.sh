#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="${PROJECT_ROOT}/artifact_evaluation/lineage/openroad_power/p0/source/etc/DependencyInstaller.sh"
SNAPSHOT_VERIFIER="${PROJECT_ROOT}/artifact_evaluation/verify_openroad_snapshot.py"
ACTIVATION_SCRIPT="${PROJECT_ROOT}/outputs/toolchain/activate.sh"
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

advisory() {
    printf '[NOTICE] %s\n' "$*"
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
architecture="$(uname -m)"
printf 'Arch: %s\n' "${architecture}"
printf 'Root: %s\n\n' "${PROJECT_ROOT}"

case "${architecture}" in
    x86_64|amd64)
        ok 'platform: Linux x86_64 (validated AE-1/AE-2/AE-3 platform)'
        ;;
    aarch64|arm64)
        advisory 'platform: Linux ARM64 is not validated for AE-2/AE-3; installation may work, but build/flow QoR is not guaranteed.'
        ;;
    *)
        advisory "platform: ${architecture} is not validated for AE-2/AE-3."
        ;;
esac

note 'Required host commands'
for command in git make; do
    require_command "${command}"
done

if command -v conda >/dev/null 2>&1 || [[ -x "${PROJECT_ROOT}/outputs/toolchain/miniforge3/bin/conda" ]]; then
    ok 'conda: available or project-local Miniforge present'
elif command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then
    advisory 'conda is not installed; make setup will bootstrap project-local Miniforge.'
else
    warn 'conda is unavailable and neither curl nor wget can bootstrap Miniforge'
fi

if [[ -f "${ACTIVATION_SCRIPT}" ]]; then
    # shellcheck source=/dev/null
    source "${ACTIVATION_SCRIPT}"
    printf '\n'
    note 'Project-local Python environment'
    require_command python
else
    advisory 'Project-local Python environment is not created yet; run make setup.'
fi

printf '\n'
note 'Prepared OpenROAD/ORFS environment (required for AE-2/AE-3)'
if [[ -n "${OPENROAD_EXE:-}" ]]; then
    if [[ -x "${OPENROAD_EXE}" ]]; then
        ok "OPENROAD_EXE: ${OPENROAD_EXE}"
        if "${OPENROAD_EXE}" -version >/dev/null 2>&1; then
            ok 'prepared OpenROAD executable starts'
        else
            warn 'prepared OpenROAD executable does not start in the current shell environment'
        fi
    else
        warn "OPENROAD_EXE is not executable: ${OPENROAD_EXE}"
    fi
else
    advisory 'OPENROAD_EXE is unset; AE-1 is available, while AE-2/AE-3 require an activated matching OpenROAD/ORFS workspace.'
fi

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
if [[ -f "${SNAPSHOT_VERIFIER}" ]]; then
    verifier_python=python3
    if [[ -x "${GOALEVOLVE_CONDA_PREFIX:-}/bin/python" ]]; then
        verifier_python="${GOALEVOLVE_CONDA_PREFIX}/bin/python"
    fi
    if snapshot_report=$("${verifier_python}" "${SNAPSHOT_VERIFIER}" --verify 2>&1); then
        ok 'shared OpenROAD p0 content digest'
    else
        warn 'shared OpenROAD p0 content digest differs from the release manifest'
        printf '%s\n' "${snapshot_report}" >&2
        printf '%s\n' '  Restore only the immutable p0 source with:' >&2
        printf '%s\n' '  git restore --source=HEAD --worktree -- artifact_evaluation/lineage/openroad_power/p0/source' >&2
    fi
else
    warn "shared OpenROAD p0 verifier: ${SNAPSHOT_VERIFIER}"
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
    note 'Install Git/Make and a bootstrap downloader on the host, then run:'
    printf '  make setup\n'
    exit 1
fi

ok 'host prerequisites are available'
printf 'Next: make setup, then make check (or activate OpenROAD/ORFS and set OPENROAD_EXE for AE-2/AE-3)\n'
