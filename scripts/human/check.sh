#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

bash "${PROJECT_ROOT}/scripts/human/doctor.sh"

if [[ ! -x "${PYTHON}" ]]; then
    PYTHON=python3
fi

printf '%s\n' '[INFO] Running AE-1 preflight.'
cd "${PROJECT_ROOT}"
PYTHONPATH=. "${PYTHON}" -m artifact_evaluation.runner ae1

if command -v codex >/dev/null 2>&1; then
    printf '[OK] Codex CLI available for AE-3: %s\n' "$(command -v codex)"
else
    printf '%s\n' '[INFO] Codex CLI is not installed; AE-3 requires: make setup INSTALL_CODEX_CLI=1'
fi
