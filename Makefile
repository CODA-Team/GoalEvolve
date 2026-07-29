SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c

PROJECT_ROOT := $(CURDIR)
HUMAN_SCRIPTS := $(PROJECT_ROOT)/scripts/human
JOBS ?= 8
INSTALL_SYSTEM_DEPS ?= 0

.PHONY: help doctor setup check

.DEFAULT_GOAL := help

help:
	@echo "GoalEvolve environment and artifact commands"
	@echo ""
	@echo "  make doctor                         Inspect host tools and OpenROAD prerequisites"
	@echo "  make setup                          Create .venv and install pytest"
	@echo "  make setup INSTALL_SYSTEM_DEPS=1    Install upstream OpenROAD dependencies with sudo"
	@echo "  make check                          Run host checks and the AE-1 preflight"
	@echo ""
	@echo "Set JOBS=N to limit upstream dependency builds (default: $(JOBS))."

doctor:
	@bash "$(HUMAN_SCRIPTS)/doctor.sh"

setup:
	@bash "$(HUMAN_SCRIPTS)/setup.sh" --jobs "$(JOBS)" $(if $(filter 1 yes true,$(INSTALL_SYSTEM_DEPS)),--install-system-deps,)

check:
	@bash "$(HUMAN_SCRIPTS)/check.sh"
