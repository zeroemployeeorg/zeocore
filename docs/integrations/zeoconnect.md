# ZEOconnect hosted account setup

<!-- Teaches CLAUDE.md Rev 17; user documentation reviewed 2026-09-08. -->

This optional composition surface uses a paired device session rather than a
provider API key in ZeoCore. Provider OAuth and token custody belong to
ZEOconnect. Start with [test and production environments](environments.md),
then follow the [complete pairing and service-resolution tutorial](../tutorials/zeoconnect-hosted-profile.md).
The current native slice supplies Drive, Docs and Bluesky bindings; it is not
a claim that every local integration is available through the hosted service.
Live operation also requires a compatible deployed ZEOconnect Member API.

!!! warning "Hosted access is unavailable in zeocore 0.10.0 through 0.13.0"
    Every release with the hosted HTTP transport, from 0.10.0 to 0.13.0, pins
    the hosted origin `https://connect.zeroemployee.org`.
    The ZEOconnect deployment has been renamed, so the hosted profile cannot
    reach it, and pairing and hosted calls fail. **Local integrations are
    unaffected.** At the time of writing the old name has no DNS record. That
    is a present condition, not a security guarantee, so do not rely on it to
    keep a token from being sent. The origin correction and conformance to
    ZEOconnect Broker contract `1.0.0` ship together in zeocore 0.14.0.
    Tracked in org issue 791.

## The Broker origin

From 0.14.0 the transport talks to `https://broker.connect.zeo.ac`, the
ZEOconnect Broker. The Broker serves paired devices only and is reachable only
on its organisation's private network (Broker contract `1.0.0` §2). The
browser-facing site, `https://connect.zeo.ac`, is where a person approves a
pairing; the transport never calls it. From a device that is not on that
network, a hosted call fails with:

> ZEOconnect Broker `https://broker.connect.zeo.ac` cannot be reached from this
> device. This hosted profile is available only on its organisation's private
> network. zeocore will not use local credentials in its place. To use your
> own Revolut account directly, select the local profile explicitly.

zeocore never falls back to a local credential when the Broker is unreachable.

## Protocol failures and stops

Every Broker response carries exactly one `ZEOconnect-Protocol-Version: 1`
header (Broker contract `1.0.0` §3). A response without it did not come from
the Broker; it came from a proxy, the edge or the network. One with another
value, or with the header twice, is a version mismatch. Either is a **protocol
failure**, whatever its HTTP status. A protocol failure ends that operation:
zeocore does not retry it, refresh the session, poll pairing again or resume an
upload around it. It is never reported as a stop. A stop, a refusal or an
outage is recognized only on a response that carries the header. A relay 503
*with* the header is an outage, and a YouTube upload backs off and resumes. A
503 *without* it ends the upload.

On a response that does carry the header, zeocore 0.14.0 reads the status as
follows (Broker contract `1.0.0` §9).

| Answer | What zeocore reports | What a caller may conclude |
|---|---|---|
| 403 `{"code": "stopped", "control", "scope"}`, or `failed_safe` with code `STOPPED` or message `stopped:<control>:<scope>` | `HostedStoppedError`, or `stop_of(response)` | A deliberate stop. Never retried or redispatched. |
| 503 | `HostedUnavailableError` | An outage. Whether an effect happened is **unknown**. |
| `failed_safe` with code `PROVIDER_UNAVAILABLE`, such as `controls_unavailable:<control>` | `is_outage(response)` | An outage inside the Broker: no provider call was made. The outcome is stored, so the same idempotency key returns it again. Another attempt needs a new key: it is a new occurrence, sent only under the caller's own authority, never an automatic retry. |
| no response at all | `HostedUnreachableError` | The same: unknown. Only `google.drive.file.download` is tried once more. |
| 401 | one session refresh, then the same request once more | A second 401 means the device must be paired again. |
| 426 | `HostedUpgradeRequiredError` | This zeocore is too old for the Broker. |
| any other status of 400 or more | "hosted request was refused" | The Broker said no. That alone is **not** proof that an effect did not happen. |

zeocore never resends an effect after a 503 or a lost connection. Where an
outcome is unknown, find it by an authorised read, such as the YouTube
executor's lookup of a lost upload, never by sending the effect again.

## The expected-binding fence

Use this when an invocation must act on exactly the account and enrolment the
caller saw (Broker contract `1.2.0` §6a). Read a fresh listing, then fence the
request with the values from it:

```python
(summary,) = transport.list_connections(session)
request = HostedOperationRequest(
    connection_id=summary.handle.value,
    operation_id="google.gmail.messages.read_page",
    arguments={},
    idempotency_key=key,
    expect=summary.expected_binding(),
)
```

| Answer | What zeocore reports | What a caller may conclude |
|---|---|---|
| `failed_safe`, message `binding_mismatch:<field>` | `binding_mismatch_of(response)` returns the field | The connection changed. No further provider call and no effect were made. `receipt["binding"]` holds the current values. |
| `failed_safe`, `PROVIDER_UNAVAILABLE`, `binding_unavailable` | `is_outage(response)` | The binding couldn't be read. It is not a mismatch. The outcome is stored against the key. |
| 503 `binding is unavailable` | `HostedUnavailableError` | An outage, as for any 503. |
| no revision in the listing | `HostedFenceUnsupportedError`, `reason` `"no_revision"` | This Broker can't check the fence. Nothing was sent. |
| 422 to a fenced call | `HostedFenceUnsupportedError`, `reason` `"invalid_fenced_request"` | Either the Broker can't check the fence (a 1.1 Broker) or the request is invalid. The contract gives both the same `request is invalid`. zeocore holds and never resends the request without `expect`. |
| 400 `kernel connection binding changed` | `HostedConnectionChangedError` | The connection was re-enrolled with a different subject, scopes, resources or credential. Nothing was sent to the provider. Repair the connection in ZEOconnect; don't retry. |
| 400 with `code` `request_changed_under_key` (1.2.1 §6a.5) | `HostedRequestChangedError` | The key was already used for a different request: other arguments, or another `expect`. Nothing was recorded, and the key's first outcome stands. A changed request needs a new key. |
| `failed_safe`, `REQUEST_REFUSED`, `request_changed_under_key` | `request_changed_of(response)` | The same, for a read. No provider call was made. |

## Setup metadata and availability

The unreleased `zeo_core.integrations.hosted.setup_catalog` module exposes
`SETUP_CATALOGUE` and `setup_manifest(integration_id)`. Its twenty entries
separate business accounts, builder connections, model access, local tools and
product sign-in. The catalogue contains reviewed presentation data. It neither
probes an account nor supplies authorization/callback URLs. Unknown integrations
return `None`; they do not receive a generic credential form.

```python
from zeo_core.integrations.hosted.setup_catalog import setup_manifest

manifest = setup_manifest("hubspot.marketing")
assert manifest is not None
print(manifest.display_name, manifest.purpose)
for profile in manifest.profiles:
    print(profile.profile.value, profile.support.value)
```

Named operations reuse existing `ServiceRequirement` identities and capability
versions. Single-component service names such as `github` are accepted, matching
the existing capability service registry. The operation must still use the exact
service prefix. Other local SDK methods are explicitly unmapped; this metadata
does not expose them as new hosted operations. Existing hosted directions remain
`not_admitted` until their deployment and provider qualification is complete.
A local implementation entry does not mean that a binary or account is ready.

`AvailabilitySnapshot` in `zeo_core.integrations.hosted.setup` requires one fact
for each of implementation, entitlement, provider consent, account health,
resource binding, Runtime authority and reachability. Each fact carries its own
revision, observation time and expiry. Calling `evaluate(now=...)` returns every
blocker and a deterministic primary blocker. A valid connection with insufficient
provider features remains a feature problem, rather than an authentication error.
At expiry the snapshot is stale; future observations are unknown. Known revocation
remains visible even when its observation becomes stale.

`AvailabilityView.dispatch_recheck_required` is always true, including when the
blocker list is empty. The host must authenticate and admit the exact operation
again at dispatch. Fixed action identifiers select vetted UI components; they
are not network instructions or approval tokens. Preserve the initiating work
when presenting or repairing any blocker.

`HostedResourceSelection` extends the existing resource summary with connection,
provider identity/version, observation time and grant revision. Exact objects,
enumerated fixed collections, and dynamic containers have different semantics.
Future members require an explicitly dynamic selection. A folder name does not
grant recursive access. This is presentation metadata: the Broker must enforce
current organization, connection, app-access and resource ceilings independently.

## Which services the hosted profile can resolve

`ServiceResolver` builds hosted proxies only from a `HostedServiceRegistry`. The
default, `REVIEWED_HOSTED_SERVICES`, registers `google.drive` with
`google.drive.file.download` and nothing else. A registration names an
in-process proxy class, the exact operations it implements, and which of them
need a selected resource. The registry is immutable once constructed, refuses a
duplicate service identity, an operation outside its service prefix or a
different member protocol version, and resolves a requirement only when every
requested operation is registered. Nothing is discovered: there is no
import-by-name, entry-point scan or provider URL. A host that needs a different
set constructs its own registry and passes `hosted_registry=` explicitly.

A registration makes a proxy constructible. It does not make hosted enrollment
available; the setup catalogue above reports the deployed support state.

## Offline test track

No account or key is needed. Inject `FakeGoogleDriveService` and use
`ExecutionProfile.FAKE` as shown in the tutorial. Use
`InMemorySecureSessionStore` for pairing protocol fixtures. Run those scripts
with `--mode test --fixture --integration zeoconnect` so fixture state and
outputs cannot be confused with a paired live test run. Assert the known fake
bytes and resolution/operation outcomes. These tests prove client composition,
not deployed membership, browser pairing or provider delivery.

## Live test account and pairing

1. Provision a separate test identity/member in the deployed ZEOconnect
   application at [connect.zeo.ac](https://connect.zeo.ac).
   If membership or the required connector is not available, that is a live
   prerequisite; no ZeoCore API key can bypass it.
2. In that identity's connection setup, authorize a dedicated provider test
   account, using the [Google](google.md) or [Bluesky](bluesky.md) account
   separation guidance. Select only disposable test resources. The OAuth
   browser must show the test account before consent is granted.
3. In your client application construct `KeychainSecureSessionStore` on macOS,
   `ZEOconnectHTTPTransport(session_store=store)`, and
   `build_hosted_runtime(transport=transport, session_store=store)`. The
   [tutorial's pairing code](../tutorials/zeoconnect-hosted-profile.md#hosted-pairing-is-an-explicit-second-action)
   shows the exact requirement, challenge, browser approval and polling calls.
4. Launch that application explicitly in test mode:

```bash
python -m zeo_core.integrations.environments --mode test   --root "$ZEO_ENV_ROOT" --integration zeoconnect --   python /absolute/path/your_pairing_application.py
```

Approve the displayed device code in the test member's browser session. Wait at
least the challenge's polling interval before each completion attempt. Pairing
stores a rotating device session, not provider credentials. Keychain entries
are separated by mode and a hash of the environment root; a production device
session is not loaded automatically into test mode. Keep that root stable for
a deployment. No global paired session is migrated into managed mode.

For a non-macOS host, inject a secure session-store adapter supplied by your
deployment, with separate namespaces for each mode/root. The native Keychain
adapter cannot work there. The in-memory adapter loses sessions on process exit
and is intended for fixtures; no plaintext fallback is provided.

## Production account and pairing

Provision the real production member and provider connection separately. Start
the same application with `--mode production` and the same environment root:

```bash
python -m zeo_core.integrations.environments --mode production   --root "$ZEO_ENV_ROOT" --integration zeoconnect --   python /absolute/path/your_pairing_application.py
```

Pair again in the production member's browser session and select production
resources explicitly. Do not copy the test device session. If several eligible
connections exist, present the returned selection state to the user; do not
choose the first account silently. Test mode uses the same deployed API unless
an explicitly configured localhost development transport is used. It does not
turn the hosted product into a vendor sandbox.

## E2E verification and cleanup

Start with a seeded selected Drive file. Resolve its requirement, complete
pairing, select the intended connection/resource, then download and compare the
known bytes. Verify the provider account and selected-resource display in the
application. Repeat in production with its own harmless resource. For mutations
use the application's explicit approval flow; pairing alone does not approve a
post or document change. An ambiguous effect must stop automatic dispatch.

Exercise device revocation and confirm the old session can no longer perform
operations. Re-pair only in the same intended mode. For cleanup, revoke the test
device and test provider connection in their respective account controls and
remove disposable provider artifacts. On a revoked/repair-required connection,
show that state; do not substitute another account. On protocol/version errors,
check the deployed server compatibility described in the tutorial. A successful
fake-server test does not establish that the production service is compatible.
