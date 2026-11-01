# ============================================================
# zeocore Makefile
# ============================================================
# Standalone package (this repo IS the package root -- no monorepo prefix).
# The one gate that matters is `make verify-full`:
#   format-check -> ruff -> mypy (strict) -> arch-check -> hygiene-check ->
#   hygiene-secrets -> plugin-boundary -> pytest+coverage
# run against a clean editable install with fresh caches.
#
# Doctrine this gate enforces (carried over from the quackverse monorepo
# this package was extracted from):
#   - mypy is the closest thing Python has to `tsc`. It only checks what
#     is annotated, so NEW/CHANGED code must be fully typed or it is not
#     actually gated.
#   - The test run is the verdict. Green mypy is necessary, not sufficient.
#   - Production code must NEVER detect that it is under test
#     (no inspect.stack(), no "pytest" in sys.modules in shipped paths).
#     hygiene-check fails the build if it finds this.
# ============================================================

# Terminal colors
GREEN  := $(shell tput -Txterm setaf 2)
YELLOW := $(shell tput -Txterm setaf 3)
WHITE  := $(shell tput -Txterm setaf 7)
RESET  := $(shell tput -Txterm sgr0)
BLUE   := $(shell tput -Txterm setaf 4)
RED    := $(shell tput -Txterm setaf 1)

SHELL := /bin/bash

# Project settings
PYTHON_VERSION := 3.12
VENV_NAME := .venv
PROJECT_NAME := zeocore
REPO_ROOT := $(shell pwd)

# Where this checkout's environment, caches and build output live.
# ZEO_WORK_DIR set (the org's machines, where repositories live on one volume
# and runs on another; the operator, 2026-10-12): all of it goes under
# $(ZEO_WORK_DIR)/<checkout directory name>-<hash of its path>/ (two checkouts
# with one name, such as worktrees, never share an environment), and the
# checkout holds only the repository, bar .import_linter_cache. Unset,
# everything stays inside the checkout, as for any contributor.
ifdef ZEO_WORK_DIR
ifneq ($(words $(ZEO_WORK_DIR)),1)
$(error ZEO_WORK_DIR must be one path without spaces)
endif
ifneq ($(patsubst /%,/,$(ZEO_WORK_DIR)),/)
$(error ZEO_WORK_DIR must be an absolute path, got '$(ZEO_WORK_DIR)')
endif
ifneq ($(filter $(REPO_ROOT) $(REPO_ROOT)/%,$(abspath $(ZEO_WORK_DIR))),)
$(error ZEO_WORK_DIR must be outside the checkout, got '$(ZEO_WORK_DIR)')
endif
WORK_DIR := $(abspath $(ZEO_WORK_DIR))/$(notdir $(REPO_ROOT))-$(shell printf %s '$(REPO_ROOT)' | cksum | cut -d' ' -f1)
VENV_DIR := $(WORK_DIR)/venv
SITE_DIR := $(WORK_DIR)/site
DIST_DIR := $(WORK_DIR)/dist
PYTEST_CACHE := -o cache_dir=$(WORK_DIR)/cache/pytest
export UV_PROJECT_ENVIRONMENT := $(VENV_DIR)
export COVERAGE_FILE := $(WORK_DIR)/coverage/.coverage
export MYPY_CACHE_DIR := $(WORK_DIR)/cache/mypy
export RUFF_CACHE_DIR := $(WORK_DIR)/cache/ruff
export PYTHONPYCACHEPREFIX := $(WORK_DIR)/cache/pycache
export HYPOTHESIS_STORAGE_DIRECTORY := $(WORK_DIR)/cache/hypothesis
else
WORK_DIR := $(REPO_ROOT)
VENV_DIR := $(REPO_ROOT)/$(VENV_NAME)
SITE_DIR := $(REPO_ROOT)/site
DIST_DIR := $(REPO_ROOT)/dist
PYTEST_CACHE :=
endif
PYTHON := $(VENV_DIR)/bin/python
# The optional integrations a full development environment carries. One list,
# used by sync (make setup), install-all and verify-env, so they never drift apart.
SETUP_EXTRAS := gmail,notion,supabase,google,drive,calendar,pandoc,llms,github,bluesky,http,ffmpeg,jupytext,notebook,mcp,mcp-dev,runtime-host,revolut
comma := ,
SRC := src
PKG_SRC := src/zeo_core
TESTS := tests

# Prefer venv python if present, otherwise fall back to system python3/python
PYTHON_BIN := $(shell \
  if [ -x "$(PYTHON)" ]; then echo "$(PYTHON)"; \
  elif command -v python3 >/dev/null 2>&1; then echo python3; \
  elif command -v python >/dev/null 2>&1; then echo python; \
  else echo ""; fi)

# Test settings
PYTEST_ARGS ?= -v
COVERAGE_THRESHOLD := 90

.DEFAULT_GOAL := help

help: ## Show this help message
	@echo ''
	@echo '${YELLOW}zeocore — Development Guide${RESET}'
	@echo ''
	@echo '${YELLOW}Setup:${RESET}'
	@echo '  ${GREEN}make setup${RESET}            - Full dev environment (env + install + dev tools)'
	@echo '  ${GREEN}make check-env${RESET}        - Verify installation'
	@echo '  ${GREEN}make doctor${RESET}           - Is this machine ready? Checks everything, reports every finding + fix'
	@echo ''
	@echo '${YELLOW}THE GATE (run before every commit):${RESET}'
	@echo '  ${GREEN}make verify${RESET}           - format-check + ruff + mypy + hygiene + tests (fast gate)'
	@echo '  ${GREEN}make verify-full${RESET}      - clean install + the full gate (slow, complete, trustworthy)'
	@echo ''
	@echo '${YELLOW}Individual checks:${RESET}'
	@echo '  ${GREEN}make format${RESET}           - Auto-fix formatting (ruff + isort) — mutates files'
	@echo '  ${GREEN}make format-check${RESET}     - Check formatting only — never mutates'
	@echo '  ${GREEN}make lint${RESET}             - ruff check + ruff format --check'
	@echo '  ${GREEN}make typecheck${RESET}        - mypy (strict)'
	@echo '  ${GREEN}make hygiene-check${RESET}    - fail if production code detects tests'
	@echo '  ${GREEN}make test${RESET}             - pytest with coverage (runs ONCE)'
	@echo '  ${GREEN}make test-fast${RESET}        - pytest without coverage (quick inner loop)'
	@echo '  ${GREEN}make test-docs${RESET}        - documentation links + safe example smoke tests'
	@echo '  ${GREEN}make build${RESET}            - python -m build (sdist + wheel)'
	@echo ''
	@echo '${YELLOW}Inspection:${RESET}'
	@echo '  ${GREEN}make structure${RESET}        - Project structure + hotspots'
	@echo ''
	@echo '${YELLOW}All targets:${RESET}'
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_.-]+:.*## / {printf "  ${YELLOW}%-18s${GREEN}%s${RESET}\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ''

# ============================================================
# Environment
# ============================================================
.PHONY: env
env: ## Create virtual environment using uv
	@echo "${BLUE}Creating virtual environment...${RESET}"
	uv venv --python $(PYTHON_VERSION) $(VENV_DIR)
	@echo "${GREEN}Virtual environment created. Activate: source $(VENV_DIR)/bin/activate${RESET}"

.PHONY: install
install: ## Install zeocore (editable)
	@echo "${BLUE}Installing zeocore...${RESET}"
	@if [ ! -f "$(PYTHON)" ]; then \
	   echo "${YELLOW}Virtual environment not found. Creating it first...${RESET}"; \
	   $(MAKE) --no-print-directory env; \
	fi
	uv pip install -e . --python $(PYTHON)
	@echo "${GREEN}zeocore installed${RESET}"
	@echo "${BLUE}Verifying import (no sys.path hacks allowed)...${RESET}"
	@$(PYTHON) -c "import zeo_core; print(f'OK: zeo_core at {zeo_core.__file__}')" || \
	  (echo "${RED}Import failed — package is not cleanly installable. This is a doctrine failure, not a warning.${RESET}" && exit 1)

.PHONY: install-all
install-all: install ## Install zeocore with all optional integrations
	@echo "${BLUE}Installing optional integration dependencies...${RESET}"
	uv pip install -e ".[$(SETUP_EXTRAS)]" --python $(PYTHON)
	@echo "${GREEN}All integration dependencies installed${RESET}"

.PHONY: install-dev
install-dev: ## Install development dependencies
	@echo "${BLUE}Installing development tools...${RESET}"
	@if [ ! -f "$(PYTHON)" ]; then $(MAKE) --no-print-directory env; fi
	uv pip install -e ".[dev]" --group documentation --python $(PYTHON)
	@echo "${GREEN}Development dependencies installed${RESET}"

.PHONY: install-lint
install-lint: ## Install Tier-1 architecture linters (.[lint] extra — import-linter)
	@echo "${BLUE}Installing architecture linters (.[lint])...${RESET}"
	@if [ ! -f "$(PYTHON)" ]; then $(MAKE) --no-print-directory env; fi
	uv pip install -e ".[lint]" --python $(PYTHON)
	@echo "${GREEN}Architecture linters installed${RESET}"

.PHONY: sync
sync: ## Install exactly what uv.lock pins (every gate extra, dev, lint, docs); refuses a stale lock
	@echo "${BLUE}Installing the locked environment from uv.lock...${RESET}"
	uv sync --locked --python $(PYTHON_VERSION) \
		$(foreach extra,$(subst $(comma), ,$(SETUP_EXTRAS)) dev lint,--extra $(extra)) \
		--group documentation
	@$(PYTHON) -c "import zeo_core; print(f'OK: zeo_core at {zeo_core.__file__}')"

.PHONY: setup
setup: ## Create the development environment from uv.lock (what CI and releases test)
	@echo "${BLUE}Creating complete development environment...${RESET}"
	$(MAKE) --no-print-directory sync
	$(MAKE) --no-print-directory hooks
	@echo ""
	@echo "${GREEN}Setup complete. Activate: source $(VENV_DIR)/bin/activate${RESET}"

.PHONY: hooks
hooks: ## Point git at the committed hooks in .githooks, so a push runs make verify first
	@if ! git rev-parse --git-dir >/dev/null 2>&1; then \
	   echo "Not a git checkout, so no git hook was installed."; \
	 elif [ -z "$$(git config --get core.hooksPath)" ]; then \
	   git config core.hooksPath .githooks && \
	   echo "${GREEN}Git hooks installed: core.hooksPath = .githooks (a push runs make verify first)${RESET}"; \
	 elif [ "$$(git config --get core.hooksPath)" = ".githooks" ]; then \
	   echo "Git hooks already installed: core.hooksPath = .githooks"; \
	 else \
	   echo "${YELLOW}core.hooksPath is already set to $$(git config --get core.hooksPath), so the committed hooks are not installed; to install them, run: git config core.hooksPath .githooks${RESET}"; \
	 fi

.PHONY: check-env
check-env: ## Check that the virtual environment is active and working
	@echo "${BLUE}Checking environment...${RESET}"
	@if [ ! -f "$(PYTHON)" ]; then \
	   echo "${RED}Virtual environment not found at $(PYTHON). Run 'make setup'.${RESET}"; exit 1; \
	fi
	@$(PYTHON) --version
	@$(PYTHON) -c "import sys; print(f'Python executable: {sys.executable}')"
	@$(PYTHON) -c "import zeo_core; print(f'zeo_core: {zeo_core.__file__}')" 2>/dev/null || \
	  echo "${YELLOW}zeo_core not importable yet — run 'make install'${RESET}"
	@echo "${GREEN}Environment check complete${RESET}"

# ============================================================
# DOCTOR — "is my machine ready to work on/release zeocore?"
# ============================================================
# Unlike every other target here, doctor NEVER stops at the first problem
# (KEEP_GOING by construction, not a flag): it runs every check, counts the
# failures, and prints a full report so a beginner sees everything wrong in
# ONE pass instead of playing whack-a-mole with `make setup` one error at a
# time. Each finding is printed with its FIX right next to it — the
# audience is someone who has never debugged a Python environment before.
.PHONY: doctor
doctor: ## Check this machine is ready to develop/release zeocore (never stops at the first problem)
	@bash tools/doctor.sh "$(REPO_ROOT)" "$(PYTHON)" "$(VENV_DIR)"

# ============================================================
# THE GATE — verify / verify-full
# ============================================================
# verify       = the fast doctrine gate (no reinstall). Run before every commit.
# verify-full  = the complete gate: clean caches + fresh editable install first,
#                so it cannot pass on stale bytecode or a broken package layout.

.PHONY: verify
# verify-env: provision the environment ONLY when it is absent, and only
# from the committed lock.
#
# The canon zeo.yml v2.3 (byte-identical adoption mandatory, org issue #276)
# runs `make verify` in its post-merge zeo-certify job on a clean runner, with
# no setup step. Without .venv every stage failed on exit 127, on every main
# merge since the canon was adopted (7124cae6). Rather than diverge from the
# canon, the gate provisions itself on a clean checkout.
#
# The zeocore elders ruled (org issue #787, Q4) that this provisioning must
# use the COMMITTED lock and must not continue after a failed setup. So it is
# `uv sync --locked`, not `make setup`: `make setup` uses `uv pip install`,
# which re-resolves from pyproject.toml and ignores uv.lock. `--locked`
# refuses outright if uv.lock disagrees with pyproject.toml, so a stale lock
# fails the gate instead of being re-resolved quietly. The extras and the
# documentation group are exactly those `make setup` installs.
#
# Where .venv already exists (developer machines, and ci.yml and publish.yml,
# which run `make setup` first), nothing changes and verify never reinstalls.
.PHONY: verify-env
verify-env:
	@if [ ! -x "$(PYTHON)" ]; then \
		echo "${YELLOW}No environment at $(VENV_DIR) -- provisioning it from the committed uv.lock before the gate.${RESET}"; \
		uv sync --locked --python $(PYTHON_VERSION) \
			$(foreach extra,$(subst $(comma), ,$(SETUP_EXTRAS)) dev lint,--extra $(extra)) \
			--group documentation \
		|| { echo "${RED}Provisioning from uv.lock failed; the gate will not run.${RESET}"; exit 1; }; \
	fi

verify: verify-env ## The doctrine gate: format-check + ruff + mypy + hygiene + tests
	@echo "${BLUE}Running doctrine gate...${RESET}"
	@echo ""
	@echo "${BLUE}[1/8] format-check${RESET}"
	@$(MAKE) --no-print-directory format-check
	@echo ""
	@echo "${BLUE}[2/8] ruff lint${RESET}"
	@$(MAKE) --no-print-directory lint
	@echo ""
	@echo "${BLUE}[3/8] mypy (strict)${RESET}"
	@$(MAKE) --no-print-directory typecheck
	@echo ""
	@echo "${BLUE}[4/8] arch-check (import-linter directional contracts)${RESET}"
	@$(MAKE) --no-print-directory arch-check
	@echo ""
	@echo "${BLUE}[5/8] hygiene-check (no test-detection in production code)${RESET}"
	@$(MAKE) --no-print-directory hygiene-check
	@echo ""
	@echo "${BLUE}[6/8] hygiene-secrets${RESET}"
	@$(MAKE) --no-print-directory hygiene-secrets
	@echo ""
	@echo "${BLUE}[7/8] plugin-boundary${RESET}"
	@$(MAKE) --no-print-directory plugin-boundary
	@echo ""
	@echo "${BLUE}[8/8] tests + coverage${RESET}"
	@$(MAKE) --no-print-directory test
	@echo ""
	@$(MAKE) --no-print-directory docs-build
	@echo "${GREEN}✓ verify complete: doctrine gate passes${RESET}"

# The kit (kit/) is a Go module: its gate is separate from the Python gate, so a
# contributor who never touches kit/ needs no Go toolchain. Maintainers and CI
# run both. GOTOOLCHAIN=local: go.mod pins the toolchain and nothing downloads.
.PHONY: kit-verify
kit-verify: ## The kit gate: gofmt, vet, race tests, mutation list, offline consumer build (Go 1.27, no network)
	@command -v go >/dev/null || { echo "${RED}kit-verify needs Go 1.27 on PATH (the Python gate does not).${RESET}"; exit 1; }
	@echo "${BLUE}[kit 1/4] gofmt${RESET}"
	@cd kit && out="$$(gofmt -l .)" && [ -z "$$out" ] || { echo "${RED}gofmt would change:${RESET}"; echo "$$out"; exit 1; }
	@echo "${BLUE}[kit 2/4] go vet${RESET}"
	@cd kit && GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly go vet ./...
	@echo "${BLUE}[kit 3/4] go test -race${RESET}"
	@cd kit && GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly go test -race -count=1 ./...
	@echo "${BLUE}[kit 4/4] mutations (every guard has a test that fails without it)${RESET}"
	@cd kit && GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly ./scripts/mutate.sh scripts/mutations.txt
	@echo "${GREEN}✓ kit-verify complete${RESET}"

.PHONY: verify-full
verify-full: ## verify, preceded by clean caches + fresh editable install (slow, complete)
	@echo "${BLUE}Running FULL verification (fresh install, no stale state)...${RESET}"
	@$(MAKE) --no-print-directory clean-caches
	@$(MAKE) --no-print-directory sync
	@$(MAKE) --no-print-directory verify
	@echo ""
	@$(MAKE) --no-print-directory verify-ui
	@echo "${GREEN}✓ verify-full complete: system is healthy on a clean install${RESET}"

# --- Individual gate steps ---------------------------------

.PHONY: format
format: ## Format code with Ruff + isort (MUTATES files — not part of the gate)
	@echo "${BLUE}Formatting code...${RESET}"
	$(PYTHON) -m ruff check $(SRC) $(TESTS) --fix
	$(PYTHON) -m ruff format $(SRC) $(TESTS)
	$(PYTHON) -m isort $(SRC) $(TESTS)

.PHONY: format-check
format-check: ## Check formatting only (never mutates — gate-safe)
	@echo "${BLUE}Checking formatting...${RESET}"
	$(PYTHON) -m ruff format --check $(SRC) $(TESTS)

.PHONY: lint
lint: ## Run ruff lint + format-check
	@echo "${BLUE}Running ruff...${RESET}"
	$(PYTHON) -m ruff check $(SRC) $(TESTS)

.PHONY: typecheck
typecheck: ## Run mypy in strict mode (the closest thing to tsc)
	@echo "${BLUE}Running mypy (strict)...${RESET}"
	# explicit_package_bases + mypy_path = "src" are set in pyproject.toml's
	# [tool.mypy] (not here as CLI flags/env vars): without them mypy's CLI-target
	# walk names these files "src.zeo_core.*" while the editable install's
	# py.typed route names them "zeo_core.*", aborting with "Source file found
	# twice under different module names". This repo's flat, non-hyphenated
	# layout (no more quack-core/ prefix) means the monorepo's separate
	# RULING-111 hyphenated-directory workaround (a narrower src-only mypy
	# target) does not apply here -- full src+tests scope, no special-casing.
	$(PYTHON) -m mypy $(SRC) $(TESTS)

.PHONY: hygiene-check
hygiene-check: ## Fail if production code detects that it is under test
	@echo "${BLUE}Checking for test-detection in production code...${RESET}"
	@offenders=$$(grep -rEn 'inspect\.stack\(\)|["'"'"']pytest["'"'"'] +in +sys\.modules|_is_test_environment|f_locals|_mock_name|__name__ *== *["'"'"']MagicMock["'"'"']' $(PKG_SRC) 2>/dev/null || true); \
	if [ -n "$$offenders" ]; then \
	  echo "${RED}✗ Production code must not detect tests (green instrument, wrong render):${RESET}"; \
	  echo "$$offenders" | sed 's/^/    /'; \
	  echo "${YELLOW}  Move the behavior into the test, or inject it.${RESET}"; \
	  exit 1; \
	fi
	@echo "${GREEN}✓ hygiene OK — no mechanism-based or mock-duck-typed test-detection in production paths${RESET}"

.PHONY: test
test: ## Run tests with coverage (ONCE)
	@echo "${BLUE}Running tests with coverage...${RESET}"
	$(PYTHON) -m pytest $(TESTS) $(PYTEST_ARGS) $(PYTEST_CACHE) \
	  --cov=zeo_core --cov-report=term-missing \
	  --cov-fail-under=$(COVERAGE_THRESHOLD)

.PHONY: test-fast
test-fast: ## Run tests without coverage (quick inner-loop feedback)
	@echo "${BLUE}Running tests (no coverage)...${RESET}"
	$(PYTHON) -m pytest $(TESTS) $(PYTEST_ARGS) $(PYTEST_CACHE)

.PHONY: test-docs
test-docs: ## Check documentation links and safe beginner examples
	@echo "${BLUE}Checking documentation and beginner examples...${RESET}"
	$(PYTHON) -m pytest $(TESTS)/test_docs $(PYTEST_ARGS) $(PYTEST_CACHE)

.PHONY: docs-build docs-serve
docs-build: ## Build the searchable documentation and check links strictly
	$(PYTHON) -m unittest discover -s tests/docs_site -v
	$(PYTHON) -m mkdocs build --strict --site-dir $(SITE_DIR)

docs-serve: ## Preview the documentation locally
	$(PYTHON) -m mkdocs serve

.PHONY: test-module
test-module: ## Run one module's tests: make test-module M=test_integrations/pandoc
	@test -n "$(M)" || (echo "Usage: make test-module M=test_integrations/pandoc" && exit 1)
	$(PYTHON) -m pytest $(TESTS)/$(M) $(PYTEST_ARGS) $(PYTEST_CACHE) --cov=zeo_core --cov-report=term-missing

.PHONY: arch-check
arch-check: ## Enforce directional import contracts (.importlinter via import-linter)
	@echo "${BLUE}Checking import architecture (.importlinter)...${RESET}"
	@$(VENV_DIR)/bin/lint-imports || $(PYTHON) -m importlinter lint
	@$(MAKE) --no-print-directory integration-edge-strings
	@echo "${GREEN}✓ arch OK — directional import contracts kept${RESET}"

.PHONY: build
build: ## Build sdist + wheel (python -m build)
	@echo "${BLUE}Building sdist + wheel...${RESET}"
	$(PYTHON) -m build --outdir $(DIST_DIR)
	@echo "${GREEN}✓ build complete — see $(DIST_DIR)${RESET}"

# ============================================================
# HTTP adapter
# ============================================================
.PHONY: api-run
api-run: ## Run HTTP adapter server
	@echo "${BLUE}Starting zeo_core HTTP adapter...${RESET}"
	$(VENV_DIR)/bin/uvicorn zeo_core.adapters.http.app:create_app --factory --host 0.0.0.0 --port 8080

.PHONY: api-run-reload
api-run-reload: ## Run HTTP adapter with auto-reload
	$(VENV_DIR)/bin/uvicorn zeo_core.adapters.http.app:create_app --factory --reload --host 0.0.0.0 --port 8080

.PHONY: api-test
api-test: ## Run HTTP adapter tests
	@echo "${BLUE}Running HTTP adapter tests...${RESET}"
	$(PYTHON) -m pytest $(TESTS)/test_http $(PYTEST_ARGS) $(PYTEST_CACHE) \
	  --cov=zeo_core.adapters.http --cov-report=term-missing

# ============================================================
# Clean
# ============================================================
.PHONY: clean-caches
clean-caches: ## Remove test/type/lint caches and compiled bytecode (fast, safe)
	@echo "${BLUE}Clearing caches...${RESET}"
	@rm -rf .coverage .mypy_cache .pytest_cache .ruff_cache
	@if [ "$(WORK_DIR)" != "$(REPO_ROOT)" ]; then rm -rf "$(WORK_DIR)/cache" "$(WORK_DIR)/coverage"; fi
	@find . -type d -name "__pycache__" -not -path "./$(VENV_NAME)/*" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -not -path "./$(VENV_NAME)/*" -delete 2>/dev/null || true
	@echo "${GREEN}✓ caches cleared${RESET}"

.PHONY: clean
clean: clean-caches ## Remove build artifacts + caches (keeps venv)
	@echo "${BLUE}Cleaning build artifacts...${RESET}"
	@rm -rf build/ dist/ *.egg-info "$(DIST_DIR)" "$(SITE_DIR)"
	@echo "${GREEN}✓ cleaned${RESET}"

.PHONY: clean-all
clean-all: clean ## Remove everything including the virtual environment
	@echo "${BLUE}Removing virtual environment...${RESET}"
	@rm -rf "$(VENV_DIR)"
	@echo "${GREEN}✓ cleaned all (venv removed — run 'make setup' to rebuild)${RESET}"

# ============================================================
# Inspection / navigation
# ============================================================
.PHONY: structure
structure: ## Show project structure + hotspots
	@echo "${YELLOW}Project Summary:${RESET}"
	@echo "  Python: $$( [ -n "$(PYTHON_BIN)" ] && $(PYTHON_BIN) -V 2>/dev/null || echo 'n/a')"
	@echo "  Git branch: $$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'n/a')"
	@echo "  Files: $$(find . -type f -not -path './.git/*' -not -path './.venv/*' -not -path './.hypothesis/*' | wc -l | tr -d ' ')"
	@echo ""
	@$(MAKE) --no-print-directory structure-insights

.PHONY: structure-insights
structure-insights: ## Repo hotspots (largest dirs / file counts)
	@echo "${YELLOW}Hotspots (by file count):${RESET}"
	@find . -type f \
	   -not -path './.git/*' -not -path './.venv/*' \
	   -not -path './.hypothesis/*' \
	   | sed 's|^\./||' | awk -F/ '{print $$1}' \
	   | sort | uniq -c | sort -nr | head -n 12 \
	   | awk '{printf "  %-6s %s\n", $$1, $$2}'

.PHONY: structure-tree
structure-tree: ## Directory tree (filtered)
	@IGNORE='\.git|\.venv|__pycache__|\.DS_Store|\.hypothesis|\.pytest_cache|\.ruff_cache|\.mypy_cache|build|dist|.*\.egg-info'; \
	if command -v tree > /dev/null; then tree -a -I "$$IGNORE"; \
	else find . -not -path './.git/*' -not -path './.venv/*' -not -path './__pycache__/*' | sort; fi

# Tier-1 grep-ban gate targets
include hygiene-grepbans.mk

.PHONY: verify-ui
verify-ui: ## Locked React build, shared-corpus parity and renderer checks
	npm ci --prefix packages/react --ignore-scripts
	npm test --prefix packages/react
