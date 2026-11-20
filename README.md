# ZeoCore

[Documentation](https://zeroemployeeorg.github.io/zeocore/) · [Release notes](https://github.com/zeroemployeeorg/zeocore/releases)

[![CI](https://github.com/zeroemployeeorg/zeocore/workflows/CI/badge.svg)](https://github.com/zeroemployeeorg/zeocore/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/zeocore.svg)](https://pypi.org/project/zeocore/)
[![Python versions](https://img.shields.io/pypi/pyversions/zeocore.svg)](https://pypi.org/project/zeocore/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**ZeoCore is a Python framework for writing capabilities: small, typed,
named units of work that anything can call.**

You write a function once — giving it an identity, a typed request and
response, a declaration of its side effects, and a structured result — and
that same function can then be run by a script, served over HTTP, exposed to
an MCP-native coding agent like Claude Code or Cursor, or handed to an LLM as
a callable tool. You don't rewrite it for each destination.

**Working on ZeoCore itself?** `make help` is the first-time guide, `make doctor` checks your machine (it changes nothing), `make verify` is the gate, and `make help-all` lists every target.

**New here? Start with the [Quickstart](QUICKSTART.md).** It takes you from
an empty folder to a running capability in about ten minutes, and assumes no
prior knowledge of ZeoCore.

## What you can build with it

- **Your own tools**, written once and used from a script, an HTTP service, an
  MCP server or an LLM tool call ([Quickstart](QUICKSTART.md)).
- **Integrations with real services** — GitHub, Google Workspace, Notion,
  Bluesky, YouTube, HubSpot, Kit, language models and more —
  each with a setup guide for credentials and test accounts
  ([integration guides](docs/integrations/README.md)).
- **A briefing with live cards** — flight, weather, arrival timeline and a
  daily currency watchlist — from keyless public sources, rendered as
  self-contained HTML and driven by an OpenAI managed agent
  ([briefing cards](docs/how-to/briefing-cards.md),
  [Agents API](docs/integrations/agents-api.md)).
- **The `zeocore` command**, a JSON-lines command line over the same contracts
  for callers that do not import Python ([reference](docs/reference/cli.md)).

## Who this is for

- **Students and newcomers** learning how to structure real Python tools —
  typed inputs, explicit error handling, no hidden global state.
- **Developers** building automation, content pipelines, or integrations who
  don't want to re-solve configuration, filesystem, and error handling in
  every project.
- **Teams** who want one authoring surface that scripts, HTTP services, and
  agents can all consume.

You should be comfortable writing Python functions and classes. You do *not*
need prior experience with Pydantic, MCP, or agent frameworks.

## Requirements

**Python 3.12 or newer.** That is the only hard requirement, and it is the
interpreter Google Colab provides, so ZeoCore runs there without a custom
Python.

Not sure what you have? Run `python3 --version` on macOS/Linux or
`py --version` on Windows. The [Quickstart](QUICKSTART.md#step-1-check-your-python-version)
walks through installing a current Python if you need it.

## Install

```bash
uv pip install zeocore
# or, without uv
pip install zeocore
```

The package is `zeocore`; the module you import is `zeo_core`.

ZeoCore never reads a provider key for you and never stores one. Anything that
needs a credential reads it from your process environment (for example
`OPENAI_API_KEY`), and the example scripts that need one say so at the top.
Copy [`.env.example`](.env.example) to `.env` for local use; `.env` is
git-ignored.

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
| [QUICKSTART.md](QUICKSTART.md) | Install Python 3.12, make a venv, write and run your first capability. No prior knowledge assumed. |
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

## Optional integrations

Optional SDKs ship as extras. Install only what you need:

| Extra | What it adds |
|---|---|
| `zeocore[github]` | GitHub API integration |
| `zeocore[drive]` | Google Drive |
| `zeocore[gmail]` | Gmail |
| `zeocore[calendar]` | Google Calendar (read + write) |
| `zeocore[google]` | Drive + Gmail + Docs auth plumbing together |
| `zeocore[youtube]` | YouTube publishing: resumable transfer, schedule, thumbnail, captions, playlists |
| `zeocore[bluesky]` | Bluesky posting via an app password — no OAuth, no developer app |
| `zeocore[notion]` | Notion (read + write) |
| `zeocore[pandoc]` | Document conversion via Pandoc |
| `zeocore[llms]` | OpenAI / Anthropic / tiktoken clients — chat, tool-calling, prompt caching |
| `zeocore[jupytext]` | Script ↔ Jupyter notebook conversion |
| `zeocore[notebook]` | Fresh-kernel notebook execution with bounded output |
| `zeocore[http]` | FastAPI-based HTTP adapter for exposing tools over REST |
| `zeocore[mcp]` | MCP adapter for exposing tools to Claude Code, Cursor, and other MCP-native agents |
| `zeocore[runtime-host]` | Supervised capability host and canonical wire validation |
| `zeocore[all]` | The integration extras; excludes `http`, `mcp`, `runtime-host`, `dev` and `lint` |

HubSpot, Kit and the briefing cards are in the base package; their live
operations still need your own account or provider terms. The OpenAI managed
agent wrapper needs the `openai` package (3.29 or newer), which you install
yourself. `mcp` is a separate extra: `zeocore[all]` does **not** include it
(use `zeocore[all,mcp]`). The `dev` and `lint` extras are for contributors; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## What's in the package

| Module | What it's for |
|---|---|
| `zeo_core.tools` | Authoring — `@capability`, `CapabilityRegistry`, `invoke_sync` / `invoke_async`, `BaseZeoTool`, `ToolContext`, `tool_to_capability`, optional mixins. |
| `zeo_core.contracts` | Data contracts — `CapabilityId`, `CapabilityDefinition`, `CapabilityManifest`, `CapabilityResult`, `CapabilityOutcome`, guards. |
| `zeo_core.contracts.ui` | Card and widget contracts: `DataRef`, `WidgetUpsert`, `WidgetBoard`. |
| `zeo_core.execution` | Bounded execution — one total deadline, explicit retries and fallback, cancellation, sanitized attempt records. |
| `zeo_core.adapters` | HTTP, MCP and LLM function adapters, the Runtime capability host, and `widget_html` (cards to self-contained HTML). |
| `zeo_core.integrations` | Adapters for GitHub, Google Workspace, LLM providers, Notion, HubSpot, Kit, Gemini images, Pandoc, jupytext, notebooks, ffmpeg, the OpenAI managed Agents API (`agents`) and keyless public briefing sources (`briefing_sources`). |
| `zeo_core.core` | Filesystem operations, path resolution, a typed error hierarchy, MIME detection, serialization, logging. |
| `zeo_core.config` | YAML/env-var configuration loading and per-tool config models. |
| `zeo_core.modules` | Plugin discovery and explicit-loading registry. |
| `zeo_core.prompt` | Prompt template selection and enhancement utilities. |

[GET-STARTED.md](GET-STARTED.md#core-modules-overview) walks through these
module by module.

## Quality bar

- **mypy --strict**, clean across the whole source tree.
- The **full test suite** runs after every merge to `main` and nightly, on
  Python 3.12 (the minimum supported interpreter) and 3.14 (the newest); pull
  requests run a fast smoke job first. Coverage is held to a 90% hard floor.
- Production code is not allowed to detect that it is under test (a dedicated
  CI check fails the build if it finds `"pytest" in sys.modules` or similar).

## Project status

ZeoCore is a beta library: the API is typed and tested, but the surface may
still shift before 1.0. **Every merge to `main` is a release** — see the
[GitHub releases](https://github.com/zeroemployeeorg/zeocore/releases) for what
changed in each version. Issues, questions and API feedback are welcome.

## Contributing

New contributors start at [CONTRIBUTING.md](CONTRIBUTING.md). This project
follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Security reports go
through [SECURITY.md](SECURITY.md).

## Project links

[PyPI](https://pypi.org/project/zeocore/) ·
[Source](https://github.com/zeroemployeeorg/zeocore) ·
[Issues](https://github.com/zeroemployeeorg/zeocore/issues) ·
[Quickstart](QUICKSTART.md) ·
[Docs](docs/README.md) ·
[Manual](GET-STARTED.md) ·
[Changelog](CHANGELOG.md) ·
[Contributing](CONTRIBUTING.md) ·
[Security](SECURITY.md)

## License

MIT — see [LICENSE](LICENSE). SPDX: `MIT`.
