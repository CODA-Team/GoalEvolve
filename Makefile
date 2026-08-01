SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c

PROJECT_ROOT := $(CURDIR)
HUMAN_SCRIPTS := $(PROJECT_ROOT)/scripts/human
JOBS ?= 8
INSTALL_CODEX_CLI ?= 0

.PHONY: help doctor setup build-tools ae2-preflight check

.DEFAULT_GOAL := help

help:
	@echo "GoalEvolve environment and artifact commands"
	@echo ""
	@echo "  make doctor                         Inspect host tools and OpenROAD prerequisites"
	@echo "  make setup                          Create the project-local Python environment"
	@echo "  make setup INSTALL_CODEX_CLI=1      Install or update the project-local Codex CLI"
	@echo "  make check                          Run host checks and the AE-1 preflight"
	@echo ""
	@echo "Set JOBS=N to limit upstream dependency builds (default: $(JOBS))."

doctor:
	@bash "$(HUMAN_SCRIPTS)/doctor.sh"

setup:
	@bash "$(HUMAN_SCRIPTS)/setup.sh" $(if $(filter 1 yes true,$(INSTALL_CODEX_CLI)),--install-codex-cli,)

build-tools:
	@echo "GoalEvolve uses a prepared host OpenROAD/ORFS toolchain for AE-2 and AE-3."
	@echo "Set OPENROAD_EXE to the matching executable, then run the AE-2 command in README.md."
	@exit 2

ae2-preflight:
	@test -f "$(PROJECT_ROOT)/outputs/toolchain/activate.sh" || { echo "Run: make setup" >&2; exit 2; }
	@source "$(PROJECT_ROOT)/outputs/toolchain/activate.sh"; PYTHONPATH=. "$$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2-preflight --artifact aes_r54_student1 --verbose

check:
	@bash "$(HUMAN_SCRIPTS)/check.sh"
