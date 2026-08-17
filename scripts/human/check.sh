#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ACTIVATION_SCRIPT="${PROJECT_ROOT}/outputs/toolchain/activate.sh"

[[ -f "${ACTIVATION_SCRIPT}" ]] || {
    printf 'GoalEvolve setup is incomplete. Run: make setup JOBS=8\n' >&2
    exit 1
}
# shellcheck source=/dev/null
source "${ACTIVATION_SCRIPT}"
PYTHON="${GOALEVOLVE_CONDA_PREFIX}/bin/python"
[[ -x "${PYTHON}" ]] || { printf 'Configured Conda Python is missing: %s\n' "${PYTHON}" >&2; exit 1; }

bash "${PROJECT_ROOT}/scripts/human/doctor.sh"

printf '%s\n' '[INFO] Running AE-1 preflight.'
cd "${PROJECT_ROOT}"
PYTHONPATH=. "${PYTHON}" artifact_evaluation/ae1/run_ae1.py

if command -v codex >/dev/null 2>&1; then
    printf '[OK] Codex CLI available for AE-3: %s\n' "$(command -v codex)"
else
    printf '%s\n' '[INFO] Codex CLI is not installed; AE-3 requires: make setup INSTALL_CODEX_CLI=1'
fi
