#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
unset GOALEVOLVE_CONDA_PREFIX OPENROAD_EXE
output="$(bash "${PROJECT_ROOT}/scripts/human/doctor.sh")"
[[ "${output}" == *'Project-local Python environment is not created yet'* ]]
[[ "${output}" == *'OPENROAD_EXE is unset'* ]]
