# hygiene-grepbans.mk — Tier-1 grep-ban gate targets.
# Included by the root Makefile. Ported from the quackverse monorepo's
# quack-core Makefile; ban patterns renamed zeo_core (this repo's own
# import path) to match the zeocore standalone extraction.
# Scope = PKG_SRC (production only); -I skips binaries, --exclude-dir=__pycache__
# skips compiled artifacts (they embed absolute build paths and false-match).

.PHONY: hygiene-secrets
hygiene-secrets: ## Ban hardcoded key literals (sk-...) and absolute /Users/ paths in production
	@echo "$(BLUE)Checking for hardcoded secrets / absolute paths...$(RESET)"
	@offenders=$$(grep -rEnI --include='*.py' --exclude-dir=__pycache__ 'sk-[A-Za-z0-9]{16,}|/Users/' $(PKG_SRC) 2>/dev/null || true); \
	if [ -n "$$offenders" ]; then \
	echo "$(RED)FAIL: hardcoded secret or absolute path in production:$(RESET)"; \
	echo "$$offenders" | sed 's/^/    /'; \
	echo "$(YELLOW)  Move secrets to env; make paths relative/configurable.$(RESET)"; \
	exit 1; \
	fi
	@echo "$(GREEN)OK: no hardcoded secrets or absolute paths$(RESET)"

.PHONY: plugin-boundary
plugin-boundary: ## Ban plugin.py importing tools/capabilities (Plugin is a leaf)
	@echo "$(BLUE)Checking plugin.py boundary...$(RESET)"
	@offenders=$$(grep -rEnI 'zeo_core\.tools|zeo_core\.contracts\.capabilities' $$(find $(PKG_SRC) -name plugin.py) 2>/dev/null || true); \
	if [ -n "$$offenders" ]; then \
	echo "$(RED)FAIL: plugin.py must not import tools/capabilities:$(RESET)"; \
	echo "$$offenders" | sed 's/^/    /'; \
	exit 1; \
	fi
	@echo "$(GREEN)OK: plugin boundary clean$(RESET)"

# zeocore-SOW-36 slice 0: import-linter cannot see a module named in a string
# (importlib, a "module:Name" table), so the edges it misses are banned here.
# Outside src/zeo_core/integrations, no quoted string may name a module of an
# integration that moves, except two listed lines: cli/schemas.py names
# revolut.models (it ends in the Revolut slice), and a log message in
# prompt/_internal/enhancer.py (text, not an import). The list can only shrink.
# Limits of a grep-ban: it sees a quoted dotted name in a .py file under src, in
# any directory not named integrations; it does not see concatenation, f-strings,
# __import__ with a relative name and a package, or non-.py data (entry points,
# runtime-host bindings). The exemptions are whole lines. A grep that cannot run
# fails the target instead of passing it.
.PHONY: integration-edge-strings
integration-edge-strings: ## Ban string-named imports of the integrations that move (SOW-36)
	@echo "$(BLUE)Checking string-named integration imports...$(RESET)"
	@test -d $(PKG_SRC) || { echo "$(RED)FAIL: $(PKG_SRC) is not a directory$(RESET)"; exit 1; }
	@found=$$(grep -rEnI --include='*.py' --exclude-dir=__pycache__ --exclude-dir=integrations \
	'["'"'"']zeo_core\.integrations\.(github|google|hubspot|kit|notion|database|social|llms|revolut|meetings|gemini|pandoc|ffmpeg|jupytext|notebook)\b' $(PKG_SRC)); rc=$$?; \
	if [ $$rc -gt 1 ]; then echo "$(RED)FAIL: grep could not run (exit $$rc)$(RESET)"; exit 1; fi; \
	offenders=$$(printf '%s\n' "$$found" \
	| grep -v '^$(PKG_SRC)/cli/schemas.py:.*zeo_core\.integrations\.revolut\.models:' \
	| grep -v '^$(PKG_SRC)/prompt/_internal/enhancer.py:.*logger\.warning(' | grep -v '^$$' || true); \
	if [ -n "$$offenders" ]; then \
	echo "$(RED)FAIL: a string names a module of an integration that moves:$(RESET)"; \
	echo "$$offenders" | sed 's/^/    /'; \
	exit 1; \
	fi
	@echo "$(GREEN)OK: no string-named integration imports$(RESET)"
