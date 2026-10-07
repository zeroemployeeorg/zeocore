# ZeoCore

<!-- Teaches CLAUDE.md Rev 17; reviewed 2026-09-10: Runtime host and meeting source APIs. -->

[Documentation](https://profrodai.github.io/zeocore/) · [Release notes](RELEASE_NOTES.md)

[![CI](https://github.com/profrodai/zeocore/workflows/CI/badge.svg)](https://github.com/profrodai/zeocore/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/zeocore.svg)](https://pypi.org/project/zeocore/)
[![Python versions](https://img.shields.io/pypi/pyversions/zeocore.svg)](https://pypi.org/project/zeocore/)
[![Coverage](https://img.shields.io/badge/coverage-90%25-brightgreen.svg)](https://github.com/profrodai/zeocore)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**ZeoCore is a Python framework for writing capabilities: small, typed,
named units of work that anything can call.**

You write a function once — giving it an identity, a typed request and
response, a declaration of its side effects, and a structured result — and
that same function can then be run by a script, served over HTTP, exposed to
an MCP-native coding agent like Claude Code or Cursor, or handed to an LLM as
a callable tool. You don't rewrite it for each destination.

**New here? Start with the [Quickstart](QUICKSTART.md).** It takes you from
an empty folder to a running capability in about ten minutes, and assumes no
prior knowledge of ZeoCore.

Connect external services with the [integration account setup guides](docs/integrations/README.md):
credential acquisition, test accounts, production accounts and runnable checks
for every supported integration.

For Runtime-supervised applications, start with the
[provider registration guide](docs/how-to/provider-registration.md) and the
[0.11.0 setup](#new-in-0110-runtime-host-and-meeting-operations).

## Who this is for

- **Students and newcomers** learning how to structure real Python tools —
  typed inputs, explicit error handling, no hidden global state.
- **Developers** building automation, content pipelines, or integrations who
  don't want to re-solve configuration, filesystem, and error handling in
  every project.
- **Teams** in the Zero Employee ecosystem who need one authoring surface
  that runners, HTTP services, and agents can all consume.

You should be comfortable writing Python functions and classes. You do *not*
need prior experience with Pydantic, MCP, or agent frameworks.

## Requirements

**Python 3.14 or newer.** That's the only hard requirement. (The floor moved
to `>=3.14` in 0.6.0, matching sovereign-agent; if you're pinned to an older
interpreter, stay on 0.5.0, which requires `>=3.13`.)

Not sure what you have? Run `python3 --version` on macOS/Linux or
`py --version` on Windows. The [Quickstart](QUICKSTART.md#step-1-check-your-python-version)
walks through installing 3.14 if you need it.

## Install

```bash
uv pip install zeocore
# or, without uv
pip install zeocore
```

The package is `zeocore`; the module you import is `zeo_core`.

Normal installations also include the inert
[ZEOconnect managed-profile client](docs/tutorials/zeoconnect-hosted-profile.md).
It performs no import-time or default-profile networking. Applications opt into
hosted execution explicitly and pair through the browser; provider credentials
never enter application code.

## Your first capability

```python
import logging
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from zeo_core.contracts import CapabilityExample, CapabilityResult, EffectKind
from zeo_core.core.fs import get_service as get_fs_service
from zeo_core.tools import ToolContext, bound_capability_of, capability, invoke_sync


class GreetRequest(BaseModel):
    name: str


class GreetResponse(BaseModel):
    message: str


@capability(
    id="demo.greet@1.0.0",
    description="Greet a person by name.",
    effects={EffectKind.READ},
    examples=(
        CapabilityExample(
            request={"name": "World"},
            response={"message": "Hello, World!"},
        ),
    ),
)
def greet(request: GreetRequest, ctx: ToolContext) -> CapabilityResult[GreetResponse]:
    ctx.require_logger().info("greeting %s", request.name)
    return CapabilityResult.ok(data=GreetResponse(message=f"Hello, {request.name}!"))


with TemporaryDirectory() as tmp:
    ctx = ToolContext(
        run_id="demo-run-001",
        tool_name="greet",
        tool_version="1.0.0",
        logger=logging.getLogger("greet"),
        fs=get_fs_service(),
        work_dir=tmp,
        output_dir=tmp,
    )
    result = invoke_sync(bound_capability_of(greet), GreetRequest(name="World"), ctx)
    print(result.status.value, "|", result.data.message)
```

Expected output:

```
success | Hello, World!
```

Line-by-line explanation of that script lives in
[QUICKSTART.md](QUICKSTART.md#what-each-part-of-that-file-does).

## The mental model

Four ideas carry the whole framework.

**1. A capability is identified, not just named.** Identity is
`namespace.name@semver` (`demo.greet@1.0.0`), so versions can coexist and
callers can pin one.

**2. The request and response are Pydantic models.** JSON Schema is generated
from those models, which is how HTTP, MCP, and LLM adapters can call your
capability without you hand-writing schema for each.

**3. Everything from the outside world arrives via `ToolContext`.** Logger,
filesystem, config, and any services the caller wired in — your capability
asks the context instead of reaching for ambient global state. Absence of a
declared service fails closed.

**4. Expected failures are returned, not raised.** A `CapabilityResult` is
success, skip, or error, always structured. Exceptions are for what the
*caller* didn't expect; a `CapabilityResult` is for what the *tool* expects
and needs to report cleanly — validation failed, a downstream API errored, an
optional integration wasn't configured. Callers get one shape to check
(`result.status`, plus fine-grained `result.outcome`) instead of a
`try`/`except` matrix, so a runner orchestrating many tools can log, retry,
or persist every result the same way. For genuinely exceptional cases,
ZeoCore's typed `ZeoError` hierarchy gives you catchable types instead of
string parsing — see [`examples/error_handling.py`](examples/error_handling.py).

Class-based tools are still supported: subclass `BaseZeoTool`, implement
`run(request, ctx) -> CapabilityResult`, and adapt with `tool_to_capability`.
See [`examples/minimal_tool.py`](examples/minimal_tool.py) and
[`examples/tool_to_capability.py`](examples/tool_to_capability.py).

mypy checks all of it end to end.

## Learn ZeoCore

| Start here | What it gives you |
|---|---|
| [QUICKSTART.md](QUICKSTART.md) | Install Python 3.14, make a venv, write and run your first capability. No prior knowledge assumed. |
| [docs/README.md](docs/README.md) | The learning hub: tutorials, a guided path through the examples, and reference material. |
| [docs/tutorials/capability-authoring.md](docs/tutorials/capability-authoring.md) | The canonical authoring tutorial — registry, guards, manifests, adapter binding. |
| [GET-STARTED.md](GET-STARTED.md) | The full manual: configuration, paths, filesystem, plugins, every integration, adapters, troubleshooting. |
| [docs/reference/api.md](docs/reference/api.md) | The public API surface, symbol by symbol, and which import paths are supported. |
| [llms.txt](llms.txt) | A condensed import map for coding agents. |

## Examples

Every example under [`examples/`](examples/) is a real, runnable script —
none are illustrative fragments. Run any of them with
`uv run examples/<name>.py`.

- [`capability_authoring.py`](examples/capability_authoring.py) — canonical
  `@capability` authoring, registry, and `invoke_sync`.
- [`minimal_tool.py`](examples/minimal_tool.py) — the smallest class tool: no
  mixins, no services, just `run()`.
- [`capability_guards.py`](examples/capability_guards.py) — a `RequestGuard`
  rejecting a request before the handler runs.
- [`error_handling.py`](examples/error_handling.py) — the `ZeoError` family.
- [`config_usage.py`](examples/config_usage.py) — `load_config()`'s three
  real behaviors.

The [docs hub](docs/README.md#runnable-examples) indexes the runnable examples,
grouped by topic.

Examples that need a credential (`NOTION_TOKEN`, `GITHUB_TOKEN`, an LLM API
key, …) read it from the process environment. Copy
[`.env.example`](.env.example) to `.env`, fill in real values, and load it
however your shell or tooling prefers (e.g. `uv run --env-file .env ...`) —
see [GET-STARTED.md's "Secrets and `.env`"](GET-STARTED.md#secrets-and-env)
section.

## Kit marketing

[Kit newsletters and sequences](docs/tutorials/kit-marketing.md) provides
broadcasts, sequence authoring, subscriber consent, tags and reporting through
thirteen registered agent capabilities. Run `python examples/kit_usage.py` offline.

## HubSpot marketing

[HubSpot newsletters and automation](docs/tutorials/hubspot-marketing.md) provides
registered capabilities for drafts, campaigns, subscription preferences and
marketing email sequences. Included in 0.10.0; no extra dependency is needed.

## New in 0.10.0

[Managed environments](docs/integrations/environments.md) keep test and production
credentials and state separate. The [account setup index](docs/integrations/README.md)
covers acquisition of keys, test accounts, real accounts and bounded E2E checks.
[Gemini reference images](docs/integrations/gemini-images.md) use admitted effects,
private artifacts and reconciliation. [Notebook execution](docs/integrations/notebook-execution.md)
and [the authoring reference](docs/integrations/authoring-reference.md) provide
fresh-kernel execution and independently checked staging receipts.
See [release notes](RELEASE_NOTES.md) for migration and remaining qualification limits.

## New in 0.11.0: Runtime host and meeting operations

The Runtime host and meeting adapters are available in **0.11.0**. Install the
package below and run the examples from the matching `v0.11.0` checkout:

```bash
uv pip install "zeocore[runtime-host]==0.11.0"
python examples/runtime_host_catalogue_v1.py
python examples/meeting_request_v1.py
```

The first example builds a provider catalogue and a validated, canonical request.
The second prepares an exact meeting read request. Both run offline without
credentials, admission or provider calls.

- [Provider registration](docs/how-to/provider-registration.md) explains capabilities,
  providers, adapters, integrations and the existing plugin registry.
- [Runtime host](docs/how-to/runtime-host.md) documents `zeo-capability`, trusted
  launch context, bounded IPC, managed effects and Runtime-owned artifacts. The
  Unix protocol is a candidate awaiting joint wire and application acceptance.
- [Meeting operations](docs/integrations/meetings.md) covers Notion upsert, Sheets
  and Calendar reads, and Gmail draft creation, retrieval and reconciliation.
  This meeting-v1 protocol has separate bindings from the generic host.

Registering a capability never grants permission to execute it. Runtime owns
admission and durable operations; ZEOconnect owns protected connector dispatch.
Legacy plugin registration now publishes transactionally and restores surviving
contributions on unload. It remains an explicit local loading API.

## Optional integrations

Optional SDKs ship as extras. HubSpot, Kit, managed environments and the Gemini
adapter are in the base package; their live operations still require configured
accounts and authorization. Install additional dependencies only as needed:

| Extra | What it adds |
|---|---|
| `zeocore[github]` | GitHub API integration |
| `zeocore[drive]` | Google Drive |
| `zeocore[gmail]` | Gmail |
| `zeocore[calendar]` | Google Calendar (read + write) |
| `zeocore[youtube]` | YouTube publishing: resumable upload, schedule, thumbnail, captions, playlists (injected credentials only) |
| `zeocore[google]` | Drive + Gmail + **Docs** auth plumbing together |
| `zeocore[bluesky]` | Bluesky posting via an app password — no OAuth, no developer app |
| `zeocore[notion]` | Notion (read + write) |
| `zeocore[supabase]` | Supabase Database, Auth, Storage, Edge Functions, and async Realtime |
| `zeocore[pandoc]` | Document conversion via Pandoc |
| `zeocore[llms]` | OpenAI / Anthropic / tiktoken clients — chat, tool-calling, prompt caching |
| `zeocore[jupytext]` | Script ↔ Jupyter notebook conversion and semantic receipts |
| `zeocore[notebook]` | Fresh-kernel execution with bounded output and cleanup |
| `zeocore[ffmpeg]` | Media probing/transcoding via the org's `ffmpeg-zeo` package |
| `zeocore[http]` | FastAPI-based HTTP adapter for exposing tools over REST |
| `zeocore[mcp]` | MCP adapter for exposing tools to Claude Code, Cursor, and other MCP-native agents |
| `zeocore[runtime-host]` | Supervised capability host and canonical wire validation |
| `zeocore[all]` | Integration extras; excludes `http`, `mcp`, `runtime-host`, `dev` and `lint` |

`runtime-host` must be installed explicitly; it is available from 0.11.0.

`mcp` and `mcp-dev` are real, separate extras — `zeocore[all]` does **not**
pull in the MCP adapter. Install it explicitly (e.g. `zeocore[all,mcp]`).
The `dev` and `lint` extras are for contributors; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## What's in the package

| Module | What it's for |
|---|---|
| `zeo_core.tools` | Authoring — `@capability`, `CapabilityRegistry`, `invoke_sync` / `invoke_async`, `BaseZeoTool`, `ToolContext`, `tool_to_capability`, optional mixins. |
| `zeo_core.execution` | Host-side bounded execution — one total deadline, explicit retries/fallback, cancellation, truthful target identity, and sanitized attempt records for read-only/advisory work. |
| `zeo_core.contracts` | Data contracts — `CapabilityId`, `CapabilityDefinition`, `CapabilityManifest`, `CapabilityResult`, `CapabilityOutcome`, guards, invocation records. See [contracts/README.md](src/zeo_core/contracts/README.md). |
| `zeo_core.adapters` | HTTP, MCP, LLM function projection, and the Runtime capability host. |
| `zeo_core.contracts.runtime` | Version-1 launch, attempt, request, effect and result bindings for the Runtime host. |
| `zeo_core.integrations.meetings` | Runtime-admitted meeting reads, Notion upsert and Gmail draft operations. |
| `zeo_core.core` | Filesystem operations, path resolution, a typed error hierarchy, MIME detection, serialization, logging, an operation registry. |
| `zeo_core.config` | YAML/env-var configuration loading and per-tool config models. |
| `zeo_core.integrations` | Adapters for GitHub, Google Workspace, Supabase, LLM providers, Notion, HubSpot, Kit, Gemini images, Pandoc, jupytext, notebooks, ffmpeg, and Bluesky; managed environments and native service profiles. Supabase covers Database, Auth, Storage, Edge Functions, and async Realtime while deliberately excluding raw SQL and Vault plaintext access. |
| `zeo_core.modules` | Plugin discovery and explicit-loading registry. |
| `zeo_core.prompt` | Prompt template selection and enhancement utilities. |
| `zeo_core.contract_pack` | Versioned consumption contract pack for ecosystem runners (no `sovereign_agent` import). |

[GET-STARTED.md](GET-STARTED.md#core-modules-overview) walks through these
module by module.

## Quality bar

- **mypy --strict**, clean across the whole source tree.
- The **full test suite** runs on every change (a handful are environment-gated
  and skip/run depending on credentials or OS behavior), with 90.00%+ coverage
  enforced as a two-decimal hard CI floor
  (`--cov-fail-under=90`) — a pull request that drops coverage fails the gate.
- **CI runs the full suite on Python 3.14** (the minimum supported
  interpreter) on every push.
- Production code is not allowed to detect that it's under test (a dedicated
  CI check fails the build if it finds `"pytest" in sys.modules` or similar).

## Project status

ZeoCore **0.11.0** is a beta library: the API is typed and tested, and this
release is the canonical capability-authoring surface for the Zero Employee
ecosystem. The surface may still shift before 1.0. Issues, questions, and API
feedback are welcome.

## Contributing

New contributors start at [CONTRIBUTING.md](CONTRIBUTING.md), which covers
dev environment setup (`make setup`), the verification gate (`make verify`),
and how to submit a change. This project follows the
[Contributor Covenant](CODE_OF_CONDUCT.md). Security reports go through
[SECURITY.md](SECURITY.md).

## Project links

[PyPI](https://pypi.org/project/zeocore/) ·
[Source](https://github.com/profrodai/zeocore) ·
[Issues](https://github.com/profrodai/zeocore/issues) ·
[Quickstart](QUICKSTART.md) ·
[Docs](docs/README.md) ·
[Manual](GET-STARTED.md) ·
[Changelog](CHANGELOG.md) ·
[Contributing](CONTRIBUTING.md) ·
[Security](SECURITY.md)

## License

MIT — see [LICENSE](LICENSE). SPDX: `MIT`.
