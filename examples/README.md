# ZeoCore examples

<!-- Teaches CLAUDE.md Rev 17; reviewed 2026-09-10: Runtime host and meeting source APIs. -->

Runnable scripts and a complete authoring reference. The scripts run as-is — none are
illustrative fragments, and none require you to fill in a placeholder before
they do something.

```bash
uv run examples/<name>.py
```

Run them **from the repository root**: several write scratch files relative
to the current working directory, and `zeo_core.core.fs` sandboxes file
access to the project root by default.

This page is a catalog. If you are looking for the API these scripts use,
see [docs/reference/api.md](../docs/reference/api.md); for prose
walkthroughs, see [GET-STARTED.md](../GET-STARTED.md) and
[docs/README.md](../docs/README.md).

---

## Before you start

**Python 3.14+ and ZeoCore installed.** From a clone:

```bash
uv pip install -e .            # base install: the offline examples below
uv pip install -e ".[all]"     # integration extras; excludes HTTP, MCP and Runtime host
uv pip install -e ".[http]"    # single extras, as needed
```

**Extras.** Some scripts need an optional extra. Each one says so in its
own module docstring, and the groups below list them.

**Credentials.** Scripts that need a secret read it from the process
environment — never from a config file. Copy [`.env.example`](../.env.example)
to `.env`, fill in real values, and load it however your tooling prefers:

```bash
uv run --env-file .env examples/notion_demo.py --live-read
```

**Nothing here is destructive to your machine.** Scripts that need scratch
space use either the system temp directory or a `./tmp_*_example/` folder
that they delete on exit. The Notion demo is simulated by default and its
explicit live mode is read-only.

---

## Start here: the offline path

Eight scripts, in learning order. They need **only the base install** — no
extras, no credentials, no network. Work through them top to bottom and you
will have seen the whole authoring surface.

### 1. [`minimal_tool.py`](minimal_tool.py) — the floor

The smallest legal tool: subclass `BaseZeoTool`, implement
`run(request, ctx)`, return a `CapabilityResult`. No mixins, no services, no
optional behavior.

```bash
uv run examples/minimal_tool.py
```

Builds a `ToolContext` in a temp directory (playing the runner's role),
counts words in a sentence, prints `Words: 8, Characters: 73`.

### 2. [`capability_authoring.py`](capability_authoring.py) — the canonical surface

The same idea as a function: `@capability` with an id, description,
declared effects, and one example; then `bound_capability_of()` and
`invoke_sync()`, with a `CapabilityRegistry` in between.

```bash
uv run examples/capability_authoring.py
```

Prints `Hello, World!` — deliberately boring output for a script whose point
is the shape of the code above it.

### 3. [`capability_guards.py`](capability_guards.py) — rejecting bad requests

A `RequestGuard` is a policy check over an already-validated request.
Pydantic checks shape; a guard checks whether the request should be allowed
to run at all.

```bash
uv run examples/capability_guards.py
```

Invokes the capability twice. The good request prints its slug; the blank
one prints `guard_rejected`, `ZEO_CAP_GUARD_REJECTED`, and
`rejected has data: False` — the handler body never ran.

### 4. [`tool_to_capability.py`](tool_to_capability.py) — bridging the two styles

`tool_to_capability()` adapts a `BaseZeoTool` class into a
`BoundCapability` by reading the class's own `run()` type hints, so a class
tool gains registry, manifest, and adapter support without being rewritten.

```bash
uv run examples/tool_to_capability.py
```

Calls the same tool both ways — `tool.run()` directly and `invoke_sync()`
through the adapter — prints matching results, the canonical id
`demo.word_count@1.0.0`, and a registry hit.

### 5. [`error_handling.py`](error_handling.py) — when to raise instead

`CapabilityResult` covers expected outcomes; the `ZeoError` family covers
genuinely exceptional ones. This walks the difference with three failure
modes and one success, including the `@wrap_io_errors` decorator that
converts stray builtins into typed `ZeoError` subclasses.

```bash
uv run examples/error_handling.py
```

Prints four labeled scenarios — missing file, invalid JSON, missing required
key, valid file — each with the exception type and its structured
`.context` dict.

### 6. [`config_usage.py`](config_usage.py) — `load_config()`'s three behaviors

The one confusing thing about configuration, made explicit: calling
`load_config()` with no argument never raises, while calling it with an
explicit path that does not exist always does.

```bash
uv run examples/config_usage.py
```

Prints all three paths in order: built-in defaults with no config file
anywhere, a deliberately caught `ZeoConfigurationError`, and real values
loaded from a YAML file the script writes first.

### 7. [`explicit_plugin_loading_example.py`](explicit_plugin_loading_example.py) — discovery without side effects

Importing `zeo_core.modules` loads nothing. This shows discovery (listing
without instantiating), explicit loading, and how `strict=True` differs from
`strict=False` when an id is misspelled.

```bash
uv run examples/explicit_plugin_loading_example.py
```

Lists the four built-in entry points (`config`, `fs`, `paths`, `prompt`),
confirms the registry is still empty after discovery, loads three plugins,
then shows strict mode refusing everything on a typo versus non-strict mode
loading what it can and reporting a warning. A few plugin-registration log
lines are printed to stderr along the way; they are expected.

### 8. [`llm_tools_usage.py`](llm_tools_usage.py) — projecting to an LLM tool list

`CapabilityManifest.from_definition()` plus `project_openai_tool()` turns a
capability into an OpenAI function tool. No API key, no network, no extra —
this is pure schema work.

```bash
uv run examples/llm_tools_usage.py
```

Prints the projected function name (`demo_greet_v1_0_0`), description, and
parameters — then feeds it a schema containing `not` and prints the typed
refusal, because the projection refuses unsupported keywords instead of
silently dropping them.

> `make test-docs` smoke-tests an allowlist of these credential-free
> examples against their exact expected stdout, so the offline path is
> guaranteed to keep working.

---

## New in 0.10.0: environments, profiles and authoring

These base-install examples run offline and need no key:

| Run from the checkout root | Observable result |
|---|---|
| `python examples/environment_usage.py` | Distinct private test/production state; an ambient Kit key is excluded. |
| `python examples/zeoconnect_usage.py` | A typed service requirement resolves under the explicit fake profile and downloads exact fixture bytes. |
| `python examples/gemini_request.py` | Private reference bytes are verified and a Gemini request is constructed; no authorization or generation occurs. |
| `python examples/notion_governed_upsert.py` | A governed upsert request binds its marker and source without dispatching. |

For conversion, fresh-kernel notebook execution and staging, follow the
[authoring reference](../docs/integrations/authoring-reference.md). It requires
`zeocore[jupytext,notebook,pandoc]` plus the Pandoc binary. The examples are
checkout assets, not installed shell commands. Run the release's examples with
its matching installed package. Staging produces receipts; it does not publish.

## New in 0.11.0: provider and meeting requests

These examples are checkout assets. Use the `v0.11.0` checkout and install
the matching package:

```bash
uv pip install "zeocore[runtime-host]==0.11.0"
python examples/runtime_host_catalogue_v1.py
python examples/meeting_request_v1.py
```

| Example | What it demonstrates | Requirements |
| --- | --- | --- |
| [`runtime_host_catalogue_v1.py`](runtime_host_catalogue_v1.py) | A real provider factory, manifest inventory, default-materialized input and RFC 8785 request digest | 0.11.0 plus `runtime-host` extra |
| [`meeting_request_v1.py`](meeting_request_v1.py) | Exact Sheets resource and request digest; changing the range changes the binding | 0.11.0 base install |

Both are preparation-only: no Runtime session, credentials or provider calls.
The meeting digest is its existing sorted-JSON protocol; the generic host uses
RFC 8785 with a `sha256:` prefix. Do not substitute one for the other.

Use the [registration guide](../docs/how-to/provider-registration.md),
[host protocol](../docs/how-to/runtime-host.md) and
[meeting guide](../docs/integrations/meetings.md) to move from these examples to
an admitted caller. The host CLI requires trusted inherited descriptors; running
`zeo-capability invoke` alone cannot authorize work.

## Adapters and mixins

Intermediate. Each needs one extra, and each is about *hosting* capabilities
rather than writing them.

### [`toolkit_usage.py`](toolkit_usage.py) — mixins and graceful skip

**Extra:** `zeocore[drive]` (or `[google]`, or `[all]`) — the script imports
`GoogleDriveService` at module level, so without the extra it fails on
import rather than skipping.
**Credentials:** none. It never contacts Google.

Layers `IntegrationEnabledMixin` (service lookup from `ctx.services`) and
`LifecycleMixin` (`pre_run` / `post_run`) onto a tool that reads JSON,
computes statistics, and *optionally* uploads to Drive.

```bash
uv run examples/toolkit_usage.py
```

Requests the upload with no `google_drive` service wired into the context, so
the tool logs the absence and continues: `Uploaded file id: None`, then
writes the processed JSON to a temp output directory. That graceful-skip
path — a missing optional service is not an error — is the lesson.

### [`http_adapter_usage.py`](http_adapter_usage.py) — the same capability over REST

**Extra:** `zeocore[http]`.
**Credentials:** none. No server is started and no port is bound.

Binds a capability into `OperationRegistry` with
`register_capability_operation()`, builds the FastAPI app exactly as a real
server would, and drives it with `TestClient`.

```bash
uv run examples/http_adapter_usage.py
```

Prints three `200` responses: `/health/live`, `/ops` listing the registered
`greet` operation, and `POST /ops/greet` returning the full serialized
`CapabilityResult`. Without the extra it prints an install hint and exits 0.

### [`mcp_server_usage.py`](mcp_server_usage.py) — the same tool over MCP

**Extra:** `zeocore[mcp]`.
**Credentials:** none. The client connects to the server object in memory —
no subprocess, no network.

`register_tool()` derives an MCP tool from an ordinary `BaseZeoTool` by
reading its `run()` type hint. The tool class here is identical to
`minimal_tool.py`'s: nothing about a tool changes to make it MCP-reachable.

```bash
uv run examples/mcp_server_usage.py
```

Registers the tool, creates the server, lists `['word_count']` through the
real MCP SDK client, calls it, and prints the JSON result. Without the extra
it raises `ImportError` with an install hint — the adapter's stub, working as
designed.

---

## Integrations

### Kit marketing

Run `python examples/kit_usage.py` for an offline draft and send demonstration.
It prints the distinct draft and send IDs and performs no live network call.
See the [Kit guide](../docs/tutorials/kit-marketing.md).

### HubSpot marketing

Run python examples/hubspot_usage.py for a credential-free demonstration of
registered draft and publish capabilities through a simulated HTTP boundary.
It performs no live send. See the [HubSpot guide](../docs/tutorials/hubspot-marketing.md).

For real accounts, use the [setup index](../docs/integrations/README.md) first:
choose separate test and production accounts, obtain credentials, and follow
that provider's bounded E2E check. Installing an extra alone does not authorize
an external write. The examples below document their own preconditions.

### [`jupytext_usage.py`](jupytext_usage.py) — script ↔ notebook round trip

**Extra:** `zeocore[jupytext]`.
**Credentials:** none. Fully local.

```bash
uv run examples/jupytext_usage.py
```

Writes a percent-format `.py` into `./tmp_jupytext_example/`, converts it to
`.ipynb`, converts it back, and confirms the round trip preserved the
function. The scratch directory is deleted on exit. Without the extra, the
integration's `initialize()` returns a failure result and the script prints
`jupytext module is not installed` instead of crashing.

### [`ffmpeg_usage.py`](ffmpeg_usage.py) — probe, transcode, thumbnail

**Extra:** `zeocore[ffmpeg]`.
**External tool:** a real `ffmpeg` / `ffprobe` on `PATH` (`brew install ffmpeg`,
`apt install ffmpeg`), or resolvable through `ffmpeg-zeo`'s own download
mechanism.

```bash
uv run examples/ffmpeg_usage.py
```

Generates a one-second synthetic test video with ffmpeg's `lavfi` source (no
media file needed), probes it, transcodes to H.264, writes a thumbnail, and
prints the integration's render metrics. Expect ffmpeg's own verbose output
on stderr mixed in — that is ffmpeg talking, not an error. Missing binary:
prints a skip message. Missing extra: `initialize()` fails with an install
hint.

### [`notion_demo.py`](notion_demo.py) — simulated or explicit live read

**Extra:** `zeocore[notion]`.
**Credentials:** `NOTION_TOKEN` — an integration token from
<https://www.notion.so/my-integrations>, **shared with at least one page**
from Notion's UI (creating the token is not enough; Notion's model requires
explicitly sharing each page or database with it).

```bash
uv run examples/notion_demo.py --simulated
uv run --env-file .env examples/notion_demo.py --live-read
```

Simulated mode exercises users, search, data sources, blocks, comments, and
pagination without network or credentials. Live-read mode searches objects
shared with the integration and performs no writes.

Deeper walkthrough: [docs/tutorials/notion-integration.md](../docs/tutorials/notion-integration.md).

### [`calendar_usage.py`](calendar_usage.py) — OAuth, read and write

**Extra:** `zeocore[calendar]` (or `[google]`) — imported at module level, so
without it the script fails on import.
**Credentials:** `ZEO_GOOGLE_CALENDAR_CLIENT_SECRETS`, pointing at an OAuth
client-secrets JSON file (Google Cloud Console → APIs & Services →
Credentials → OAuth client ID → *Desktop app*, with the Calendar API enabled
on that project). There is no single-token shortcut here, unlike Notion.

```bash
uv run examples/calendar_usage.py                     # skips, shows the call shapes
ZEO_GOOGLE_CALENDAR_CLIENT_SECRETS=/path/to/client_secrets.json \
    uv run examples/calendar_usage.py                 # live: opens a browser
```

Without the file: prints a skip notice plus the calling shapes for
`list_calendars`, `list_events`, `create_event`, `update_event`, and
`delete_event`. With it: runs the interactive OAuth consent flow in your
browser on first run (caching credentials into `./tmp_calendar_example/`),
lists calendars and upcoming events, then creates, updates, and **deletes** a
demo event dated 2099 — it cleans up after itself.

Deeper walkthrough: [docs/tutorials/calendar-integration.md](../docs/tutorials/calendar-integration.md).

---

## At a glance

| Script | Extra | Also needs | Offline |
|---|---|---|---|
| [`minimal_tool.py`](minimal_tool.py) | — | — | yes |
| [`capability_authoring.py`](capability_authoring.py) | — | — | yes |
| [`capability_guards.py`](capability_guards.py) | — | — | yes |
| [`tool_to_capability.py`](tool_to_capability.py) | — | — | yes |
| [`error_handling.py`](error_handling.py) | — | — | yes |
| [`config_usage.py`](config_usage.py) | — | — | yes |
| [`explicit_plugin_loading_example.py`](explicit_plugin_loading_example.py) | — | — | yes |
| [`llm_tools_usage.py`](llm_tools_usage.py) | — | — | yes |
| [`toolkit_usage.py`](toolkit_usage.py) | `drive` | — | yes |
| [`http_adapter_usage.py`](http_adapter_usage.py) | `http` | — | yes |
| [`mcp_server_usage.py`](mcp_server_usage.py) | `mcp` | — | yes |
| [`jupytext_usage.py`](jupytext_usage.py) | `jupytext` | — | yes |
| [`ffmpeg_usage.py`](ffmpeg_usage.py) | `ffmpeg` | `ffmpeg` binary | yes |
| [`notion_demo.py`](notion_demo.py) | `notion` | only for `--live-read` | yes |
| [`calendar_usage.py`](calendar_usage.py) | `calendar` | OAuth client-secrets JSON | skips |

"Offline: yes" means the script does its real work with no network access.
"Skips" means it runs and prints a skip notice, but needs credentials (and a
network) to do anything interesting.

## Related reading

- [docs/reference/api.md](../docs/reference/api.md) — every public symbol these scripts import.
- [Capability authoring tutorial](../docs/tutorials/capability-authoring.md) — the guided version of examples 2–4.
- [MCP server with Claude Code / Cursor](../docs/tutorials/mcp-server-with-claude-code.md) — turning `mcp_server_usage.py` into a real agent-facing server.
- [GET-STARTED.md](../GET-STARTED.md) — the full module-by-module walkthrough.
- [README.md](../README.md) — the short version.
