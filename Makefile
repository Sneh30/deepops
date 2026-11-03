# OpsPilot Cloud — Mac-side entry point (IMPLEMENTATION.md §1/§7).
# The product runtime is Windows-only; these targets are authoring/build steps.

ifeq ($(OS),Windows_NT)
PYTHON ?= .venv/Scripts/python.exe
PWSH ?= pwsh
else
PYTHON ?= .venv/bin/python
PWSH ?= pwsh
endif

.PHONY: help lint lint-python lint-powershell test test-integration bundle clean check-config

help:
	@echo "Targets:"
	@echo "  make lint              ruff + mypy + PSScriptAnalyzer"
	@echo "  make test              unit tests (mocked Windows calls)"
	@echo "  make test-integration  Windows-only integration tests"
	@echo "  make bundle PHASE=1    dist zip + handoff/HANDOFF_1.md"
	@echo "  make clean             remove local caches and build output"

lint: lint-python lint-powershell

lint-python:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m mypy src

lint-powershell:
	@command -v $(PWSH) >/dev/null 2>&1 || { \
		echo "PowerShell 7 (pwsh) is required for PSScriptAnalyzer; install it before running make lint."; \
		exit 1; \
	}
	$(PWSH) -NoProfile -File powershell/lint.ps1

test:
	TMPDIR="$(CURDIR)/.tmp" $(PYTHON) -m pytest tests/unit -q

test-integration:
ifeq ($(OS),Windows_NT)
	$(PYTHON) -m pytest tests/integration -q
else
	@echo "Windows-only; run on the Windows-side agent (IMPLEMENTATION.md §7)." >&2
	@exit 1
endif

bundle:
	@test -n "$(PHASE)" || { echo "PHASE is required, e.g. make bundle PHASE=1"; exit 1; }
	PYTHONPATH="$(CURDIR)/src" $(PYTHON) -m orchestrator.bundle --phase $(PHASE)

check-config:
	PYTHONPATH="$(CURDIR)/src" $(PYTHON) -m orchestrator.cli check-config

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
