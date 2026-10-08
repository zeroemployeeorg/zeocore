# zeocore 0.12.0

ZeoCore 0.12.0 adds YouTube publishing for multi-gigabyte files, a local Revolut
Business profile for an individual's own account, and inert connection setup and
availability contracts. Python 3.14 or newer remains required. See
[CHANGELOG.md](CHANGELOG.md) for the complete history.

Both provider integrations in this release are **offline contracts only: no live
request has been made to YouTube or to Revolut.** The models, bounds and refusals
are tested against recorded shapes, not against either provider's running API.

## YouTube publishing

The `youtube` extra adds `zeo_core.integrations.google.youtube`: resumable
chunked upload with progress, schedule (`publish_at`), metadata, thumbnail,
caption track, playlist item, channel identity and video state. It takes
injected credentials only (`GoogleCredentialSource`) and reads no credential
file. Models refuse what YouTube would reject before any call is made, and a
failed upload is never retried. When YouTube keeps an upload private because the
API project is unverified, the result says so rather than reporting success.

For files too large to hold in memory, the provider client opens resumable
sessions **inside** the custody boundary and never sends the bytes itself.
`transfer` sends a file to a session link with no credential attached, probing
first so that a drop, a kill or a reboot resumes at the exact byte. `job` and
`publish` are a write-once job directory and an executor CLI (`pair`,
`connections`, `run`, `status`); if the final answer is lost, the executor adopts
one matching upload, holds when it finds several, and never uploads twice. If
YouTube refuses a link without a token, the job switches to a custody relay —
sealed 4 MiB chunks through ZEOconnect, same session, same resume. This replaces
0.11's `upload_video`.

YouTube's own fields (video id, privacy, schedule and link) live only in a
replaceable `youtube.json`, never in the write-once job files. `retain` refreshes
each record once it is 20 days old, and deletes it when the video is gone, when
access has lapsed, or when no refresh succeeded by day 29, logging when and why.
This follows the Developer Policies' 30-day limit (§III.E.4).

YouTube is deliberately **not** in the setup catalogue.

## Local Revolut Business enrollment

The base package gains a read-only Revolut Business client: accounts, and one
bounded, explicitly paged transaction page over a fixed origin, with exact
`Decimal` amounts, declared normalization, byte bounds on both the upstream body
and the normalized result, observation timestamps, and no credential path of its
own.

The `revolut` extra adds `zeo_core.integrations.revolut.local` for an individual
enrolling **their own** account: a private key and certificate in per-user files
that are never written inside a repository, a one-time consent state, and a
refresh marker made durable before the request is sent. Because a Revolut refresh
invalidates the previous access token, an attempt whose answer was lost is
recorded as unknown and **stays blocked**; no elapsed time makes it known. A
fresh consent on the same registration does not clear that block, and a stored
enrollment is bound to its environment, so a sandbox file is refused by a
production-selected object and the reverse, before any client is built or any
credential is sent.

This is the hobbyist path: credentials in private local files, no database. The
hosted organizational path remains ZEOconnect's.

## Connection setup and availability

Versioned, inert setup manifests for all twenty integration tracks, with current
operation and capability mappings and explicit unadmitted hosted profiles;
seven-dimension availability snapshots that preserve every blocker, expiry and
known revocation **without conferring dispatch authority**; and versioned
resource-selection metadata that distinguishes exact objects, fixed collections
and explicitly dynamic future membership. `HostedServiceRegistry` replaces the
hosted resolver's hard-coded service branch with explicit, immutable
registrations; the reviewed default registers exactly the previously resolvable
`google.drive` download, so resolution is unchanged.

## Install and upgrade

```bash
uv pip install --upgrade "zeocore==0.12.0"
uv pip install "zeocore[youtube]==0.12.0"
uv pip install "zeocore[revolut]==0.12.0"
```

The first command includes the read-only Revolut client and the setup and
availability contracts. The `youtube` extra adds the publishing integration and
its executor; the `revolut` extra adds local enrollment (client key, certificate
and RS256 assertion). `zeocore[all]` already carries every dependency both new
extras need. Install other provider extras only as needed.

Examples are repository assets, not installed shell commands; run them from the
matching `v0.12.0` checkout. The [example catalog](examples/README.md) and
[API reference](docs/reference/api.md) describe the supported public imports.
