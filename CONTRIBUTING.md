# Contributing to ZeoCore

Thanks for considering a contribution. This document covers how to set up a
dev environment, what the verification gate expects, and how to add tests.

## Development setup

This repo uses [`uv`](https://github.com/astral-sh/uv) for environment and
dependency management, wired up through `make`:

```bash
git clone https://github.com/zeroemployeeorg/zeocore.git
cd zeocore
make setup
```

`make setup` creates a `.venv` (Python 3.14 by default -- see `PYTHON_VERSION`
in the Makefile; the package itself requires 3.14+), installs `zeocore` in
editable mode with all optional integrations, and installs the `dev` and
`lint` extras. Activate it with:

```bash
source .venv/bin/activate
```

Run `make help` at any point to see every available target.

## Before you open a PR: the gate

This repo has one gate that matters: `make verify`. Run it before every
commit you intend to submit:

```bash
make verify
```

It runs, in order: `format-check` -> `ruff` lint -> `mypy` (strict) ->
`arch-check` (import-linter directional contracts) -> `hygiene-check` (fails
if production code detects it's under test) -> `hygiene-secrets` ->
`plugin-boundary` -> the test suite with coverage (floor: 90%).

For a slower, more thorough version that reinstalls from a clean environment
first (catches issues that stale caches or a broken package layout would
hide), use:

```bash
make verify-full
```

A PR that doesn't pass `make verify` won't merge cleanly -- run it locally
first, not just in CI.

### Individual checks

If you want faster inner-loop feedback while iterating:

```bash
make format        # auto-fix formatting with ruff + isort (mutates files)
make lint           # ruff check + format --check
make typecheck       # mypy --strict
make test-fast       # pytest without coverage
make test-docs       # documentation links + safe beginner example smoke tests
make test-module M=test_fs   # run one module's tests with coverage
```

`make test-docs` is a focused inner-loop target. The full `make verify` test
step already discovers `tests/test_docs`, so documentation tests run there
exactly once without a separate gate step.

## Code style

- **Linting**: [ruff](https://docs.astral.sh/ruff/), configured in
  `pyproject.toml`'s `[tool.ruff]` / `[tool.ruff.lint]`. Enabled rule sets
  include pycodestyle (E/W), pyflakes (F), isort (I), flake8-comprehensions
  (C), flake8-bugbear (B), pyupgrade (UP), pep8-naming (N),
  flake8-annotations (ANN), flake8-bandit (S), and flake8-builtins (A).
  Line length is 88, double-quote strings.
- **Type checking**: [mypy](https://mypy-lang.org/) in strict mode
  (`disallow_untyped_defs`, `disallow_incomplete_defs`,
  `warn_return_any`, etc -- see `[tool.mypy]` in `pyproject.toml`). New or
  changed code must be fully typed; mypy only checks what's annotated, so
  untyped code isn't actually gated.
- **Import architecture**: `import-linter` enforces the directional
  contracts declared in `.importlinter` (checked by `make arch-check`).
- **Canonical import paths**: `zeo_core.tools`'s mixins
  (`IntegrationEnabledMixin`, `LifecycleMixin`, `ToolEnvInitializerMixin`)
  are implemented under `zeo_core.tools.mixins.*` but should always be
  imported from `zeo_core.tools` directly (`from zeo_core.tools import
  LifecycleMixin`, not `from zeo_core.tools.mixins.lifecycle import
  LifecycleMixin`). Set `ZEO_WARN_NONCANONICAL_IMPORTS=1` in your dev/CI
  environment to get a `FutureWarning` when this is violated -- it's
  opt-in (not on by default) because it cannot reliably distinguish a
  non-canonical import from the package's own internal bootstrap; see
  `zeo_core/tools/mixins/__init__.py`'s module docstring for the full
  reasoning.
- **Examples**: files under `examples/` must import `zeo_core` (never a
  predecessor package name) and be runnable with `uv run examples/<name>.py`.
  Credential-gated examples may skip with a printed reason; they must not
  crash on a missing extra if they document that extra.
- **Documentation links**: use repository-relative links in user-facing
  Markdown so local readers and GitHub resolve the same files. Include heading
  fragments when linking to a section, and run `make test-docs` after moving
  or renaming documentation.
- **Beginner examples**: keep the credential-free onboarding examples
  deterministic, offline, and runnable with the base install. If their
  intentional output changes, update the expected output in
  `tests/test_docs/test_examples.py`. Do not add credential, OAuth, server,
  ffmpeg, or optional-extra examples to that smoke-test allowlist.
- **No test-detection in production code**: production code (`src/zeo_core`)
  must never branch on whether it's running under test (no
  `inspect.stack()`, no `"pytest" in sys.modules`, etc). `make hygiene-check`
  fails the build if it finds this pattern. If a test needs different
  behavior, inject it (a parameter, a fixture, a fake) rather than having
  production code sniff its caller.

## Adding tests

- Tests live under `tests/`, mirroring `src/zeo_core/`'s module layout --
  see `tests/README.md` for the full structure and the fixtures available
  in `tests/conftest.py`.
- Test both success and failure paths, not just the happy path.
- Prefer the dedicated mock classes already present in each test package
  over ad hoc `MagicMock` wiring, for consistency with existing tests.
- The coverage floor is 90% (`--cov-fail-under=90` in `make test`); a PR
  that drops coverage below that will fail the gate.
- `hypothesis` is available for property-based testing where it adds real
  value (see the `dev` extra in `pyproject.toml`).

## Submitting a change

1. Fork the repository and create a branch for your change.
2. Make your change, with tests.
3. Run `make verify` locally and confirm it passes clean.
4. Open a pull request describing what changed and why.

## Reporting issues

Please open a GitHub issue with a clear description, steps to reproduce (for
bugs), and your Python/zeocore version. For vulnerabilities, see
[SECURITY.md](SECURITY.md) — do not file a public issue.

## Releasing (maintainers)

Releases go through `.github/workflows/publish.yml`, using PyPI's [trusted
publishing](https://docs.pypi.org/trusted-publishers/) (OIDC) -- no API
token is stored as a repo secret for either index.

1. Bump `pyproject.toml`'s `[project] version`. It is the single source of
   truth: installed `zeo_core.__version__` reads the distribution metadata
   through `importlib.metadata`, so there is no second version literal to edit.
2. Add a dated entry to `CHANGELOG.md`, update `RELEASE_NOTES.md` with the
   exact heading `# zeocore X.Y.Z`, update docs/examples, and run `uv lock`.
   Run `make verify`, `make build`, and `make release-check`. The latter
   verifies metadata consistency and that the version is unused on both
   indexes, using live positive controls. Run it before staging consumes
   the TestPyPI version. Index filenames cannot be reused.
3. Stage through Actions -> "Publish" -> "Run workflow", selecting the
   release branch and target `testpypi`. Require every job, including the
   clean-environment install, to pass. That smoke job gets the exact wheel
   URL from `https://test.pypi.org/pypi/zeocore/X.Y.Z/json`, verifies its
   TestPyPI file host, and installs that URL while resolving dependencies
   exclusively from `https://pypi.org/simple/`. Do not combine package indexes
   or install an unpinned latest version as evidence for this release.
4. After the reviewed release changes land on main, tag that verified commit:
   `git tag -a vX.Y.Z -m "Release zeocore X.Y.Z"`, then
   `git push origin vX.Y.Z`. This triggers real PyPI publication. The workflow
   refuses a tag that disagrees with package metadata or release notes and
   creates the GitHub release from `RELEASE_NOTES.md` after the PyPI smoke passes.
5. The workflow's own `smoke-test` job installs the just-published package
   into a clean venv and imports it -- treat that job's failure as the
   release having NOT actually succeeded, even if the publish step itself
   reported success (PyPI's own index can take a moment to propagate; the
   workflow waits, but a failure here is still real signal, not a fluke to
   ignore).

**Trusted publisher setup** (one-time, on each index's web UI — not something
a CI workflow can do for itself):

- https://pypi.org/manage/account/publishing/
- https://test.pypi.org/manage/account/publishing/

Register a trusted publisher on each with **all four** fields exact:
repository owner `profrodai`, repository name `zeocore`, workflow
filename `publish.yml` (just the filename, not the full path), and
environment name `pypi` (for the pypi.org entry) or `testpypi` (for the
test.pypi.org entry). All four are baked into the OIDC token's claims and
must match byte-for-byte, or the publish step fails with
`invalid-publisher: valid token, but no corresponding publisher` -- an
error that does not say which field is wrong, so get all four right rather
than guessing from one failed attempt.
