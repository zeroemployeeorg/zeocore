# ZeoCore Documentation

<!-- Teaches CLAUDE.md Rev 17; integration setup index added 2026-09-08. -->

**Connecting a service?** Start with [integration account setup](integrations/README.md)
for exact credential screens, separate test/production accounts and E2E checks
for every supported integration.

This is the learning hub. It tells you what to read, in what order, and what
each piece is for.

**Brand new to ZeoCore?** Go straight to
[QUICKSTART.md](../QUICKSTART.md) — it installs Python 3.14, sets up a
virtual environment, and gets a capability running on your machine in about
ten minutes.

## The learning path

Work through these in order. Each step assumes the one before it.

| # | Read | Time | You'll be able to |
|---|---|---|---|
| 1 | [README.md](../README.md) | 5 min | Say what ZeoCore is, who it's for, and how a capability is shaped. |
| 2 | [QUICKSTART.md](../QUICKSTART.md) | 10 min | Install ZeoCore and run your own capability, and explain every line of it. |
| 3 | [Concepts](concepts.md) | 15 min | Understand capabilities, contracts, context, and where the runner's job begins. |
| 4 | [Capability authoring tutorial](tutorials/capability-authoring.md) | 20 min | Register capabilities, add guards, build manifests, and bind to adapters. |
| 5 | [Results and errors](tutorials/results-and-errors.md) | 20 min | Choose correctly between returning a result and raising an exception. |
| 6 | [Context, configuration, and files](tutorials/context-config-files.md) | 20 min | Wire `ToolContext`, `load_config()`, and filesystem access together. |
| 7 | [Bounded retries and explicit fallback](tutorials/resilient-execution.md) | 15 min | Put one-attempt capabilities behind a total deadline without hidden or multiplied retries. |
| 8 | [GET-STARTED.md](../GET-STARTED.md) | reference | Use paths, plugins, integrations, and adapters in depth. |
| 9 | An integration tutorial ([ZEOconnect](tutorials/zeoconnect-hosted-profile.md), [MCP](tutorials/mcp-server-with-claude-code.md), [Supabase](tutorials/supabase-integration.md), [Notion](tutorials/notion-integration.md), [Calendar](tutorials/calendar-integration.md), [Google Docs](tutorials/google-docs-integration.md), or [Bluesky](tutorials/bluesky-integration.md)) | 20 min | Connect your capability to the outside world. |

Unfamiliar term along the way? The [glossary](glossary.md) defines them in
one place.

## Tutorials

Step-by-step guides for people building on ZeoCore.

**Core**

- [Author your first capability](tutorials/capability-authoring.md) —
  `@capability`, the registry, invoking, OpenAI projection, and HTTP/MCP
  binding. **Start here after the quickstart.**
- [Results and errors](tutorials/results-and-errors.md) — the difference
  between an expected outcome and a bug, and how each is reported.
- [Context, configuration, and files](tutorials/context-config-files.md) —
  `ToolContext`, configuration loading, and filesystem access, and how they
  relate.
- [Bounded retries and explicit fallback](tutorials/resilient-execution.md) —
  one total deadline, explicit attempt plans, cancellation, and truthful
  live/simulated labels.

**Added in 0.11.0, after capability authoring**

- [Provider registration](how-to/provider-registration.md) — terminology,
  an explicit factory and the offline catalogue example.
- [Runtime host](how-to/runtime-host.md) — trusted launch context, admission,
  canonical bytes and Runtime-owned results; requires `runtime-host`.
- [Meeting operations](integrations/meetings.md) — admitted reads, Notion upsert
  and Gmail draft operations, with a request-only offline example.

**Integrations and adapters**

- [Kit marketing](tutorials/kit-marketing.md) — newsletters, sequence authoring,
  subscriber consent, tags and metrics through agent capabilities.

- [HubSpot marketing](tutorials/hubspot-marketing.md) — newsletters, campaigns,
  subscriptions and email drip sequences, with agent capabilities.

- [ZEOconnect managed profile](tutorials/zeoconnect-hosted-profile.md) — run
  one typed service under fake, local, hosted, or governed placement without
  putting provider credentials in application code.
- [MCP server with Claude Code / Cursor](tutorials/mcp-server-with-claude-code.md)
  — expose your tools to MCP-native coding agents.
- [Notion integration](tutorials/notion-integration.md) — read and write
  Notion pages and databases.
- [Supabase integration](tutorials/supabase-integration.md) — Database, Auth,
  Storage, Edge Functions, Realtime, and the Vault boundary.
- [Google Calendar integration](tutorials/calendar-integration.md) — OAuth
  setup, reading and creating events.
- [Google Docs integration](tutorials/google-docs-integration.md) — reading
  document text, creating documents, and batch edits.
- [Bluesky integration](tutorials/bluesky-integration.md) — posting, and why
  link positions are UTF-8 byte offsets.

## Runnable examples

Every script in [`examples/`](../examples/) runs as-is with
`uv run examples/<name>.py`. None are illustrative fragments. Some need an
optional extra installed (noted below); the credential-backed ones skip
gracefully when the credential isn't set, rather than crashing.

**Authoring the core surface**

- [`capability_authoring.py`](../examples/capability_authoring.py) — the
  canonical `@capability` function, registry, and `invoke_sync`.
- [`minimal_tool.py`](../examples/minimal_tool.py) — the smallest class-based
  tool: no mixins, no services, just `run()`.
- [`tool_to_capability.py`](../examples/tool_to_capability.py) — adapt a
  `BaseZeoTool` class into a `BoundCapability`.
- [`capability_guards.py`](../examples/capability_guards.py) — a
  `RequestGuard` rejecting a request before the handler body runs.
- [`toolkit_usage.py`](../examples/toolkit_usage.py) — lifecycle hooks, an
  optional integration, and a graceful skip when a service isn't wired.

**Infrastructure**

- [`config_usage.py`](../examples/config_usage.py) — `load_config()`'s three
  real behaviors, including the failure mode caught on purpose.
- [`error_handling.py`](../examples/error_handling.py) — the `ZeoError`
  family and when to raise instead of returning a result.
- [`explicit_plugin_loading_example.py`](../examples/explicit_plugin_loading_example.py)
  — discover and load plugins with no import-time side effects.

**Exposing capabilities**

- [`llm_tools_usage.py`](../examples/llm_tools_usage.py) — project a
  `CapabilityManifest` to an OpenAI function tool, or refuse cleanly.
- [`http_adapter_usage.py`](../examples/http_adapter_usage.py) — bind a
  capability into `OperationRegistry` and exercise the FastAPI app
  (`zeocore[http]`).
- [`mcp_server_usage.py`](../examples/mcp_server_usage.py) — expose a tool as
  an MCP server (`zeocore[mcp]`).

**Integrations**

- [`notion_demo.py`](../examples/notion_demo.py) — current Notion API,
  simulated by default with an explicit read-only live mode.
- [`calendar_usage.py`](../examples/calendar_usage.py) — Google Calendar
  read/write, skipped when OAuth isn't configured.
- [`jupytext_usage.py`](../examples/jupytext_usage.py) — script ↔ notebook
  round-trip.
- [`ffmpeg_usage.py`](../examples/ffmpeg_usage.py) — probe, transcode, and
  thumbnail a synthetic test video it generates itself.

## Release 0.10.0 workflows

- [Managed test and production](integrations/environments.md) and
  [`environment_usage.py`](../examples/environment_usage.py): explicit state and
  credential selection before launching an application.
- [Native service profiles](tutorials/zeoconnect-hosted-profile.md) and
  [`zeoconnect_usage.py`](../examples/zeoconnect_usage.py): an offline fake first,
  then deliberately configured local, hosted or governed placement.
- [Gemini reference images](integrations/gemini-images.md) and
  [`gemini_request.py`](../examples/gemini_request.py): key acquisition, separate
  projects, host custody, admission and reconciliation.
- [Notebook execution](integrations/notebook-execution.md) and the
  [authoring reference](integrations/authoring-reference.md): local conversion,
  execution and staging with observable receipts.
- [Release notes](../RELEASE_NOTES.md): upgrade instructions and qualification limits.

## Reference

- [API reference](reference/api.md) — the public surface, symbol by symbol,
  and which import paths are supported.
- [Concepts](concepts.md) — the model behind the API: capabilities,
  contracts, context, and the runner boundary.
- [Glossary](glossary.md) — one-line definitions of the vocabulary used
  throughout these docs.
- [GET-STARTED.md](../GET-STARTED.md) — the full manual, and the place to
  look things up once you're past the tutorials. Includes a
  [troubleshooting section](../GET-STARTED.md#troubleshooting).
- [`src/zeo_core/contracts/README.md`](../src/zeo_core/contracts/README.md) —
  the contracts kernel: identities, definitions, manifests, results.
- [`src/zeo_core/contracts/EXAMPLES.md`](../src/zeo_core/contracts/EXAMPLES.md)
  — worked contract examples.
- [llms.txt](../llms.txt) — a condensed import map for coding agents.
- [CHANGELOG.md](../CHANGELOG.md) — what changed, and which things were
  deliberately not built.

Browse the [documentation site](https://zeroemployeeorg.github.io/zeocore/) for
searchable guides and [generated API signatures](reference/generated.md).
The [public API map](reference/api.md) defines supported import paths.

## Contributing

[CONTRIBUTING.md](../CONTRIBUTING.md) covers dev environment setup
(`make setup`), the verification gate (`make verify`), and how to submit a
change. Conduct expectations are in
[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md); security reports go through
[SECURITY.md](../SECURITY.md).

## Maintainer / ecosystem reports

These are **not** end-user documentation. They record how ZeoCore relates to
Sovereign Agent and the wider Zero Employee ecosystem.

- [Capability symbol audit](reports/capability-symbol-audit.md)
- [Sovereign Agent replacement readiness](reports/sovereign-agent-capability-replacement-readiness.md)
