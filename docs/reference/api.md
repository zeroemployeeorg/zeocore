# ZeoCore API reference

A curated map of ZeoCore's **public** surface: the canonical import path for
each symbol, the signatures you actually call, and where the boundary sits
between "supported API" and "internal detail you should not import".

This page is hand-written and covers ZeoCore **0.11.0** (beta — see
[Stability](#stability)). It is not generated from
docstrings, and it is not exhaustive: every entry links to the source file,
which is the authoritative signature. If a symbol is not listed here and not
in a module's `__all__`, treat it as internal.

**New to ZeoCore?** Read [README.md](../../README.md) first, then run the
offline examples in [`examples/README.md`](../../examples/README.md). Come
back here when you need to look something up.

**Added integration surfaces in 0.10.0**

| Public import | Contract and guide |
|---|---|
| `zeo_core.integrations.hubspot` | [Marketing capabilities](../tutorials/hubspot-marketing.md): email, campaigns, subscription consent and automation. |
| `zeo_core.integrations.kit` | [Marketing capabilities](../tutorials/kit-marketing.md): broadcasts, sequences, subscribers and tags. |
| `zeo_core.integrations.environments.IntegrationEnvironment` | [Managed launch](../integrations/environments.md) with distinct test/production state. |
| `zeo_core.integrations.hosted.ServiceResolver` | [Native profiles](../tutorials/zeoconnect-hosted-profile.md), explicit requirements and typed resolution results. |
| `zeo_core.integrations.gemini.ImageGenerationService` | [Reference image generation](../integrations/gemini-images.md) through admitted connections and durable effects. |
| `zeo_core.integrations.notebook.execute_notebook` | [Fresh-kernel execution](../integrations/notebook-execution.md) with bounded output and cleanup. |

For conversion contracts and filesystem staging, use the
[authoring reference](../integrations/authoring-reference.md), which links the
public request/receipt models and a complete executable consumer.

**Contents**

- [The one import rule](#import-rule)
- [Stability: what this page promises](#stability)
- [`zeo_core` — top-level authoring shortcut](#top-level)
- [`zeo_core.tools` — capability authoring](#tools)
- [`zeo_core.execution` — bounded retries and fallback](#execution)
- [`zeo_core.connections` — durable effect orchestration](#connections)
- [`zeo_core.contracts` — data contracts](#contracts)
- [Result states: status, outcome, and error codes](#result-states)
- [`zeo_core.core` — filesystem, paths, errors, registry](#core)
- [`zeo_core.config` — configuration](#config)
- [`zeo_core.modules` — plugin discovery](#modules)
- [`zeo_core.prompt` — prompt strategies](#prompt)
- [`zeo_core.adapters` — HTTP, MCP, LLM tools](#adapters)
- [`zeo_core.integrations` — external services](#integrations)
- [`zeo_core.contract_pack` — ecosystem pin](#contract-pack)
- [Optional extras](#extras)
- [Not public API](#not-public)
- [Where to go next](#next)

---

<a id="import-rule"></a>

## The one import rule

Import from the **package**, never from the module inside it.

```python
# Canonical
from zeo_core.tools import BaseZeoTool, ToolContext, capability, invoke_sync
from zeo_core.contracts import CapabilityResult, EffectKind
from zeo_core.core.fs import get_service

# Not supported — these are implementation files, and they move
from zeo_core.tools.mixins.lifecycle import LifecycleMixin
from zeo_core.tools.invoke import invoke_sync
from zeo_core.core.fs._ops.read_ops import read_text
```

Every public name in this page is reachable from the package path shown in
its heading. Set `ZEO_WARN_NONCANONICAL_IMPORTS=1` to get a runtime
`FutureWarning` on some non-canonical mixin imports; the module docstrings in
[`src/zeo_core/tools/mixins/`](../../src/zeo_core/tools/mixins/) are the
reliable signal.

Tool and capability authors normally need exactly two imports:
`zeo_core.tools` and `zeo_core.contracts`. Everything else on this page is
either infrastructure you may use (`zeo_core.core.fs`, `zeo_core.config`) or
wiring a *host application* does (`zeo_core.adapters`).

<a id="stability"></a>

## Stability: what this page promises

ZeoCore 0.11.0 is a **beta** library. The API is typed and tested
(mypy `--strict` across the tree), but the surface may still shift before
1.0. Breaking changes are recorded in [CHANGELOG.md](../../CHANGELOG.md).

Three tiers, used throughout this page:

| Tier | What it means | How to recognize it |
|---|---|---|
| **Public** | Documented here, exported in the module's `__all__`, safe to import. | Listed in `__all__` of the package you import from. |
| **Documented, no `__all__`** | Real API used by ZeoCore's own adapters and shipped examples, but the module does not declare `__all__`. Treat *the names listed here* as supported, not the whole module. | Called out inline below (`zeo_core.core.registry`, `zeo_core.core.jobs`, `zeo_core.core.mime`). |
| **Internal** | Not API. May be renamed or deleted in any release, including patch releases. | Leading `_` in the path (`_internal`, `_ops`, `_dev`), or listed under [Not public API](#not-public). |

`zeo_core.core.fs` actively enforces its boundary: attribute access to
`_internal` / `_ops` raises `AttributeError` with an explanatory message
rather than returning the module.

---

<a id="top-level"></a>

## `zeo_core` — top-level authoring shortcut

Source: [`src/zeo_core/__init__.py`](../../src/zeo_core/__init__.py)

The package root re-exports a **deliberately small** slice — the names a
class-based tool's `run()` signature needs — so `import zeo_core` gives you
something usable without further exploration:

```python
from zeo_core import (
    BaseZeoTool,          # base class for class-style tools
    ToolContext,          # immutable dependency container
    CapabilityResult,     # the result envelope run() returns
    ZeoToolProtocol,      # structural protocol for tool detection
    IntegrationEnabledMixin,
    LifecycleMixin,
    ToolEnvInitializerMixin,
)

__version__  # derived from the installed distribution metadata
```

It does **not** re-export `zeo_core.config`, `zeo_core.core`,
`zeo_core.integrations`, `zeo_core.modules`, or the rest of
`zeo_core.contracts`. That is intentional namespacing, which also means
`dir(zeo_core)` and editor autocomplete will undersell the package. Use the
per-module sections below.

<a id="tools"></a>

## `zeo_core.tools` — capability authoring

Source: [`src/zeo_core/tools/`](../../src/zeo_core/tools/) ·
Tutorial: [capability authoring](../tutorials/capability-authoring.md) ·
Examples: [`capability_authoring.py`](../../examples/capability_authoring.py),
[`minimal_tool.py`](../../examples/minimal_tool.py)

The whole authoring surface lives behind one import. A **capability** is the
abstract function; a **tool** is a class implementing one; a
`CapabilityResult` is the machine-readable outcome.

### Declare a capability

```python
from zeo_core.tools import capability

def capability(
    *,
    id: str,                                  # "namespace.name@1.2.3"
    description: str,
    effects: Iterable[EffectKind],
    examples: Sequence[CapabilityExample],    # at least one is required
    error_codes: Sequence[str] | frozenset[str] = (),
    concurrency: ConcurrencyMode = ConcurrencyMode.PARALLEL_SAFE,
    resource_key_fields: tuple[str, ...] = (),
    requirements: CapabilityRequirements | None = None,
    tags: Sequence[str] | frozenset[str] = (),
    metadata: Mapping[str, JsonValue] | None = None,
    deprecation: CapabilityDeprecation | None = None,
    projection_name: str | None = None,
    guards: Sequence[RequestGuard] = (),
    register_to: object | None = None,        # a CapabilityRegistry, or nothing
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...
```

The decorated function must have the canonical signature — anything else
raises `CapabilityAuthoringError` at decoration time (import time, not call
time):

```python
def handler(request: RequestModel, ctx: ToolContext) -> CapabilityResult[ResponseModel]: ...
```

`RequestModel` and `ResponseModel` must be Pydantic `BaseModel` subclasses;
their JSON Schemas are generated from the models, not from annotations.
`*args` / untyped `**kwargs` are rejected. Decoration does **not** register
anything globally unless you pass `register_to`.

```python
bound_capability_of(fn) -> BoundCapability
```

Retrieves the `BoundCapability` the decorator attached to the function
(`fn.__zeo_capability__`). Raises `CapabilityAuthoringError` if `fn` was not
decorated.

Source: [`authoring.py`](../../src/zeo_core/tools/authoring.py)

### Invoke a capability

```python
from zeo_core.tools import invoke_sync, invoke_async, BoundCapability

invoke_sync(capability: BoundCapability, request: BaseModel, ctx: ToolContext) -> CapabilityResult[Any]
await invoke_async(capability: BoundCapability, request: BaseModel, ctx: ToolContext) -> CapabilityResult[Any]
```

Both run the same pipeline and **never raise** for handler failures — they
convert everything into a `CapabilityResult`:

1. cancellation check (via the optional `"cancellation"` service),
2. request validation against `capability.request_model`,
3. guards (`RequestGuard.check`),
4. availability (declared `requirements` present in `ctx`),
5. the handler,
6. return-value normalization.

Calling `invoke_sync` on an `async def` capability returns an
`invalid_return` error result rather than a coroutine. `BoundCapability`
itself exposes `definition`, `request_model`, `guards`, `is_async`,
`is_available(ctx)`, and `invoke(request, ctx)` (which dispatches to the
right helper).

Also public here, mostly for runners:

- `invocation_record(*, capability, request, result, ctx, invocation_id, started_at, ended_at) -> CapabilityInvocationRecord` — a redacted, digest-based audit record.
- `resource_coordination_key(capability, request) -> str | None` — deterministic lock key from `resource_key_fields`. ZeoCore computes the key; it does not take locks.

Source: [`invoke.py`](../../src/zeo_core/tools/invoke.py)

### Register capabilities

```python
from zeo_core.tools import (
    CapabilityRegistry, CapabilityProvenance, CapabilityRegistryError,
    get_capability_registry, reset_capability_registry,
)

registry = CapabilityRegistry()
registry.register(cap, provenance=None)          # duplicate identity → CapabilityRegistryError
registry.get("demo.greet@1.0.0")                 # or a CapabilityId; version optional
registry.resolve("demo.greet", version=None)     # highest matching version, or None
registry.resolve_compatible(capability_id)       # lowest same-major version >= requested
registry.list_all()                              # sorted by canonical id
registry.manifests()                             # list[CapabilityManifest]
registry.provenance_of(identity)
registry.load_entry_points(group="zeo_core.capabilities")
registry.clear()
```

Instances are the intended usage. `get_capability_registry()` returns a
process-global convenience instance (`reset_capability_registry()` clears
it) — handy in scripts and tests, never required by a runner.

Third-party packages publish capabilities through the
`zeo_core.capabilities` entry-point group, loaded explicitly with
`load_entry_points()`. Nothing is auto-loaded on import.

Source: [`registry.py`](../../src/zeo_core/tools/registry.py)

### Class-based tools

```python
from zeo_core.tools import BaseZeoTool

class MyTool(BaseZeoTool):
    name = "my_tool"          # required (class attribute or __init__ arg)
    version = "1.0.0"         # defaults to "1.0.0"
    namespace = "zeo"         # used when adapting to a capability id

    def run(self, request: MyRequest, ctx: ToolContext) -> CapabilityResult[MyResponse]: ...
```

`run()` is the only abstract method. `initialize(ctx)` and
`is_available(ctx)` are optional hooks that default to success/`True`.
Identity is frozen after `__init__`: assigning to `name` or `version` later
raises `AttributeError`, because runners may cache identity for routing.

Source: [`base.py`](../../src/zeo_core/tools/base.py) ·
Example: [`minimal_tool.py`](../../examples/minimal_tool.py)

### Adapt a class tool into a capability

```python
from zeo_core.tools import tool_to_capability, ToolAdapterError

tool_to_capability(
    tool: BaseZeoTool,
    *,
    examples: Sequence[CapabilityExample] | None = None,
    effects: Sequence[EffectKind] | None = None,
    error_codes: Sequence[str] = (),
    concurrency: ConcurrencyMode = ConcurrencyMode.PARALLEL_SAFE,
    resource_key_fields: tuple[str, ...] = (),
    requirements: CapabilityRequirements | None = None,
    guards: Sequence[RequestGuard] = (),
    description: str | None = None,
) -> BoundCapability
```

The adapter reads the tool's own `run()` type hints for the request/response
models, derives the id as `namespace.name@version` (unless the class sets
`capability_id`), and picks up optional class attributes:
`capability_examples`, `capability_description`, `capability_effects`,
`capability_error_codes`, `capability_concurrency`,
`capability_resource_key_fields`, `capability_requirements`,
`capability_tags`, `capability_guards`.

At least one example and a description are mandatory — missing either raises
`ToolAdapterError`. Invoking through the adapter runs
`initialize` → `pre_run` → `run` → `post_run`; calling `tool.run()` directly
still works and needs no definition at all.

Source: [`adapter.py`](../../src/zeo_core/tools/adapter.py) ·
Example: [`tool_to_capability.py`](../../examples/tool_to_capability.py)

### `ToolContext`

```python
from zeo_core.tools import ToolContext

ctx = ToolContext(
    run_id="run-001",
    tool_name="greet",
    tool_version="1.0.0",
    logger=logging.getLogger("greet"),   # runner-provided
    fs=get_fs_service(),                 # runner-provided
    work_dir=tmp,                        # str | Path, stored as str
    output_dir=tmp,
    services={},                         # optional: name -> service instance
    metadata={},                         # optional: must be JSON-serializable
)
```

A frozen Pydantic model. `services` and `metadata` are wrapped in
`MappingProxyType`, so the top level cannot be reassigned or mutated;
`metadata` is additionally validated as JSON-safe at construction
(`Path` → `str`, `datetime` → ISO, `Enum` → value, Pydantic models via
`model_dump()`), and a non-serializable value fails immediately.

Accessors: `work_path` / `output_path` (as `Path`), `require_logger()`,
`require_fs()`, `get_service(name)`, `require_service(name)` (raises with a
list of available services), plus `get_clock()`, `get_cancellation()`,
`get_artifact_sink()`.

Source: [`context.py`](../../src/zeo_core/tools/context.py)

### Mixins (optional)

```python
from zeo_core.tools import IntegrationEnabledMixin, LifecycleMixin, ToolEnvInitializerMixin
```

- **`IntegrationEnabledMixin`** — `get_service(name, ctx, expected_type=None)` returns `None` when the runner did not wire the service; `require_service(name, ctx, expected_type=None)` raises. A wrong `expected_type` raises `TypeError`.
- **`LifecycleMixin`** — `pre_run(request, ctx)`, `post_run(request, result, ctx)`, `validate(request, ctx)`, `cleanup(ctx)`. All default to success and all return `CapabilityResult`. `ZeoToolLifecycleMixin` is a backward-compatible alias.
- **`ToolEnvInitializerMixin`** — environment/directory setup helpers for tools that need them.

A tool with none of these is still a complete, valid tool.

Example: [`toolkit_usage.py`](../../examples/toolkit_usage.py)

### Runner-supplied services

```python
from zeo_core.tools import SystemClock, NeverCancelled, RecordingArtifactSink
```

Default implementations of the optional services `invoke_sync` /
`invoke_async` look up in `ctx.services` by name: `"clock"`,
`"cancellation"`, `"artifacts"`, `"redaction_paths"`. When absent, invoke
falls back to `SystemClock()` and `NeverCancelled()`.
`RecordingArtifactSink` collects `ArtifactRef`s in memory for a runner to
persist afterwards.

Source: [`services.py`](../../src/zeo_core/tools/services.py)

### Protocols, builders, and errors

| Symbol | Purpose |
|---|---|
| `ZeoCapability` | Runtime-checkable protocol: `definition`, `invoke()`, `is_available()`. Inheritance not required. |
| `ZeoToolProtocol` | Structural protocol for detecting class tools. |
| `build_definition(...)` | Build a `CapabilityDefinition` from Pydantic models directly (what `@capability` calls internally). |
| `coordination_key(request, fields)` | Deterministic `"a\|b"` key from dotted request fields. |
| `register_capability_operation(...)` | Bind a `BoundCapability` into an `OperationRegistry` — see [adapters](#adapters). |
| `CapabilityAuthoringError` | Invalid `@capability` signature or contract (subclasses `TypeError`). |
| `ToolAdapterError` | A `BaseZeoTool` cannot be adapted (subclasses `TypeError`). |
| `CapabilityRegistryError` | Registry contract violation (subclasses `ValueError`). |

<a id="execution"></a>

## `zeo_core.execution` — bounded retries and fallback

Source: [`src/zeo_core/execution/`](../../src/zeo_core/execution/) ·
Tutorial: [bounded retries and explicit fallback](../tutorials/resilient-execution.md)

The public execution package wraps one-attempt callbacks with an immutable
policy. Its principal entry points are:

```python
from zeo_core.execution import (
    AsyncExecutionTarget,
    AttemptContext,
    AttemptError,
    ExecutionPolicy,
    SubprocessInvocation,
    SyncExecutionTarget,
    async_capability_target,
    run_async,
    run_sync,
    sync_capability_target,
    subprocess_target,
)
```

`ExecutionPolicy` declares one total timeout, one per-attempt ceiling, the
complete ordered target plan, bounded backoff and optional jitter, retryable
classifications, and whether explicit simulation is allowed. Its default
retry classification does not create retries: one target ID still means one
attempt.

`run_sync` and `run_async` return `ResilientExecutionResult`, including an
immutable record for every started attempt and the identity and execution mode
of the target that actually succeeded. They do not retain provider exception
text. The async runner enforces each timeout; synchronous callbacks must apply
the supplied `AttemptContext.timeout_seconds` to their blocking I/O.

`subprocess_target` supplies that hard boundary for shell-free child
processes. It sends request bytes over stdin, starts an isolated process group,
terminates the group on timeout or cancellation, defaults to an empty child
environment, and admits only parsed typed stdout as a result. Raw child output
and errors never enter attempt records.

`sync_capability_target` and `async_capability_target` adapt exactly-`READ`
`BoundCapability` values. Effectful execution is currently refused because it
requires persisted dispatch and reconciliation rather than an in-memory retry.

`LLMClient.chat_once()` and `llm_chat_target` expose existing OpenAI,
Anthropic, Ollama, and mock clients as one-attempt leaves so this policy, rather
than a nested provider loop, owns retry and fallback. The adapter trusts only
structured status codes for failure classification; use `subprocess_target`
when the call also needs a hard process deadline.

<a id="connections"></a>

## `zeo_core.connections` — durable effect orchestration

Source: [`src/zeo_core/connections/`](../../src/zeo_core/connections/) ·
Tutorial: [durable effect orchestration](../tutorials/resilient-execution.md#effectful-calls-use-durable-orchestration)

```python
from zeo_core.connections import (
    ConnectorAdmissionError,
    EffectDispatchResult,
    EffectOrchestrator,
    ExactAuthorizationVerifier,
    KeychainEffectDispatcher,
    KeychainSecretStore,
    ReconciliationResult,
    SQLiteConnectionStore,
    validate_connector_revision,
)
```

`SQLiteConnectionStore` is the organization-scoped durable implementation for
connections, immutable connector revisions, execution history, append-only
receipts, sanitized confirmation evidence, and authorization replay nonces.
`ExactAuthorizationVerifier` refuses absent, mismatched, expired, replayed,
wrong-audience, untrusted-issuer, or cryptographically unverifiable effect
authorizations without reconstructing organizational policy. Construction
requires an `AuthorizationSignatureVerifier`, an expected audience, and a
non-empty issuer allowlist; there is no permissive default verifier.

`EffectOrchestrator.execute(...)` accepts trusted organization, connection,
revision, and operation identities separately from the authorization. It
persists `DISPATCH_STARTED` before invoking an `EffectDispatcher`. A provider
exception after that point records `AMBIGUOUS` and invokes the supplied
`EffectReconciler` without redispatch. Direct and reconciled success require a
positive confirmation digest; state, receipt, and confirmation evidence commit
atomically.

`validate_connector_revision` and the related connection/request validators
keep execution business-operation-shaped: provider origins and paths come from
the immutable revision, request schemas are closed, and caller-selected
transport fields are refused. `KeychainSecretStore` keeps credential material
in macOS Keychain; `KeychainEffectDispatcher` consumes one expiring lease and
exposes material only inside the provider callback.

`EffectDispatchResult` and `ReconciliationResult` reject raw provider detail.
They admit only closed dispositions, normalized errors, and lowercase SHA-256
digests. Provider-specific dispatch and reconciliation implementations are host
wiring, not permissive defaults supplied by ZeoCore.

<a id="contracts"></a>

## `zeo_core.contracts` — data contracts

Source: [`src/zeo_core/contracts/`](../../src/zeo_core/contracts/) ·
Deeper: [contracts/README.md](../../src/zeo_core/contracts/README.md),
[contracts/EXAMPLES.md](../../src/zeo_core/contracts/EXAMPLES.md)

The versionable kernel: no business logic, no side effects. Everything below
is a frozen or strictly-validated Pydantic model.

### The result envelope

```python
from zeo_core.contracts import CapabilityResult

CapabilityResult.ok(data, msg="Success", metadata=None, logs=None, duration_sec=None, run_id=None)
CapabilityResult.skip(reason, code, metadata=None, run_id=None)
CapabilityResult.unavailable(reason, code="ZEO_CAP_UNAVAILABLE", metadata=None, run_id=None)
CapabilityResult.fail(msg, code, exception=None, metadata=None, logs=None, run_id=None,
                      outcome=CapabilityOutcome.integration_failure)
CapabilityResult.fail_from_exc(msg, code, exc, metadata=None, run_id=None)
```

Fields: `status`, `outcome`, `data`, `run_id`, `timestamp`, `duration_sec`,
`human_message`, `machine_message`, `error`, `logs`, `metadata`.
Prefer the constructors above over building one by hand — see
[result states](#result-states) for the invariants they enforce.

`CapabilityError` (structured `code` / `message` / `details`) and
`CapabilityLogEvent` are the other two envelope types.

### Identity, definition, manifest

```python
from zeo_core.contracts import CapabilityId, CapabilityDefinition, CapabilityManifest

CapabilityId.parse("google.calendar.event.create@1.0.0")   # or CapabilityId(namespace=..., name=..., version=...)
cap_id.canonical()                                         # "namespace.name@1.2.3"

CapabilityManifest.from_definition(cap.definition)         # provider-neutral discovery document
```

Identity is `namespace.name@semver`: dotted lowercase namespace segments, a
single lowercase name, a real semantic version. Anything else raises at
construction. A `CapabilityManifest` carries `schema_version`, `id`,
`description`, `request_schema`, `response_schema`, `examples`,
`error_codes`, `effects`, `requirements`, `tags`, `metadata`,
`deprecation`, and `projection_name`.

Declaration models: `CapabilityExample` (`request`, optional `response`,
`name`, `description`), `CapabilityEffects` (`kinds`, `concurrency`,
`resource_key_fields`), `CapabilityRequirements` (`services`,
`credentials`, `binaries`, `network`, `filesystem`), `NetworkRequirement`,
`FilesystemRequirement`, `CapabilityDeprecation`, and
`schemas_from_models(request_model, response_model)`.

Effects are a **declaration for inspection, not a permission grant** —
authorization lives outside ZeoCore.

### Guards

```python
from zeo_core.contracts import RequestGuard, GuardResult, GuardIssue

class NonEmptyNameGuard:                      # structural: no base class needed
    def check(self, request: BaseModel) -> GuardResult:
        if not request.name.strip():
            return GuardResult.reject("name must not be blank",
                                      issues=(GuardIssue(path="name", message="blank"),))
        return GuardResult.accept()
```

Pydantic validates *shape*; a guard is a side-effect-free *policy* check over
an already-validated request. A rejection short-circuits before the handler
body runs and produces `CapabilityOutcome.guard_rejected`.
`GuardResult.reject()` defaults to code `ZEO_CAP_GUARD_REJECTED`.

Example: [`capability_guards.py`](../../examples/capability_guards.py)

### Artifacts and run manifests

`ArtifactRef`, `StorageRef`, `Checksum`, `ToolInfo`, `Provenance`,
`ManifestInput`, `RunManifest`. Tools *describe* artifacts; the runner
creates the `RunManifest` and owns persistence.

### Enums, ids, time, versions

| Group | Symbols |
|---|---|
| Status | `CapabilityStatus`, `CapabilityOutcome` |
| Declarations | `EffectKind`, `ConcurrencyMode` |
| Artifacts | `ArtifactKind`, `StorageScheme`, `ChecksumAlgorithm` |
| Logging | `LogLevel` |
| Ids | `generate_run_id()`, `generate_artifact_id()`, `generate_invocation_id()`, `is_valid_uuid()` |
| Time | `utcnow()`, `utcnow_iso()` |
| Versions | `CONTRACTS_VERSION`, `MANIFEST_VERSION`, `ARTIFACT_SCHEMA_VERSION`, `ENVELOPE_VERSION`, `CAPABILITY_MANIFEST_SCHEMA_VERSION` |

`EffectKind`: `READ`, `WRITE`, `DELETE`, `EXTERNAL_COMMUNICATION`,
`FINANCIAL`, `SECURITY_SENSITIVE`.
`ConcurrencyMode`: `PARALLEL_SAFE`, `SERIAL_PER_CAPABILITY`,
`SERIAL_PER_RESOURCE`, `EXCLUSIVE`.

`EchoRequest` and `VideoRefRequest` are exported demo **models**; the demo
implementations behind them are internal.

---

<a id="result-states"></a>

## Result states: status, outcome, and error codes

Every capability returns one envelope, and callers branch on it instead of
catching a matrix of exceptions.

`CapabilityStatus` is the three-way branch orchestrators use.
`CapabilityOutcome` refines it. The mapping is enforced: constructing a
result whose outcome contradicts its status raises `ValueError`.

| Constructor | `status` | `outcome` | Typical use |
|---|---|---|---|
| `.ok(data=...)` | `success` | `success` | The capability did its job. |
| `.skip(reason, code)` | `skipped` | `policy_skipped` | A deliberate policy decision (input too short, already done). |
| `.unavailable(reason)` | `skipped` | `unavailable` | A declared dependency was not wired in. |
| `.fail(msg, code)` | `error` | `integration_failure` (default) | An expected downstream failure. |
| `.fail_from_exc(msg, code, exc)` | `error` | `unexpected_exception` | An exception you caught and are reporting. |

Three more outcomes are produced by the invoke pipeline rather than by you:
`guard_rejected`, `invalid_return`, and `cancelled`. Human approval and
externally imposed timeouts are deliberately **not** result states.

Field invariants (validated on construction):

- `status=error` requires both `error` and `machine_message`.
- `status=skipped` requires `machine_message` and forbids `error` — skips are decisions, not failures.
- `status=success` forbids both — success is the default path and needs no routing code.
- `machine_message` must start with `ZEO_` (current convention), `ZC_` (short alias), or `QC_` (accepted for pre-rename orchestrators). Use `ZEO_<AREA>_<DETAIL>`, e.g. `ZEO_VAL_TOO_SHORT`.

Codes the invoke pipeline emits on your behalf:

| Code | Outcome | When |
|---|---|---|
| `ZEO_CAP_GUARD_REJECTED` | `guard_rejected` | A guard rejected the request, or request validation failed. |
| `ZEO_CAP_UNAVAILABLE` | `unavailable` | Declared services / `fs` missing from `ctx`; the message names what is missing. |
| `ZEO_CAP_INVALID_RETURN` | `invalid_return` | The handler returned something other than `CapabilityResult`, or an async capability was called with `invoke_sync`. |
| `ZEO_CAP_UNEXPECTED` | `unexpected_exception` | The handler raised. |
| `ZEO_CAP_CANCELLED` | `cancelled` | The injected cancellation token reported cancellation. |

Exceptions still exist for genuinely exceptional conditions — that is what
the `ZeoError` family in [`zeo_core.core.errors`](#errors) is for.
Reach for a result when the *tool* expects the condition, and for an
exception when the *caller* would not.

Example: [`capability_guards.py`](../../examples/capability_guards.py) prints
each of `outcome`, `machine_message`, and `data` for an accepted and a
rejected request.

---

<a id="core"></a>

## `zeo_core.core` — filesystem, paths, errors, registry

`zeo_core.core` itself exports nothing: always import the subpackage
(`zeo_core.core.fs`, not `zeo_core.core`).

### `zeo_core.core.fs` — filesystem service

```python
from zeo_core.core.fs import get_service, create_service, FileSystemService

fs = get_service()                                  # cached singleton, base_dir = CWD
fs = create_service(base_dir=None, log_level=..., unsafe_allow_absolute_paths=False)

result = fs.read_text("notes.md")
if result.ok:
    print(result.content)
```

**Sandboxing:** by default the service refuses paths outside its `base_dir`
(the current working directory). `unsafe_allow_absolute_paths=True` relaxes
that trust boundary while still blocking `..` escapes — this is why some
examples write scratch files under the repo instead of `/tmp`.

Operation groups on `FileSystemService`: reads (`read_text`, `read_bytes`,
`read_lines`, `read_json`, `read_yaml`), writes (`write_text`,
`write_bytes`, `write_lines`, `write_json`, `write_yaml`, `atomic_write`),
directories (`create_directory`, `ensure_dir`, `list_dir`, `list_directory`),
lifecycle (`copy`, `move`, `delete`, `create_temp_file`,
`create_temp_directory`), inspection (`exists`, `is_file`, `is_dir`,
`get_file_info`, `get_file_size_str`, `get_mime_type`, `stat`), search
(`find_files`, `find_files_by_content`), paths (`resolve_path`,
`normalize_path`, `join_path`, `split_path`, `is_safe_path`,
`is_subdirectory`, `get_unique_filename`), and integrity (`compute_checksum`,
`hash_file`).

Result models — `OperationResult`, `BoolResult`, `ReadResult`,
`WriteResult`, `FileInfoResult`, `DirectoryInfoResult`, `FindResult`,
`DataResult`, `PathResult`, `ErrorInfo` — all carry `ok`, `path`,
`message`, `error_info`, `meta`. **`ok` is canonical**; `.success` is a
deprecated alias kept for existing callers, and `.error_info` supersedes the
legacy `.error` string.

Source: [`src/zeo_core/core/fs/`](../../src/zeo_core/core/fs/)

### `zeo_core.core.paths` — project-aware path resolution

```python
from zeo_core.core.paths import get_path_service

paths = get_path_service()
root = paths.get_project_root()                     # PathResult(success, path, error)
cfg  = paths.resolve_project_path("config/settings.yaml")
ctx  = paths.detect_project_context()               # ContextResult(success, context, error)
```

Public: `PathService`, `PathResolver`, `get_path_service()`, models
`ProjectContext`, `ContentContext`, `ProjectDirectory`, and results
`PathResult`, `StringResult`, `ContextResult` (these use `.success`, unlike
the `fs` results above). Other methods: `resolve_relative_to_project`,
`get_relative_path`, `find_nearest_directory`, `get_known_directory`,
`get_module_path`, `infer_module_from_path`, `detect_content_context`.

This package deliberately does **not** expose low-level join/split — use
`zeo_core.core.fs` for filesystem primitives.

Source: [`src/zeo_core/core/paths/`](../../src/zeo_core/core/paths/)

<a id="errors"></a>

### `zeo_core.core.errors` — typed exceptions

```python
from zeo_core.core.errors import ZeoError, ZeoFileNotFoundError, wrap_io_errors
```

One root (`ZeoError`) with a structured `.context` dict, so you catch types
instead of parsing strings:

- I/O: `ZeoIOError`, `ZeoFileNotFoundError`, `ZeoFileExistsError`, `ZeoPermissionError`
- Data: `ZeoValidationError`, `ZeoFormatError`
- Setup: `ZeoConfigurationError`, `ZeoPluginError`
- Auth / integrations: `ZeoBaseAuthError`, `ZeoAuthenticationError`, `ZeoIntegrationError`, `ZeoApiError`, `ZeoQuotaExceededError`

`@wrap_io_errors` converts unhandled builtins (`OSError`, `ValueError`, …)
raised inside the decorated function into the matching `ZeoError` subclass,
so callers only handle one family.

Source: [`src/zeo_core/core/errors/`](../../src/zeo_core/core/errors/) ·
Example: [`error_handling.py`](../../examples/error_handling.py)

### `zeo_core.core.registry` — operation registry

*Documented, no `__all__`.* This is the shared surface both adapters read.

```python
from zeo_core.core.registry import OperationRegistry, Operation, get_registry, reset_registry, invoke_operation

ops = OperationRegistry()
ops.register(name, callable_, request_model, response_model=None, description="", tags=None)
ops.get(name) / ops.get_or_error(name) / ops.has_operation(name)
ops.list_operations(tags=None) / ops.unregister(name) / ops.clear()
```

Registering the same name twice raises `ValueError`. Prefer
`zeo_core.tools.register_capability_operation()` over calling `register()` by
hand: it reuses the capability's own request model instead of a second
hand-written schema.

Source: [`registry.py`](../../src/zeo_core/core/registry.py)

### `zeo_core.core.jobs` — async job execution

*Documented, no `__all__`.* Backing store and runner for the HTTP adapter's
`/jobs` endpoints: `JobStatus`, `JobData`, the `JobStore` / `JobRunner`
abstract bases, and the concrete `InMemoryJobStore` and
`ThreadPoolJobRunner`.

Source: [`jobs.py`](../../src/zeo_core/core/jobs.py) ·
Example: [`http_adapter_usage.py`](../../examples/http_adapter_usage.py)

### Other `zeo_core.core` modules

- **`zeo_core.core.logging`** — `get_logger`, `configure_logger`, `LOG_LEVELS`, `LogLevel` (declared in `__all__`).
- **`zeo_core.core.mime`** *(documented, no `__all__`)* — `get_content_type`, `is_binary_extension`, `is_text_extension`.
- **`zeo_core.core.serialization`** *(documented, no `__all__`)* — `normalize_for_json`, the JSON-safety check `ToolContext.metadata` uses.

<a id="config"></a>

## `zeo_core.config` — configuration

```python
from zeo_core.config import load_config, ZeoConfig

config = load_config()                        # default locations; falls back to defaults + env
config = load_config("zeo_config.yaml")       # explicit path; raises if it does not exist
config.general.project_name
config.logging.level
```

Public: `load_config(config_path=None, merge_env=True, merge_defaults=True)`,
`merge_configs(base, override)`, `get_env`, `get_config_value`,
`validate_required_config`, models `ZeoConfig`, `GeneralConfig`,
`LoggingConfig`, `PathsConfig`, `PluginsConfig`, plus the legacy globals
`get_config()` and the `config` proxy (discouraged in new code — pass config
explicitly).

The two `load_config()` forms behave differently on purpose: the
no-argument form never raises (it merges built-in defaults with environment
variables), while an explicit path is a promise the file exists and raises
`ZeoConfigurationError` if it does not. Default locations searched:
`./zeo_config.yaml`, `./config/zeo_config.yaml`, `~/.zeo/config.yaml`,
`/etc/zeo/config.yaml`, plus the same two file names under the detected
project root.

Importing this package performs **no** I/O.

`zeo_core.config.tooling` is a separate declared surface for per-tool
config and logging: `ZeoToolConfigModel`, `load_tool_config`,
`update_tool_config`, `setup_tool_logging`, `get_logger`.

Secrets belong in `.env` (gitignored), not in committed YAML — see
[`.env.example`](../../.env.example) and GET-STARTED's
[Secrets and `.env`](../../GET-STARTED.md#secrets-and-env).

Source: [`src/zeo_core/config/`](../../src/zeo_core/config/) ·
Example: [`config_usage.py`](../../examples/config_usage.py)

<a id="modules"></a>

## `zeo_core.modules` — plugin discovery

```python
from zeo_core.modules import list_available_entry_points, load_enabled_entry_points, registry

available = list_available_entry_points()      # lists WITHOUT instantiating anything
result = load_enabled_entry_points(enabled=["fs", "paths", "config"],
                                   strict=True, auto_register=True)
result.success, result.loaded, result.errors, result.warnings
plugin = registry.get_plugin("fs")
```

Importing `zeo_core.modules` has **no side effects** — nothing is discovered,
loaded, or registered until you ask. `strict=True` is all-or-nothing: one
unknown id means nothing loads. `strict=False` loads what it can and reports
the rest in `warnings`.

Public: `PluginRegistry`, `PluginLoader`, the protocols
(`PluginRegistryProtocol`, `PluginLoaderProtocol`, `ZeoPluginProtocol`,
`CommandPluginProtocol`, `WorkflowPluginProtocol`, `ExtensionPluginProtocol`,
`ProviderPluginProtocol`, `ConfigurablePluginProtocol`), the models
(`ZeoPluginMetadata`, `PluginEntryPoint`, `LoadResult`), the loading
functions (`list_available_entry_points`, `load_enabled_entry_points`,
`load_enabled_modules`), and the globals `registry` and `loader`.

From 0.11.0, registration publishes only after callbacks succeed. Unloading
restores the latest surviving contribution using registration snapshots. Selected
duplicate entry-point names refuse before import. This legacy registry does not
admit Runtime providers; see [provider registration](../how-to/provider-registration.md).

Built-in plugin ids (entry-point group `zeo_core.modules`): `fs`, `paths`,
`config`, `prompt`.

Source: [`src/zeo_core/modules/`](../../src/zeo_core/modules/) ·
Example: [`explicit_plugin_loading_example.py`](../../examples/explicit_plugin_loading_example.py)

<a id="prompt"></a>

## `zeo_core.prompt` — prompt strategies

```python
from zeo_core.prompt import PromptService, create_default_prompt_service

service = create_default_prompt_service()      # internal strategies pre-loaded
```

Public: `PromptService`, `create_default_prompt_service()`, models
`PromptStrategy`, `StrategyInfo`, and results `PromptRenderResult`,
`StrategyListResult`, `GetStrategyResult`, `RegisterStrategyResult`,
`LoadPackResult`.

Source: [`src/zeo_core/prompt/`](../../src/zeo_core/prompt/)

<a id="adapters"></a>

## `zeo_core.adapters` — HTTP, MCP, LLM tools and Runtime host

HTTP and MCP both read the same `OperationRegistry`, so registering once
exposes a capability to both. LLM tools project its manifest. The Runtime host
uses an explicitly admitted provider `CapabilityRegistry` and its own protocol.

```python
from zeo_core.tools import register_capability_operation

register_capability_operation(
    capability,                 # BoundCapability
    *,
    registry,                   # OperationRegistry
    context_factory,            # callable(capability) -> ToolContext
    name=None,                  # defaults to the canonical capability id
    description=None,
    tags=None,
) -> str                        # the registered operation name
```

### `zeo_core.adapters.http` — REST (`zeocore[http]`)

```python
from zeo_core.adapters.http import create_app, HttpAdapterConfig, run

app = create_app(cfg=None, registry=None, job_store=None, job_runner=None)  # -> FastAPI
run(cfg)                                                                    # uvicorn, blocking
```

`HttpAdapterConfig` fields: `host` (`"0.0.0.0"`), `port` (`8080`),
`cors_origins`, `auth_token`, `hmac_secret`, `public_base_url`,
`job_ttl_seconds` (`3600`), `max_workers` (`4`),
`request_timeout_seconds` (`900`).

Endpoints: `GET /health/live` and `GET /health/ready` (no auth), `GET /ops`,
`POST /ops/{op_name}`, `POST /jobs`, `GET /jobs/{job_id}`. Set
`auth_token=None` only for local experiments.

Without the extra installed, importing the package still succeeds but every
symbol is a stub that raises `ImportError` with an install hint when called.

Source: [`src/zeo_core/adapters/http/`](../../src/zeo_core/adapters/http/) ·
Example: [`http_adapter_usage.py`](../../examples/http_adapter_usage.py)

### `zeo_core.adapters.mcp` — Model Context Protocol (`zeocore[mcp]`)

```python
from zeo_core.adapters.mcp import register_tool, create_server, run

register_tool(tool, *, registry=None, name=None, description="", tags=None,
              work_dir=".", output_dir=".", services=None) -> str
create_server(registry=None, *, name="zeocore", version=...) -> MCPServer
run(registry=None, *, name="zeocore", version=...) -> None    # stdio transport
```

`register_tool()` derives the MCP `inputSchema` from the tool's own
`run(request, ctx)` type hint — a tool needs zero MCP-specific code. `run()`
serves over stdio, the transport Claude Code and Cursor speak by default;
for HTTP/SSE, build the server with `create_server()` and call the MCP SDK's
own transport methods.

Same stub behavior as the HTTP adapter when `zeocore[mcp]` is not installed.

Source: [`src/zeo_core/adapters/mcp/`](../../src/zeo_core/adapters/mcp/) ·
Tutorial: [MCP server with Claude Code / Cursor](../tutorials/mcp-server-with-claude-code.md) ·
Example: [`mcp_server_usage.py`](../../examples/mcp_server_usage.py)

### `zeo_core.adapters.llm_tools` — OpenAI function projection

```python
from zeo_core.adapters.llm_tools import project_openai_tool, openai_function_name

result = project_openai_tool(manifest)     # OpenAIProjectionResult
result.ok                                  # tool present and no incompatibility
result.tool.function["name"]               # e.g. "demo_greet_v1_0_0"
result.incompatibility.reason              # typed refusal, when not ok
```

Pure, no network, no API key, no extra required. The projection **refuses**
rather than silently weakening a schema: unsupported JSON Schema keywords
(`not`, `if`/`then`/`else`, `patternProperties`, `dependentSchemas`,
`contentEncoding`) produce a `ProjectionIncompatibility` with the offending
path. `required`, `$ref`, enums, and nullability are never dropped;
returns/examples/effects are omitted because the provider format has nowhere
to put them.

Public: `project_openai_tool`, `openai_function_name`, `OpenAIFunctionTool`,
`OpenAIProjectionResult`, `ProjectionIncompatibility`.

Source: [`src/zeo_core/adapters/llm_tools/`](../../src/zeo_core/adapters/llm_tools/) ·
Example: [`llm_tools_usage.py`](../../examples/llm_tools_usage.py)

### Runtime host — added in 0.11.0, `zeocore[runtime-host]`

Use `zeo-capability` for inherited-FD discovery and supervised invocation. Python
composition uses `zeo_core.adapters.runtime_host.host.ManagedHost` and
`parse_result`, with `RuntimeChannel` from `adapters.runtime_host.channel`.
Wire models live in `zeo_core.contracts.runtime`: `ProviderBinding`,
`AttemptBinding`, `LaunchContext`, `InvocationRequest`, `EffectRequest`,
`RuntimeReply` and `HostResult`.

The executable [schemas and canonical vectors](../../contracts/runtime-host-v1/)
and [protocol guide](../how-to/runtime-host.md) define this candidate surface.
It ships in 0.11.0; joint Runtime/ZEOconnect interoperability acceptance is still required.
Do not treat it as an unauthenticated replacement for direct Python invocation.

<a id="integrations"></a>

## `zeo_core.integrations` — external services

Import each integration from its own leaf package. Some ship in the base
installation; others require the extra shown below. The parent package
`zeo_core.integrations` exports nothing; import the leaf.

| Import | Extra | Public names (abridged) |
|---|---|---|
| `zeo_core.integrations.github` | `github` | `GitHubIntegration`, `GitHubClient`, `GitHubAuthProvider`, `GitHubConfigProvider`, `GitHubIntegrationProtocol`, `GitHubRepo`, `GitHubUser`, `PullRequest`, `create_integration` |
| `zeo_core.integrations.google.drive` | `drive` / `google` | `GoogleDriveService`, `DriveFile`, `DriveFolder`, `create_integration` |
| `zeo_core.integrations.google.mail` | `gmail` / `google` | `GoogleMailService`, `create_integration` |
| `zeo_core.integrations.google.calendar` | `calendar` / `google` | `GoogleCalendarService`, `Calendar`, `CalendarEvent`, `EventAttendee`, `EventDateTime`, `CalendarIntegrationProtocol`, `create_integration` |
| `zeo_core.integrations.meetings` | base install, from 0.11.0 | `MeetingRunner`, `MeetingAdapters`, `UnixMeetingRuntime`, `InvocationAuthorization`, `InvocationReceipt`, `request_digest` |
| `zeo_core.integrations.database.supabase` | `supabase` | `SupabaseIntegration`, `SupabaseClient`, `SupabaseRealtimeClient`; see [Supabase](../integrations/supabase.md) |
| `zeo_core.integrations.hubspot` | base install | `HubSpotIntegration`, `HubSpotClient`; [marketing guide](../tutorials/hubspot-marketing.md) |
| `zeo_core.integrations.kit` | base install | `KitIntegration`, `KitClient`; [marketing guide](../tutorials/kit-marketing.md) |
| `zeo_core.integrations.environments` | base install | `IntegrationEnvironment`; [test and production](../integrations/environments.md) |
| `zeo_core.integrations.notebook` | `notebook` | `execute_notebook`; [execution guide](../integrations/notebook-execution.md) |
| `zeo_core.integrations.hosted` | base install | `ServiceRequirement`, `ServiceResolver`, `HostedServiceRegistry`, `ExecutionProfile`, resolution states, `build_hosted_runtime`, `ZEOconnectHTTPTransport`, `KeychainSecureSessionStore`, hosted service proxies; from 0.14.0 `HostedStoppedError`, `HostedUnavailableError`, `HostedUnreachableError`, `HostedUpgradeRequiredError`, `ZEOCONNECT_CAPABILITIES_HEADER`; with contract 1.2.0, `HostedExpectedBinding`, `HostedFenceUnsupportedError`, `binding_mismatch_of` |
| `zeo_core.integrations.notion` | `notion` | `NotionIntegration`, `NotionClient`, `NotionOperation`, `NotionAPIError`, `NotionPageResult`, typed page/database/data-source/block/user models |
| `zeo_core.integrations.llms` | `llms` | `LLMClient`, `OpenAIClient`, `AnthropicClient`, `OllamaClient`, `MockLLMClient`, `FallbackLLMClient`, `LLMConfig`, `ChatMessage`, `FunctionCall` |
| `zeo_core.integrations.pandoc` | `pandoc` | `PandocIntegration`, `DocumentConverter`, `PandocConfig`, `ConversionMetrics`, `ConversionTask`, `FileInfo`, `create_integration` |
| `zeo_core.integrations.jupytext` | `jupytext` | `JupytextIntegration`, `NotebookConverter`, `JupytextConfig`, `ConversionDetails`, `NotebookInfo`, `create_integration` |
| `zeo_core.integrations.ffmpeg` | `ffmpeg` | `FFmpegIntegration`, `FFmpegConfig`, `RenderMetrics`, `create_integration` |

Shared vocabulary lives in `zeo_core.integrations.core`:
`BaseIntegrationService`, `BaseAuthProvider`, `BaseConfigProvider`, the
protocols (`IntegrationProtocol`, `StorageIntegrationProtocol`,
`AuthProviderProtocol`, `ConfigProviderProtocol`), and the results
`IntegrationResult`, `AuthResult`, `ConfigResult`, `IntegrationLoadReport`.

**Integration results are not `CapabilityResult`.** They use
`.success` / `.content` / `.error` / `.message`:

```python
result = notion.search(query="")
if result.success:
    for item in result.content or []:
        ...
```

Two import-time behaviors worth knowing, because they change how a script
fails:

- The **Google** integrations import their Google client libraries eagerly, so `import zeo_core.integrations.google.drive` raises `ImportError` without the extra.
- **Notion, jupytext, ffmpeg** import cleanly without their extra and fail later, inside `initialize()`, with a message naming the missing package.

Supabase is implemented at `zeo_core.integrations.database.supabase`, covering
Database, Auth, Storage, Edge Functions and async Realtime. Raw SQL and Vault
plaintext access are excluded. The `bigquery` and `sqlite` packages remain placeholders.

<a id="contract-pack"></a>

## `zeo_core.contract_pack` — ecosystem pin

```python
from zeo_core.contract_pack import PACK_VERSION, PACK_SCHEMA   # "1.0.0", "zeo_core.contract_pack.v1"
```

A versioned consumption contract for ecosystem runners that need to pin what
they consume without importing Sovereign Agent.

<a id="extras"></a>

## Optional extras

Declared in [`pyproject.toml`](../../pyproject.toml); `uv pip install "zeocore[name]"`.

| Extra | Adds |
|---|---|
| `http` | FastAPI HTTP adapter |
| `mcp` | MCP adapter (pinned to `mcp>=2,<3`) |
| `github` | GitHub API |
| `drive`, `gmail`, `calendar`, `google` | Google Drive / Gmail / Calendar (`google` is the shared auth plumbing) |
| `notion` | Notion read + write |
| `llms` | OpenAI / Anthropic / tiktoken |
| `pandoc` | Document conversion |
| `jupytext` | Script ↔ notebook conversion |
| `ffmpeg` | Media probing/transcoding via `ffmpeg-zeo` (needs an `ffmpeg` binary too) |
| `supabase` | Database, Auth, Storage, Edge Functions and Realtime |
| `notebook` | Fresh-kernel notebook execution |
| `bluesky` | Bluesky client |
| `runtime-host` | Supervised host from 0.11.0, JSON Schema validation and RFC 8785 |
| `all` | Integration extras; excludes `http`, `mcp` and `runtime-host`; select those explicitly |
| `dev`, `lint`, `http-dev`, `mcp-dev` | Contributor tooling — see [CONTRIBUTING.md](../../CONTRIBUTING.md) |

Python **3.14 or newer** is required.

Credentials are read from the process environment, never from committed
YAML: `NOTION_TOKEN`, `GITHUB_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
(see [`.env.example`](../../.env.example)). Google integrations use an OAuth
client-secrets JSON file instead of a single token.

<a id="not-public"></a>

## Not public API

Importing any of these couples you to internals that change without notice:

| Path | Why it is not API |
|---|---|
| `zeo_core.core.fs._internal`, `zeo_core.core.fs._ops` | Implementation of `FileSystemService`. Attribute access is actively blocked. |
| `zeo_core.core.paths._internal` | Implementation of `PathService`. `PathResolver` is re-exported from `zeo_core.core.paths` — import it from there. |
| `zeo_core.tools.mixins.*`, `zeo_core.tools.invoke`, `zeo_core.tools.authoring`, … | Implementation files behind `zeo_core.tools`. Import the package. |
| `zeo_core.tools.catalog` | **Reference** capability implementations (add, checksum, GitHub read, calendar create, pandoc). Read them for learning; do not depend on them. |
| `zeo_core.tools.compat.sovereign_style` | **Transitional** adapter for keyword-argument functions. Not the canonical authoring surface. |
| `zeo_core._dev` | Local development helpers. |
| `zeo_core.contracts.capabilities.demo` implementations | Only the demo *models* (`EchoRequest`, `VideoRefRequest`) are exported. |
| `zeo_core.integrations.database.bigquery`, `zeo_core.integrations.database.sqlite` | Placeholder packages. Supabase has a supported public API. |
| Anything with a leading underscore | Standard Python convention, enforced here. |

The commented-out media contracts in
[`contracts/__init__.py`](../../src/zeo_core/contracts/__init__.py)
(`SliceVideoRequest`, `TranscribeResponse`, …) are deliberately **not**
declared stable yet.

<a id="next"></a>

## Where to go next

- [README.md](../../README.md) — the 30-second version, install, and the module map.
- [GET-STARTED.md](../../GET-STARTED.md) — module-by-module walkthrough, including [Capabilities](../../GET-STARTED.md#capabilities) and [Core Modules Overview](../../GET-STARTED.md#core-modules-overview).
- [`examples/README.md`](../../examples/README.md) — runnable scripts, required extras and the offline beginner path.
- [Capability authoring tutorial](../tutorials/capability-authoring.md) — the worked end-to-end walkthrough.
- [contracts/README.md](../../src/zeo_core/contracts/README.md) and [contracts/EXAMPLES.md](../../src/zeo_core/contracts/EXAMPLES.md) — the contracts kernel in depth.
- [docs/README.md](../README.md) — index of tutorials and maintainer reports.
- [llms.txt](../../llms.txt) — condensed import map for coding agents.
- [CHANGELOG.md](../../CHANGELOG.md) — what changed, and what broke.
