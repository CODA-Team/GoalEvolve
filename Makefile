SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c

PROJECT_ROOT := $(CURDIR)
HUMAN_SCRIPTS := $(PROJECT_ROOT)/scripts/human
JOBS ?= 8
INSTALL_SYSTEM_DEPS ?= 0
INSTALL_CODEX_CLI ?= 0

.PHONY: help doctor setup build-tools ae2-preflight check

.DEFAULT_GOAL := help

help:
	@echo "GoalEvolve environment and artifact commands"
	@echo ""
	@echo "  make doctor                         Inspect host tools and OpenROAD prerequisites"
	@echo "  make setup                          Create .venv and install pytest"
	@echo "  make setup INSTALL_SYSTEM_DEPS=1    Install upstream OpenROAD dependencies with sudo"
	@echo "  make build-tools                    Build the isolated AE-2 CMake toolchain"
	@echo "  make ae2-preflight                  Validate the local AE-2 CMake configuration"
	@echo "  make setup INSTALL_CODEX_CLI=1      Install or update the Codex CLI with npm"
	@echo "  make check                          Run host checks and the AE-1 preflight"
	@echo ""
	@echo "Set JOBS=N to limit upstream dependency builds (default: $(JOBS))."

doctor:
	@bash "$(HUMAN_SCRIPTS)/doctor.sh"

setup:
	@bash "$(HUMAN_SCRIPTS)/setup.sh" --jobs "$(JOBS)" $(if $(filter 1 yes true,$(INSTALL_SYSTEM_DEPS)),--install-system-deps,) $(if $(filter 1 yes true,$(INSTALL_CODEX_CLI)),--install-codex-cli,)

build-tools:
	@bash "$(HUMAN_SCRIPTS)/build_ae2_toolchain.sh" --jobs "$(JOBS)"

ae2-preflight:
	@PYTHONPATH=. python3 -m artifact_evaluation.runner ae2-preflight --artifact aes_r54_student1 --verbose

check:
	@bash "$(HUMAN_SCRIPTS)/check.sh"
