# ZEOconnect managed profile

For credential creation, test accounts, production accounts and environment-isolated
execution, follow the [account setup guide](../integrations/zeoconnect.md) first.
The direct-constructor examples below also work outside the managed launcher;
use the launcher when you need its separation guarantees.

ZEOconnect is ZeoCore's optional managed execution profile. Your application
declares the service and existing operation identities it needs; the same
business function can then receive a deterministic fake, a local connector, or
a hosted proxy.

Nothing contacts ZEOconnect when `zeo_core` is imported or when a service is
resolved. Network access begins only after the application explicitly starts
pairing or invokes an already paired hosted service.

## The four placements

| Profile | Execution | Credential owner |
|---|---|---|
| `fake` | Deterministic in-process adapter | No credential |
| `local` | Local ZeoCore connector | The local user |
| `hosted` | ZeoCore proxy → ZEOconnect Member API | ZEOconnect custody |
| `governed` | Application → ZEO Go port → ZEOconnect | ZEO Go authorization; ZEOconnect custody |

`governed` never falls back to `hosted`. A governed runtime is not given the
paired-device session used by member applications.

## Declare the requirement once

Use the operation identity already shared by local and hosted connectors. There
is no second `@1` alias vocabulary.

```python
from zeo_core.integrations.hosted import ServiceRequirement

drive_read = ServiceRequirement(
    service="google.drive",
    operations=("google.drive.file.download",),
)
```

The immutable connector revision and the member-protocol version provide
versioning. Applications do not supply a connector revision, organization ID,
provider URL, credential reference, Supabase identity, or authorization header.

## Resolution and operation outcomes are different

Service resolution is local and has exactly these states:

```text
READY
CONNECTION_REQUIRED
CONNECTION_SELECTION_REQUIRED
RESOURCE_SELECTION_REQUIRED
REPAIR_REQUIRED
REVOKED
UNAVAILABLE
```

An attempted operation separately returns:

```text
CONFIRMED
APPROVAL_REQUIRED
REFUSED
FAILED_SAFE
AMBIGUOUS
```

`AMBIGUOUS` means an external effect may have happened. It is never a
connection-resolution state and it ends automatic effect dispatch.

## Fake and local composition

The resolver receives implementations at the composition root. It does not
discover them over the network.

```python
from zeo_core.integrations.hosted import (
    ExecutionProfile,
    FakeGoogleDriveService,
    Ready,
    ServiceResolver,
)

fake_drive = FakeGoogleDriveService({"selected-file": b"sku,value\nA,1\n"})
runtime = ServiceResolver(
    profile=ExecutionProfile.FAKE,
    fake_services={"google.drive": fake_drive},
)
resolved = runtime.resolve(drive_read)
if isinstance(resolved, Ready):
    resolved.service.initialize()
    resolved.service.download_file("selected-file", "input.csv")
```

For `local`, pass the local `GoogleDriveService` under the same
`"google.drive"` key. The business code after `Ready` does not change.

## Hosted pairing is an explicit second action

Create the secure session store and transport at the application boundary. On
macOS, use Keychain. Tests may use the in-memory fake. A headless production
deployment must inject its own secure adapter; ZeoCore never falls back to a
plaintext file or `.env`.

```python
from zeo_core.integrations.hosted import (
    ConnectionRequired,
    KeychainSecureSessionStore,
    ZEOconnectHTTPTransport,
    build_hosted_runtime,
)

sessions = KeychainSecureSessionStore()
transport = ZEOconnectHTTPTransport(session_store=sessions)
runtime = build_hosted_runtime(transport=transport, session_store=sessions)

resolved = runtime.services.resolve(drive_read)  # local; zero network
if isinstance(resolved, ConnectionRequired):
    challenge = runtime.connections.begin_pairing(
        drive_read,
        device_name="Sovereign Agent on my Mac",
    )
    print(challenge.verification_url, challenge.user_code)
```

After the user approves in the browser, poll no faster than
`challenge.polling_interval_seconds`:

```python
from zeo_core.integrations.hosted import PairingPendingError

try:
    runtime.connections.complete_pairing(challenge)
except PairingPendingError:
    pass  # wait, then poll the same challenge
```

Completion stores the rotating device session in Keychain and installs the
sanitized connection catalogue. Resolution then returns one of:

- `READY` when exactly one eligible selected-resource connection exists;
- `CONNECTION_SELECTION_REQUIRED` when the user must choose between accounts;
- `RESOURCE_SELECTION_REQUIRED` when the Google connection exists but no
  permitted Drive file has been selected.

Only the unchanged first Drive read may be retried/resumed automatically in
this initial profile. Pairing never constitutes approval for a mutation.

## Connection selection and privacy

A connection summary exposes only an opaque `con_...` handle, provider-facing
display identity, state, operations, and selected resource summaries. It does
not expose tenant IDs, connector revisions, Supabase identifiers, Vault
references, or credentials.

ZeoCore's public Supabase integration may be used by your application for its
own database. The hosted client never connects to ZEOconnect's Supabase project:

```text
application → ZeoCore hosted client → ZEOconnect Member API → private broker
```

## Frozen member API for the first slice

The native client preserves the private service's existing route names:

```text
POST /v1/device/authorizations
POST /v1/device/token
POST /v1/device/token/refresh
GET  /v1/connections
POST /v1/operations/{operation_id}:invoke
GET  /v1/artifacts/{artifact_id}
POST /v1/device/revoke
```

Every request and response uses `ZEOconnect-Protocol-Version: 1`. Redirects are
refused. JSON requests are limited to 64 KiB, JSON responses to 1 MiB, and
artifacts to 10 MiB. The production origin is compiled as
`https://broker.connect.zeo.ac`, the tailnet-only Broker (until 0.13.0 it was
the retired `https://connect.zeroemployee.org`); only an explicit constructor
flag permits a localhost development origin. Ambient proxy variables are ignored by the
production client.

The current private ZEOconnect server must add this version header and stop
requiring caller-supplied `connector_revision` before live compatibility is
claimed. ZeoCore's isolated contract tests use a fake server; they do not claim
that the hosted product journey is already live.

## Retry and revocation law

- A safe Drive observation may retry one transport failure with the original
  request body and idempotency key.
- Effects are attempted once. A transport failure after dispatch is not retried.
- The client never invents a new idempotency key to escape a conflict.
- An expired access token may be refreshed during an explicit hosted operation;
  the rotated session replaces the previous secure-store record.
- Device revocation deletes the local session after the server acknowledges it.
- Provider-connection revocation returns `REVOKED`; it is not treated as a
  missing connection or silently repaired.

## What ZeoCore does not do

The managed client does not implement browser OAuth, provider-token custody,
membership entitlement, tenant policy, Supabase access, ZEO Go mandates, or
arbitrary HTTP proxying. Those boundaries remain respectively in ZEOconnect and
ZEO Go.
