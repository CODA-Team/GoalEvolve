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
