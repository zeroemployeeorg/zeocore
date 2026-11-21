# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**From 0.15.2 on, every merge to `main` is a release.** The sections from 0.15.2 on
are summaries by theme, written after the fact. Each PR adds a fragment to [`changelog.d/`](changelog.d/README.md), and
each version's notes are on its
[GitHub Release](https://github.com/zeroemployeeorg/zeocore/releases). The
sections for 0.15.1 and earlier are the original notes.

## Status

- **Latest release:** 0.55.0 (20 November 2026), on [PyPI](https://pypi.org/project/zeocore/)
  and as a [GitHub Release](https://github.com/zeroemployeeorg/zeocore/releases).
- **Python:** 3.12 or newer.
- **License:** MIT, with the voice routing code derived from Apache-2.0 work (see
  `src/zeo_core/voice/NOTICE` and `src/zeo_core/voice/LICENSE`).
- **How releases work today:** every merge to `main` is a release. A release candidate and
  promotion flow (`-rc.N` versions) is planned and is not in place yet.
- **Where the notes live:** this file summarizes by meaning. The full text of each version
  is on its GitHub Release.

## Release history, 0.15.2 to 0.55.0 (12 October to 20 November 2026)

Summarized by theme, newest first. Versions named here are real tags.

### 16 to 20 November 2026 (0.53.1 to 0.55.0)

- **0.55.0** adds a Twilio WhatsApp channel: signed inbound webhook parsing, a one-shot
  outbound send that can report an unknown outcome, and a test double that needs no
  network or key.
- **0.54.0 (breaking)** removes Supabase from the core: the `zeo_core.integrations.database`
  package, the `supabase` extra and its setup entries.
- **0.53.1 to 0.53.4** tighten newsletter decoding (refusing multipart boundaries written as
  RFC 2231 continuations) and fix one flaky test.

### 25 October to 9 November 2026 (0.38.0 to 0.53.0)

- **Voice (0.51.0, 0.52.1):** vendor-neutral speech routing for speech-to-text and
  text-to-speech, with provider health, cool-down and failure handling. It is ported from
  Apache-2.0 code; notices were added in 0.52.1.
- **UI cards (0.44.0 to 0.50.0):** a closed `intervention_card` payload for operator
  situations, a `message` and `approval` kind, optional command blocks on source items, and
  a larger copy-line limit.
- **React package (0.47.0, 0.48.0):** `packages/react` renders the cards and a read-only
  calendar view, validating payloads in the browser against the published schemas.
- **Release tooling (0.38.0 to 0.43.1):** a proof-report contract, `release verify-event`
  (including private repositories through a pinned key) and `release check-approval`.
- **Providers (0.40.0, 0.41.1):** Moonshot (Kimi) is added as an LLM provider.
- **Libraries (0.42.0):** metadata-only library views and citation access receipts.
- **Documents (0.53.0):** an explicit plain-text conversion API with truthful metadata.

### 22 to 24 October 2026 (0.29.0 to 0.37.2)

- **Python floor lowered (0.29.0)** from 3.14 to 3.12, the version Google Colab provides.
- **Briefing widgets (0.30.0 to 0.36.4):** a widget-routing contract, a "not provided" value
  instead of inventing data, an HTML renderer for traveller-style cards, and keyless public
  source adapters (weather, flights).
- **Agents (0.31.0, 0.35.4 to 0.35.6):** a keyless wrapper over managed agent sessions with a
  spend limit and clear rejection reasons.
- **Public face (0.35.3, 0.36.5, 0.36.6):** README rewritten for new users; `CONTRIBUTING.md`
  written for outside contributors.
- **Release signing (0.37.0, 0.37.1):** a signing manifest can take digests from a reviewed
  build inventory; a release target moved to a renamed repository.
- **Pre-push gate (0.37.2):** pushes only to work-in-progress branches skip the full gate so
  work can be backed up before it is green.
- **Hygiene (0.36.7 to 0.36.9):** tests pin the release component's shape; `contracts/` holds
  only zeocore's own schemas; public-source requests are serialized per host.

### 12 to 21 October 2026 (0.15.2 to 0.28.1)

- **Release on merge, hardened (0.15.2 to 0.15.5):** a failed upload can no longer be skipped
  past; the smoke test waits for the installer rather than a web page; CI installs a pinned,
  checksummed Pandoc.
- **Agent-friendly command line (0.17.0 to 0.23.0):** `zeocore help` describes every argument,
  errors end with the next action, `zeocore doctor` checks a machine, and `invoke --wait`
  waits for an outcome instead of polling.
- **Google Drive (0.16.0):** read-only access to review comments and replies.
- **macOS signing (0.18.0, 0.23.3):** sign an app bundle inside-out; one rule for bundle
  identifiers.
- **Development setup (0.22.1, 0.23.1, 0.23.2):** a contributors charter, `ZEO_WORK_DIR` to
  keep environments and caches out of the checkout, and a committed pre-push gate.
- **kit (0.23.5 to 0.25.1):** hardening of plan files, protected-path checks and client
  behavior, plus key custody checks and `rollback --to VERSION`.
- **Newsletters (0.15.4, 0.25.0 to 0.28.0):** HubSpot drafts accept a reply-to address and need
  only a name; the converter takes a pinned Pandoc and hides text only where it can prove the
  text is hidden.
- **UI result card (0.26.0, 0.33.2):** a data-only `zeo.ui-result/v1` contract and schema.
- **Integrations seam (0.23.6, 0.24.0):** the GitHub App signer moved to the release component,
  with the old import path still forwarding.
- **Test stability (0.24.4, 0.28.1):** flaky tests that kept `main` red were fixed.

Versions 0.5.0 to 0.12.0 and 0.15.0 (23 August to 12 October 2026) have release pages on
GitHub; their summary will follow in a second pull request, together with a clean-up of the
older sections below.

## [0.15.1] - 2026-10-12

A patch release: 0.15.0's `zeocore` command didn't run on a plain install.
It also closes the unprotected TestPyPI publish target.

**0.15.1 has no tag.** It was published from `e22a16ca` by Publish run
38201406179, a manual dispatch, before release on merge. Its smoke test failed
on index lag 30 s after the upload, so that run made no GitHub Release. The
files on PyPI carry GitHub attestations for `zeroemployeeorg/zeocore`,
`publish.yml` and `pypi`, and install and run on a plain install.

### Fixed

- **The `zeocore` command runs on a plain install.** In 0.15.0 it crashed
  at start-up on `pip install zeocore`: `rfc8785`, which the canonical digest
  needs, was only in the `runtime-host` extra, and loading `zeocore release`
  pulled in `requests` through `zeo_core.integrations.github`, which only the
  `github` extra installs. The import check in the publish smoke test passed,
  because it never ran the command. Now:
  - `rfc8785` is a base dependency;
  - `zeocore release` loads only when it is used, and on an install without
    the `github` extra it answers `extra_missing` (exit 20), naming the extra;
  - a test runs the command with every extra-only module refused;
  - the publish smoke test runs `zeocore version` on the plain install,
    before it adds any extra.

  The workaround for 0.15.0 is `pip install 'zeocore[runtime-host,github]==0.15.0'`.

### Changed

- **Every publish goes through the protected `pypi` environment.** `publish.yml`
  no longer has a `testpypi` dispatch target, its job or its smoke step. That
  environment had no protection, so any account or App able to dispatch the
  workflow could publish to TestPyPI unreviewed (ZEOCORE-SOW-30 §3.2). A test
  now pins the dispatch options to `pypi` and the workflow's environments to
  `pypi` alone. `CONTRIBUTING.md`'s release steps drop TestPyPI staging, say the
  operator tags, and name `zeroemployeeorg` as the trusted publisher's owner.

## [0.15.0] - 2026-10-12

This release ships every change since 0.12.0. Three planned releases were never
published, and their changes ship together here: 0.13.0, 0.14.0, and the
binding-fence release, which was planned as "0.15.0 or 0.16.0". No entry
below was changed by folding them.

It conforms to these ZEOconnect Broker contracts, each pinned from its merge
commit:
- **1.0.0:** zeoconnect #35 at `2475934682ca07d480aa16946b3f3d6647255f6f`,
  file sha256 `39e97d170941c4fba95f018b3943715fc0b7aa916211e1c1b28582e52366b2c0`;
- **1.2.0**, the expected-binding fence: zeoconnect `31da8bda` (#56),
  `docs/contract/broker-contract-v1.md`, sha256 `d2be4901…`;
- **1.2.1**, the changed-request marker: zeoconnect `707bb91b` (#67),
  `contract/broker-contract-v1.md`, sha256 `dc2e951f…`;
- **1.2.2**, the re-enrolment code: zeoconnect `243409bc` (#70),
  `contract/broker-contract-v1.md`, sha256 `358bcb0f…`;
- **1.4.0**, the access refusals: zeoconnect `c134661e` (#73),
  `contract/broker-contract-v1.md`, sha256 `15f7b4db…`.

### Added

- **The expected-binding fence** (contract 1.2.0 §6a). A fenced invocation is
  refused when its connection was re-enrolled or changed between the listing
  and the call.
  - `HostedConnectionSummary.connection_revision` is the enrolment's revision.
  - `summary.expected_binding()` returns a `HostedExpectedBinding` for
    `HostedOperationRequest.expect`.
  - The Broker refuses a mismatch with no further provider call.
    `binding_mismatch_of(response)` names the field that differed, and
    `receipt["binding"]` holds the values the Broker found.
  - When the fence can't be checked, because the listing has no revision or
    the Broker answers a fenced call with 422, zeocore raises
    `HostedFenceUnsupportedError`. Its `reason` says which:
    `"no_revision"` or `"invalid_fenced_request"`. A 1.2 Broker answers
    invalid arguments with the same 422, so the second message does not claim
    the fence alone was the cause. zeocore never resends the request unfenced.
  - Every request now declares `ZEOconnect-Capabilities: stopped-code,
    expected-binding`.
- **A re-enrolled connection is its own error.** A connection id re-enrolled
  with a changed subject, scopes, resources or credential is refused by the
  Broker with a 400 `kernel connection binding changed` (zeoconnect #59). This
  now raises `HostedConnectionChangedError` instead of a generic refusal. It
  comes before any provider call, and the fix is ZEOconnect's repair, never a
  retry. From contract 1.2.2 the Broker also marks it `"code":
  "connection_binding_changed"` for a client declaring `expected-binding`.
  zeocore reads the code first and keeps the detail string as the fallback,
  so a 1.2.1 Broker is still recognised.
- **A changed request under a used key is its own error** (contract 1.2.1
  §6a.5). The same idempotency key with different arguments or a different
  `expect` is refused, nothing is recorded, and the key's original outcome
  stands. An effect's marked 400 raises `HostedRequestChangedError`. A read
  comes back `failed_safe`, which `request_changed_of(response)` recognises.
  The fix is a new key for the changed request, never a retry. An unmarked
  conflict, such as the 1.1 `approval is unavailable`, stays a plain refusal.

- `HostedRevolutBusinessClient`: Revolut Business account and transaction
  reads through ZEOconnect, read-only (contract §6). On the wire the query's
  start field is `from`; a result carrying any JSON number is refused, so
  amounts are only ever exact decimal strings.
- `HostedStoppedError` (with `control` and `scope`), `HostedUnavailableError`,
  `HostedUnreachableError` and `HostedUpgradeRequiredError`, all subclasses of
  `HostedClientError`, and `stop_of(response)` for an orchestrated stop.
- `NormalizedErrorCode.STOPPED`. zeocore declares
  `ZEOconnect-Capabilities: stopped-code` on every request, so the Broker may
  send it; without that capability it sends `REQUEST_REFUSED` with
  `stopped:<control>:<scope>`, which `stop_of` reads the same way.
- `TransactionQuery` accepts `from` as well as `from_`.
- Contract `1.1.0` additions (zeoconnect #40), which are additive, so no
  re-pin is needed: `HostedOperationResponse.replayed` is true when the Broker
  served its stored outcome, which is never a fresh success. `is_outage(response)`
  recognizes `failed_safe` with `PROVIDER_UNAVAILABLE`, such as
  `controls_unavailable:<control>`, as an outage, never a stop. The Broker
  stores that outcome, so retrying with the same idempotency key replays it;
  another attempt needs a new key and is a new occurrence, never an automatic
  retry. YouTube retention defers on it.

- **A named session file for a product's service user** (operator ruling,
  2026-10-12; client-access proposal §9, option A). `ZEOCORE_SESSION_FILE`
  selects `FileSecureSessionStore`. It is opt-in, never a fallback, and checks:
  - a 0700 directory owned by the user, with no symbolic link in the path;
  - no directory above it writable by another user unless it is sticky;
  - a regular 0600 file owned by the user, checked on the open descriptor.

  Writes are atomic, and the file is bound to its Broker origin. Without the
  variable, a Keychain that can't be read still fails closed with exit 12.

- **Broker contract 1.4.0** (zeoconnect #73, merged at `c134661e`):
  - **the three access refusals:** `door_required`, `app_identity_unverified`
    and `no_principal`, in both of their forms (an `:invoke` answered
    `failed_safe` with the exact message, and 403 `{"code": ...}` elsewhere).
    The client raises `HostedAccessRefusedError` or reports
    `access_refusal_of(...)`, and the CLI answers each by name with the way
    back;
  - **clearer CLI answers:** a stop answers its `control` and `scope`, and a
    426 answers `upgrade_required`;
  - **the parity fixtures for ZEO-RT's door** (ZEOCORE-SOW-27 §3), in
    `contracts/broker-1.4/`: digest vectors for one request per 1.4.0
    operation, plus `on_behalf_of` variants, each validated against the
    frozen schemas, with zeoconnect's own conformance cases vendored beside
    them. `tools/broker-1.4-digest-vectors.py` rewrites them.

- **Delegated PyPI releases** (`zeocore release pypi apply | approve`), on the
  operator's delegation of 2026-10-11.
  - The `zeo-release` GitHub App creates the release tag with a one-repository
    token minted from its Keychain key.
  - matorclawson approves the waiting `pypi` deployment, once, only for that
    exact tag and commit.
  - `admit` refuses unless only the two identities, together, can release:
    matorclawson is the only reviewer with self-review prevented and no admin
    bypass, only `v*` tags deploy, and a tag ruleset lets only the App touch
    `v*`.
  - The targets are a reviewed table: zeocore, zeocreator, zero-employee and
    sovereign-agent.

- **The PyPI adapter** (`zeocore release pypi plan | admit | apply |
  reconcile | verify`), on ZEOCORE-SOW-26.
  - `plan` checks the commit is reviewed and the version agrees everywhere.
  - `admit` checks that only the operator can approve the publish: the tag's
    creator can't approve its own deployment or bypass it as an admin, and
    only `v*` tags may deploy.
  - `apply` creates the release tag, its one effect, under a durable intent,
    and never retries.
  - `reconcile` follows the tag to its publish run.
  - `verify` checks PyPI's files, each attestation's recorded publisher, and
    that each attestation names that file's digest.

- **One sign-and-verify step for every org executable** (`zeo_core.release.signing`,
  `zeocore release sign | verify`), on ZEOCORE-SOW-19 to SOW-22. It is built
  first, before the PyPI adapter, because the Go seats and Pomar wait on it.
  - Internal Darwin releases sign with ZEO Runtime Code Signing, pinned by its
    leaf SHA-1. Distributed releases hold as `notarisation_unbuilt`; Linux is
    refused as signed until a Linux contract is adopted.
  - Inputs come from the reviewed `release.yaml`, never from flags. The
    signing host is configuration (`ZEOCORE_SIGNING_HOST`).
  - A separate candidate is signed under a durable intent, and a lost answer
    is reconciled by readback, never by signing again.
  - `verify` is read-only. It refuses an ad-hoc signature even when its
    requirement names the pin, a bare cdhash, another certificate (including
    the other audience's), and entitlements that differ from the manifest's.
  - A locked keychain is reported only when its settings establish it.
  - Request and response packs (`zeocore release pack | receive`) for signing
    on matormini from another host.
  - The signing service on matormini (`zeocore release serve`) and its client
    (`zeocore release send`). It is tailnet-only and identifies callers by
    `tailscale whois`. It admits a request only under ZEOCORE-SOW-24 §1: a
    node, product and identifier allowlist; the manifest read from GitHub at
    a merged commit; internal audience only; size, count and rate bounds; and
    a durable journal of every request.

- **Revolut Business reads for expenses, receipts and labels**
  (`zeo_core.integrations.revolut`), approved by the zeocore elders as an
  independent 0.13.0 expansion (org issue #787, Q2):
  - `list_expenses` (one bounded page, with `next_to` and stall detection);
  - `get_expense`;
  - `download_receipt`, bounded to 10 MiB while it is read, never parsed or
    logged, and returned with its sha256;
  - `list_label_groups` and `list_labels`, which page by an opaque token.

  Amounts stay exact `Decimal`s, and an expense's `payer` name is dropped.
  Redirects are never followed. A provider identifier must be one safe path
  segment, or nothing is sent. The 0.12 read operations and their error codes
  are unchanged.

  **Offline contract only; no request has been made to Revolut.** Which date
  Revolut's expense window filters on is not stated in its published
  contract. The cursor assumes `expense_date`, and that is unverified until a
  live sandbox run. **So every `ExpensePage` reports
  `completeness="unverified"`**, and a run that reaches `next_to=None` is not
  proof of a complete window (org #787, council ruling on E4).
- **The `zeocore` command**, for callers that don't import Python: the ZEOconnect
  Broker and WEB, TypeScript pipelines, scripts. JSON on stdin and JSON lines on
  stdout with the result last. Exit codes are the YouTube publish command's
  family: 0, 2, 10, 11, 20, plus 12 not paired and 13 ambiguous
  (`docs/reference/cli.md`).
  - `zeocore schema list` and `zeocore schema <name>` give the public contract
    models as JSON Schema 2020-12 under stable names, among them
    `hosted.operation-request`, `hosted.operation-response` and
    `connections.normalized-error`. The same schemas are committed under
    `contracts/zeocore-v1/`, and a test fails if a model drifts from its file.
  - `zeocore validate <name>` checks a value and answers the normalized form.
    Its errors carry location and type only, never input values.
  - `zeocore digest` is the sha256 of a value's RFC 8785 canonical bytes.
  - `zeocore version` names the release and the protocols.
- **The ZEOconnect client as `zeocore` commands,** so apps in any language reach
  the Broker through one client instead of writing their own: `login`, `logout`,
  `whoami`, `connections`, `invoke` and `artifact get`.
  - Each app has its own device grant in the Keychain, chosen with `--profile`
    or `ZEOCORE_PROFILE`. With no Keychain the command fails closed with exit 12.
  - `invoke` derives its idempotency key from the request, so running the same
    invocation again replays and never acts twice. It answers `request_key` and
    `retry`. `--wait-approval` emits the approval link and replays the same key
    until the person decides.
  - `KeychainSecureSessionStore(profile=...)` keeps one entry per profile. No
    profile keeps today's entry.
- **The runtime-host names are a supported tier** (planned for 0.15.0, after
  0.14.0). `zeo_core.adapters.runtime_host.canonical`, `.catalogue`, `.channel`
  and `.host` now declare `__all__`: `canonical_bytes`, `digest`, `parse_json`,
  `manifest_inventory`, `MAX_BYTES`, `ProtocolError`, `InvalidRequestError`,
  `CandidateCatalogue`, `validate_inventory`, `RuntimeChannel`, `EffectPort`,
  `ManagedHost`, `parse_result` and `prepare_request`. Until this release they
  were internal, with no stability promise. Nothing about them changes.
  `zeo_core.contracts.runtime.RUNTIME_HOST_PROTOCOL_VERSION` (1) names the
  version every versioned runtime-host-v1 model carries, so a consumer can bound
  compatibility on it. The `contracts/runtime-host-v1` schemas and vectors are
  still not in the wheel; pin them by sha256 from the source tag.
  **The promise, from 0.15.0:** while zeocore is 0.x, removing one of these
  names or changing it incompatibly happens only in a minor release (0.y.0),
  never in a patch, and is listed under "Breaking" in that release's entry.
  From 1.0.0 it needs a major version. A wire-incompatible change to a
  runtime-host-v1 model also raises `RUNTIME_HOST_PROTOCOL_VERSION`.
- **The merger's decision core, ported into zeocore** (`zeo_core.merger`). This
  follows the operator's ruling that the merger is a zeocore app; ZEO-LEDGER owns
  the decision contract and reviews the port.
  - It's a port of zeomerger's register reader, decision core, pass and journal.
  - It is held to ZEO-LEDGER's language-neutral parity fixtures, committed byte
    for byte under `contracts/merger-v1/`: 29 decision cases (v2), 11 whole-run
    cases (v4) and 14 register cases.
  - It never writes the register, makes exactly one merge call per receipt with
    `sha` set to the approved head, journals its intent durably before the call,
    and reconciles a lost answer by reading.
  - A merge call that provably never left the host (no token, or no
    connection) is journaled `not-sent`. That releases the intent, so a later
    pass decides again; anything that may have arrived stays `unclear`. The Go
    oracle does the same (zeomerger `4eb8950`, parity pass v4). An injected
    HTTP client that follows redirects is refused, since a followed redirect
    would make a later connect error no proof that nothing was sent.
  - The base branch's required checks are read from its rulesets only, so a
    branch under classic branch protection is refused (it fails closed).
  - The GitHub adapter is in; the `zeocore merger` command follows.
- **GitHub App installation tokens for an isolated principal**
  (`zeo_core.integrations.github.app_tokens`, `github-app` extra). These are
  for the merger, which by the operator's ruling is a zeocore app whose key is
  kept outside ZEOconnect.
  - It signs an RS256 App JWT and mints a token for exactly one repository and
    exactly the requested permissions. A broader grant is revoked and refused.
  - It sends one attempt and never retries.
  - The key is read only from the running user's own Keychain item, or from
    `FileAppKeySource`: the user's own regular file with no group or other access,
    never a symlink.

### Changed

These change what a caller sees. The session store API and the
`ZEOconnectHTTPTransport` constructor (`session_store`, `base_url`,
`allow_development_origin`, `http_client`, `clock`) are unchanged.

| Broker answer | 0.13.0 | 0.14.0 |
|---|---|---|
| 403 `{"code": "stopped", "control", "scope"}` | "hosted request was refused" | `HostedStoppedError`: "hosted request was stopped by `<control>` (`<scope>`)" |
| 503 with the protocol header | "hosted request was refused" | `HostedUnavailableError`: "hosted transport is unavailable" |
| 401 | "hosted request was refused" | one refresh, then the same request once more; a second 401 gives "paired device session was refused; pair this device again" |
| 426 | "hosted request was refused" | `HostedUpgradeRequiredError`, which says to upgrade zeocore |
| no connection to the production Broker | "hosted transport is unavailable" | `HostedUnreachableError` with the contract's off-network wording; a development origin keeps the old message |
| a response field zeocore doesn't know | the response is invalid | the field is ignored (contract §10) |

- **A Broker 503 is an outage, never a refusal and never a stop** (council
  ruling E7). It is not evidence that an effectful request was not accepted,
  and it grants no retry: an effect is sent once. The one safe read,
  `google.drive.file.download`, is attempted a second time only when no
  response arrived, never because of a 503 (contract §9).
- **"Refused" is not evidence of non-acceptance.** Only a stop is a positive
  statement about what the Broker did.

- **PyPI `admit` checks what a non-Admin can see** (the operator, SOW-29,
  option C). It no longer requires the exact `v*` ruleset bypass list, which
  GitHub hides from anyone without Admin, and no agent is an Admin under ruling
  r7. It requires the ruleset to be active on `refs/tags/v*` with no
  exclusions, restricting creation, update and deletion, and matorclawson's own
  `current_user_can_bypass` to be `never` (`approver_can_bypass` otherwise). A
  hidden bypass list is now read as hidden, not as an error.
- **`zero-employee` is no longer a PyPI target.** It is now zeoruntime,
  released Go-only through the shared signed release component.

- **`make setup` and CI install exactly what `uv.lock` pins** (`make sync`, which
  runs `uv sync --locked` with every gate extra plus dev, lint and docs). Before
  this, `uv pip install -e .[extras]` resolved the newest releases, so CI could
  test dependencies a release never ships (pydantic 2.14 against the locked
  2.13.4 on super PR #100). A stale lock now fails setup instead of drifting.
- **One hosted error message is new.** A Broker response with no
  `ZEOconnect-Protocol-Version` header now raises `HostedClientError("hosted
  response did not come from the Broker")`. Before, it raised "hosted protocol
  version is incompatible", which now means only a different or doubled header.
  No other `HostedClientError` message changes, and neither do the session
  store API or the `ZEOconnectHTTPTransport` constructor. Callers that match
  on the old message for a headerless response must match on the new one.
- **Breaking: `merge_pull_request` needs the head `sha` and merges only by
  `merge`.** `sha` is a required keyword, the full 40-hex commit the merge was
  approved for, and GitHub refuses the merge if the head has moved. `squash`
  and `rebase` are refused. Nothing in zeocore called it. In ZEO, merging is
  zeomerger's alone (operator decision, 2026-10-10).

### Deprecated

- **The ZEOconnect client commands for seats** (`zeocore login`, `logout`,
  `whoami`, `connections`, `invoke` and `artifact get`). A seat uses `zeo`,
  ZEO-RT's front door, for every Broker operation (ZEOCORE-SOW-27). The
  commands stay for a product running as its own macOS service user,
  `zs-<product>` (the first is `zs-newsroom-intake`). From Broker contract 1.4
  the Broker refuses a product's grant from any other user, `rodbot` and an
  author's own login included, with `app_identity_unverified`, and a seat's
  grant with `door_required`. The Broker enforces this, not zeocore.

### Fixed

- **Hosted access reaches ZEOconnect again.** `ZEOCONNECT_PRODUCTION_ORIGIN` is
  now `https://broker.connect.zeo.ac`, the Broker, which is reachable only on
  the organisation's private network (contract §2). The retired
  `https://connect.zeroemployee.org` and the browser-facing
  `https://connect.zeo.ac` are refused as transport origins (org #791).
- **YouTube retention no longer deletes data during a stop or an outage.** A
  Broker stop or a 503 used to read as "refused", which removed `youtube.json`
  as if access had lapsed. Both now defer, as an unreachable Broker already
  did, until the 30-day limit.
- **A hosted request never carries NaN or ±Infinity.** They are not JSON, and
  the Broker refuses them (zeoconnect #49). httpx 0.27, which zeocore allows,
  would have sent them. The transport now refuses such a body before sending,
  with `HostedClientError("hosted request holds a number JSON cannot carry")`.
- **A YouTube job survives a kill mid-write.** The job directory's write-once
  files (each event and `receipt.json`) were created under their final name
  and then written. A process killed in between left an empty file, so the next
  run could not read the job at all. Each file is now written and synced
  under a temporary name, then hard-linked into place, which is still
  write-once. A filesystem without hard links is refused with `JobError` rather
  than written directly, because a direct write would bring back the partial
  file. This protects against a killed process. Power-loss durability also
  depends on the filesystem honouring fsync.
- **Unknown fields nested in a Broker response are ignored too.** 0.14.0 drops
  unknown top-level fields (contract §10), but an unknown field inside
  `artifact` or `normalized_error` still refused the whole response. Both now
  drop unknown fields, so a later Broker can add fields at any level. A
  `provider_detail` is still refused.
- **Two processes sharing a paired session no longer ask to re-pair.** Refresh
  tokens are single use (contract §4). When a YouTube run and the retention
  sweep both hit a 401, both refreshed with the same token, and the loser
  reported "pair this device again" when nothing was wrong. zeocore now reads
  the session store again before refreshing, and again if the refresh is
  refused. A pair the other process rotated is used instead. One narrow window
  remains: if the refusal is read before the other process has saved its
  pair, the refusal stands.

- **A hidden ruleset bypass list is never read as empty.** GitHub returns a
  ruleset's `bypass_actors` only to a user with Admin on the repository.
  `admit` used to read the missing key as an empty list and refuse as
  `tag_ruleset_missing`, naming the wrong cause. It now records the list as
  hidden, and doesn't rely on it (see Changed).

- **The YouTube custody relay no longer retries a failure no retry can cure.**
  Until now only a refusal ended a relayed upload: every other client error
  backed off and probed again, up to 20 times. That covered an incompatible
  protocol, an invalid answer, an expired or missing device session, and the
  managed-execution refusal. Now only "unavailable" and "pending" are
  transient. Anything else holds the job as `upload_rejected` after one
  request. A refusal, a Broker stop included, still holds it as
  `session_link_refused`.
- **A Broker response without its protocol header is terminal everywhere.**
  On the relay, a 502/503/504 was read as an outage *before* the header was
  checked. A proxy's headerless 503 was therefore retried as if the Broker had
  sent it. Now the header is checked first on every Broker response, so a
  missing, different or doubled header is a protocol failure. It is never
  retried, refreshed around or read as a stop. A Broker 503 *with* the header is
  still an outage and is retried (org #787, council ruling E5).
  **What this means in practice:** a 502 or 503 from a CDN or edge proxy,
  which carries no Broker header, is the most common real outage. It now ends
  a relayed YouTube upload at once, holding the job as `upload_rejected`,
  where before it was waited out. That is E5's choice. When the Broker is
  reachable again, append a `released` event and the next run resumes the
  upload.
- **A GitHub effect is sent once, never retried.** `make_request` retried every
  method on a 5xx, a rate limit, a timeout or a dropped connection. So one
  ambiguous reply could open a second pull request, post a second comment or
  issue, write a file twice, or merge twice. Only GET and HEAD are retried now.
  POST, PUT, PATCH and DELETE are sent exactly once, and an unknown outcome is
  raised for the caller to reconcile by reading back.

### Known issues

- **Hosted access stays unavailable in this release,** as in every release since
  0.10.0. The hosted origin is still pinned to `connect.zeroemployee.org`, which
  no longer names the ZEOconnect deployment; local integrations are unaffected. This release
  changes relay failure handling. It does not claim conformance to Broker
  contract `1.0.0` and does not repair the hosted origin; both are for 0.14.0
  (org #791).

## [0.12.0] - 2026-10-08

### Added

- **YouTube data retention** (`youtube.retention`, `publish retain`): YouTube's fields
  (the video id, privacy, schedule and link) live only in a replaceable `youtube.json`,
  never in the write-once job files. `retain` refreshes each record once it is 20 days
  old (one `videos.get`, 1 unit). It deletes the record when the video is gone or
  access has lapsed, or when no refresh succeeded by day 29, and logs when and why.
  This follows the Developer Policies' 30-day limit (§III.E.4). `Receipt` no longer
  carries `provider_object_id`, `video_url` or `response_sha256`; it points to
  `provider_record`.
- **YouTube publishing for multi-GB files** (`zeo_core.integrations.google.youtube`):
  the provider client opens resumable sessions inside the custody boundary and
  never sends bytes; `transfer` sends a file to a session link with no
  credential, probing first so a drop, kill or reboot resumes at the exact byte;
  `job` and `publish` are a write-once job directory (ZEO Runtime's occurrence
  identity, Go-computed test vectors) and an executor CLI (`pair`, `connections`,
  `run`, `status`) whose lost-final-answer path adopts one matching upload,
  holds on several, and never uploads twice. Replaces 0.11's `upload_video`.
  If YouTube refuses a link without a token, the job switches to a custody
  relay (`relay.RelayByteHttp`, `ZEOconnectHTTPTransport.relay_youtube_chunk`):
  sealed 4 MiB chunks through ZEOconnect, same session, same resume.
  Offline contract only; no live request has been made.
- **YouTube publishing** (`zeo_core.integrations.google.youtube`, extra `youtube`):
  resumable chunked upload with progress, schedule (`publish_at`), metadata,
  thumbnail, caption track, playlist item, channel identity and video state.
  Injected credentials only (`GoogleCredentialSource`); it reads no credential
  file. Models refuse what YouTube would reject before any call. When YouTube
  keeps an upload private (an unverified API project), the result says so; a
  failed upload is never retried. Not in the setup catalogue. Offline contract
  only; no live request has been made.
- **Local Revolut Business enrollment** (`zeo_core.integrations.revolut.local`,
  extra `revolut`): use your own account on your own machine, with no hosted
  service or database. Three explicit steps (`setup`, `authorize`, `complete`)
  as a CLI or in Python; key, certificate and rotating tokens in owner-only
  files outside any repository; configuration only from `.env`. One refresh per
  read; a marker is written durably before a refresh is sent, and a lost answer
  is a blocked unknown outcome that only a fresh consent clears. Registered as
  the `revolut.business` entry point; the setup catalogue now reports the local
  profile as implemented. Offline contract only; no live request has been made.
- `HostedServiceRegistry`: explicit, immutable registrations replace the hosted
  resolver's hard-coded service branch. The reviewed default registers exactly
  the previously resolvable `google.drive` download, so resolution is unchanged.
- Read-only Revolut Business client (`zeo_core.integrations.revolut`): accounts
  and one bounded, explicitly paged transaction page over a fixed origin, with
  exact `Decimal` amounts, declared normalization, byte bounds on the upstream
  body and the normalized result, observation timestamps and no credential path
  of its own. Offline contract only; no live provider request has been made.
- Versioned, inert setup manifests for all twenty integration tracks, with
  current operation/capability mappings and explicit unadmitted hosted profiles.
- Seven-dimension availability snapshots that preserve every blocker, expiry
  and known revocation without conferring dispatch authority.
- Versioned resource-selection metadata distinguishing exact objects, fixed
  collections and explicitly dynamic future membership.

### Fixed

- Local Revolut enrollment no longer clears an unresolved refresh when the
  owner gives a fresh consent on the same registration. Consent authorizes new
  tokens; it does not settle what the provider did with a request whose answer
  was lost. The marker and its block survive: reads continue on the new access
  token, refreshing stays blocked. A refused grant or a rejected token, which
  are definite answers, are still cleared by a new grant. A new registration
  records `follows_unresolved_refresh`, and the guide no longer describes
  deleting a certificate as certain isolation: no qualified recovery exists.
- A stored local Revolut enrollment is bound to its environment. Files written
  for sandbox are refused by a production-selected object, and the reverse,
  with `ENVIRONMENT_MISMATCH` before any client is built or any credential is
  sent, including for completion and refresh.
- Service requirements accept existing single-component service identities such
  as `github`, while retaining exact operation-prefix validation.

## [0.11.0] - 2026-09-10

### Added

- Version-1 supervised Runtime capability host and `zeo-capability` CLI with
  explicit provider binding, canonical schemas/vectors and private inherited IPC.
  The `runtime-host` extra is separate from `all`. Joint Runtime/ZEOconnect wire
  agreement and real application acceptance remain required.
- Runtime-admitted meeting-v1 adapters for Notion upsert, Sheets/Calendar reads
  and Gmail draft creation, retrieval and reconciliation, using their existing
  meeting request/receipt protocol.
- Offline catalogue and meeting-request examples, provider-registration guidance,
  and versioned installation guidance throughout the public docs.
- Searchable documentation with generated API reference and GitHub Pages publishing.

### Fixed

- Legacy plugin registration publishes transactionally, restores surviving
  shadowed contributions on unload, uses registration snapshots for cleanup,
  and refuses ambiguous selected entry points before import.
- GitHub release announcements wait for published-package smoke checks, including
  the installed Runtime host extra, offline examples and missing-authority refusal.

## [0.10.0] - 2026-09-09

### Added

- Governed Gemini reference-image commissioning with durable dispatch markers,
  private hashed artifacts, project-bound custody and read-only reconciliation.
  Image candidates remain unreviewed; ambiguous delivery never triggers another POST.
- Conversion receipts, versioned notebook semantic comparison and strict staged
  batches through the public Jupytext/Pandoc APIs. A complete reference consumer
  independently checks fixtures and runs in separate test/production roots.
- Optional `notebook` extra for fresh-kernel execution with parent-enforced
  deadlines, bounded output, observed process cleanup, resolved interpreter and
  environment identity, declared lock provenance and explicit cell accounting.
- Explicit test and production integration environments with a process launcher,
  scoped credential inputs, secure prompts and separate configuration, credential
  caches, temporary files and working directories. All sixteen integration entry
  points plus hosted ZEOconnect have detailed account and E2E setup guides.
- Managed LLM runs fail on the selected provider instead of silently substituting
  another provider or mock; offline mock use requires explicit test fixtures.

- Kit marketing integration and thirteen registered capabilities for broadcasts,
  sequence authoring, subscriber consent, tags and reporting. Includes explicit
  reviewed sends, safe error outcomes, an offline example and provider-contract tests.
- HubSpot postmerge review corrections: tolerate inert null workflow decorations,
  allow reviewed unenrollment despite unrelated graph drift, and sanitize malformed
  provider schedule errors.

- HubSpot marketing integration and eleven registered agent capabilities for
  newsletter drafts, reviewed publishing and scheduling, campaigns, subscription
  preferences and marketing email workflows. Includes bounded pagination, safe
  error outcomes, workflow ID mapping, an offline example and account-tier docs.

- Native, consent-bound ZEOconnect composition with explicit `fake`, `local`,
  `hosted`, and isolated `governed` profiles. Applications declare existing
  service/operation identities once and receive structured connection states
  without import-time or resolution-time networking.
- An explicit device-pairing lifecycle, rotating device sessions, macOS
  Keychain custody, deterministic in-memory test custody, sanitized connection
  selection, and a fixed-origin/versioned ZEOconnect HTTP transport.
- A deterministic Drive fake and conformance proof that one Drive-read business
  function runs unchanged under fake, local, and hosted composition.

### Fixed

- Native fake and hosted service imports no longer require optional local Google or Bluesky dependencies; SDK loading is deferred until local service construction.

- Notebook worker results are published atomically so polling parents cannot
  observe partially written JSON. Successful and failed publication paths have
  deterministic regression controls.
- Google Drive listing now uses the SDK's `pageSize` keyword in both listing
  paths. Real discovery-builder regression tests prevent permissive mocks from
  hiding a broken first account check.
- Bind full HubSpot workflow reads to the requested ID before graph review or
  follow-up enrollment, update, activation, metrics and archive requests.
- Kit mutation responses now require a valid operation-bound resource ID; a
  newsletter send must return a new ID distinct from its source draft. Invalid
  identities retain an unknown outcome without retrying.
- HubSpot unenrollment rejects metadata for missing, malformed or mismatched
  workflow IDs before mapping or deletion, while permitting unrelated graph drift.

### Changed

- `httpx` is now a base dependency because the hosted bridge is part of normal
  ZeoCore installations. Construction remains inert and ignores ambient proxy
  configuration.
- New hosted invocation requests omit connector revisions. ZEOconnect derives
  the immutable revision from the authenticated connection; the 0.9 request
  field remains optional for source compatibility during server migration.

### Security

- Managed Supabase launches reject identical test and production project URLs,
  even with distinct keys, before creating state or starting the application.
- Managed integration children disable ambient netrc lookup, preventing HTTP
  clients from replacing a selected token with credentials from the user home.
- Hosted resolution and Supabase remain separate boundaries: the ZeoCore client
  accepts no Supabase URL, key, database role, Vault reference, tenant ID, or
  provider credential.
- Governed resolution cannot instantiate a hosted client, load a paired-device
  session, or fall back to member authority. Effects never receive the safe-read
  transport retry granted to the first Drive observation.

## [0.9.0] - 2026-09-06

### Added

- First-class Supabase integration through the maintained `supabase>=2.31,<3`
  Python SDK. The `zeocore[supabase]` extra exposes runtime-checkable,
  injectable services for bounded PostgREST CRUD and named RPC, secret-free
  Auth dispositions, bounded Storage, named Edge Functions, and explicit async
  Realtime subscriptions.
- Typed filters, ordering, row pages, user/session status, bucket metadata,
  Function results, and Realtime changes. Update/delete require filters;
  identifiers, object paths, response sizes, caller headers, and project URLs
  fail closed.

### Security

- Supabase keys remain owned by the auth/client-construction seam and are
  absent from configuration and public results. Privileged server keys require
  explicit opt-in and never imply application authorization.
- Raw SQL, arbitrary URLs, signed bearer URLs, the Management API, and
  `vault.decrypted_secrets` are deliberately absent. Provider exception text is
  discarded before it can carry credentials into logs or receipts.
- Supabase OAuth start rejects an authorization URL outside the configured
  project origin. Lazy Realtime construction keeps its project key opaque and
  releases the local reference after SDK construction.

### Fixed

- Supabase convenience methods now return a structured uninitialized result
  instead of raising while resolving the absent client.

### Documentation

- The Supabase tutorial now walks from project creation and publishable-key
  selection through Row Level Security, first read, Auth, Storage, Functions,
  Realtime, privileged-key isolation, and production verification.
- The Notion tutorial now teaches the admitted `notion.page.upsert` contract,
  deterministic marker, cited content, dispatch/reconciliation boundary, and
  hosted versus local custody paths. A credential-free runnable example makes
  the request and connector revision inspectable without issuing an effect.

## [0.8.0] - 2026-09-05

### Added

- A complete adapter-neutral `BrokerExecutionStore` protocol now covers the
  durable nonce, idempotency, evidence, and outcome surface used by
  `EffectOrchestrator`. SQLite remains the local implementation; hosted stores
  no longer need to import or impersonate it.
- `ObservationOrchestrator` provides a separate durable path for admitted READ
  operations: exact authorization and selected-resource binding, atomic
  idempotency, bounded and sanitized inline/artifact results, immutable
  receipts, and failed-safe timeout handling without mutation-style ambiguity.
- The admitted `notion.page.upsert` operation includes a closed request shape,
  deterministic marker, one-call dispatcher, read-only reconciler, and
  Keychain custody path. A lost create response reconciles by marker without a
  second create.
- Hosted Notion OAuth credential dispatch accepts organization-bound
  `SecretRef` objects for refresh, introspection, and revocation while keeping
  raw token material inside the custody callback.
- Google Drive and Docs accept injected credential sources and client
  factories. The named selected-file scope profile uses only `drive.file`.
- A credential-free ZEOconnect client boundary and local/hosted service factory
  provide protocol-compatible Drive download, Docs read, and confirmed Bluesky
  post services. Remote downloads are size/digest verified and local paths
  never cross the network.

### Changed

- Notion SDK construction has an explicit proxy policy and disables accidental
  ambient proxy inheritance by default. Unit tests inject provider fakes; a
  separately selected profile may opt into ambient transport configuration.
- Workspace connector extras now include the legible `docs`, `sheets`, and
  `slides` aliases in addition to the shared `google` family extra.
- Coverage is measured to two decimals and guarded by a boundary meta-test:
  89.99 fails and 90.00 passes.

### Fixed

- Release gates can no longer print success after accepting a rounded
  below-threshold coverage result.
- The HTTP callback test now matches the real synchronous boundary and no
  longer leaves an un-awaited mock coroutine.
- Jupytext fixtures carry stable cell IDs instead of relying on nbformat's
  temporary repair behavior.
- README test-suite wording and Bluesky tutorial release evidence no longer
  describe stale 0.6-era bytes.

## [0.7.0] - 2026-09-05

### Added

- Complete Notion API `2026-03-11` integration through the official
  `notion-client>=3.1.0` SDK: 44 current data operations spanning pages,
  blocks, databases, data sources and templates, users, search, comments,
  custom emoji, file uploads, views, meeting notes, and Markdown. Pagination,
  request-size limits, bounded retry metadata, and current `in_trash` and
  block-position semantics are explicit. Public OAuth covers authorization-code
  exchange, refresh, introspection, and revocation while placing issued bearer
  credentials directly into `SecretStore` custody.
- Durable effect orchestration under `zeo_core.connections`: an
  organization-scoped SQLite store, exact fail-closed authorization verifier
  with required signature verification and issuer/audience trust configuration,
  persisted pre-dispatch state, one-call provider dispatch, fail-closed ambiguity,
  reconciliation without blind redispatch, atomic outcome receipts, and
  sanitized confirmation evidence. Connector admission closes origin, path,
  redirect, request-field, secret-binding, and reconciliation surfaces;
  `KeychainEffectDispatcher` confines credential material to a one-shot custody
  callback.
- Bounded read/advisory retries and explicit provider fallback under
  `zeo_core.execution`, including hard subprocess deadlines, process-group
  cleanup, cancellation, and one-attempt LLM adapters.

### Changed

- Notion credentials now enter through environment variables or OAuth custody;
  normal configuration and public results no longer carry raw bearer material.
  Historical convenience methods remain available, but removed request fields
  fail closed and ambiguous multi-data-source database calls must name the data
  source explicitly.
- PyPI project URLs, release instructions, public repository links, and package
  examples now point directly to `zeroemployeeorg/zeocore` after the repository move.
- Published authentication results no longer carry raw provider tokens.
  Credential-bearing constructor input is rejected rather than merely hidden by
  selected serializers.

### Fixed

- The publish workflow now creates the matching GitHub release only after PyPI
  succeeds, and its clean-environment smoke test installs the exact version that
  the workflow built instead of permitting resolver fallback to an older one.
- The stable required `verify-all` context now fails visibly when any Python
  matrix leg fails, avoiding a permanently pending required check after matrix
  changes.

## [0.6.0] - 2026-08-31

Two new integrations and the credential handling to support them safely.
Short announcement: [RELEASE_NOTES.md](RELEASE_NOTES.md). Adopter path:
install -> [GET-STARTED.md](GET-STARTED.md) token guide -> construct an
integration -> post.

### Added

- **Google Docs integration** (`zeo_core.integrations.google.docs`): full
  `documents.v1` surface -- `get_document`, `create_document`, `batch_update`,
  plus `get_document_text` with a recursive body walk that includes tables.
  Index-free `replace_text` / `append_text` are the headline editing methods;
  the request builder reverse-sorts by index internally so earlier edits cannot
  invalidate later ones.
- **Bluesky integration** (`zeo_core.integrations.social.bluesky`), the first
  provider under a new `integrations/social/` package. Authenticates with an
  app password -- no OAuth, no developer app, no approval. Rich-text facets are
  computed from UTF-8 byte offsets, so links and mentions survive emoji.
- **Google Sheets integration** (`zeo_core.integrations.google.sheets`):
  reading and writing cell ranges over the Sheets v4 API, same
  `IntegrationResult` shape as every other integration.
- **Google Slides integration** (`zeo_core.integrations.google.slides`):
  presentation and page access over the Slides v1 API.
- **Tutorials for the integrations**, under [docs/tutorials/](docs/tutorials/) and
  indexed from [docs/README.md](docs/README.md) --
  [Google Docs](docs/tutorials/google-docs-integration.md) and
  [Bluesky](docs/tutorials/bluesky-integration.md) join Calendar, Notion, MCP,
  capability authoring, results-and-errors, and context/config/files. Every code
  block in the two new ones was executed against the installed package before
  being written, not transcribed from source.
- **`make doctor`** -- a readiness check that reports what an environment is
  missing rather than failing opaquely.
- **`make release-check`** -- pre-tag gate over version identity, the Python
  floor across every file that states it, the CHANGELOG entry, and index
  availability on both real PyPI and TestPyPI, each probe carrying a positive
  control so an unreachable index cannot read as a free filename.
- **Token-acquisition guide** in [GET-STARTED.md](GET-STARTED.md) for Bluesky,
  LinkedIn, Google, Notion and GitHub: which portal, which product, which
  scopes, and where the value goes. Flows that could not be verified are marked
  as unverified rather than guessed.
- `.env` is now actually loaded (`zeo_core.config.load_dotenv_file`). It was
  documented as the home for secrets but inert.

### Changed
- **Python floor raised to 3.14** (was 3.13). This is a **breaking change**: an
  environment on 3.13 will no longer resolve new releases. It aligns zeocore with
  sovereign-agent 1.1.0, which already requires 3.14. Verified before landing --
  the full suite passes on 3.14 with the same 2954 tests and no behavioural
  difference, and mypy is clean across 295 source files at the new floor.

- **Credential files move out of the working directory** to an OS-appropriate
  per-user location via `platformdirs`. A legacy credential is migrated once,
  with an explicit notice; differing contents in both locations refuses and
  instructs rather than guessing. Credential writes are `0600` and atomic.
- OAuth credentials are now validated against **granted** scopes, not requested
  ones. A cached token missing a scope previously reported valid and failed at
  the first API call.

### Fixed

- `drive` and `calendar` resolved configuration in `__init__`, so constructing
  either from a directory with no config file raised before a caller could
  supply one. Both now defer to `initialize()`, matching `mail`. All five
  integrations construct from a fresh directory.
- `google_credentials.json` and `google_client_secret.json` were not gitignored
  while the settings YAML was -- the settings file was protected and the live
  token was not.

## [0.5.0] - 2026-08-23

ZeoCore is the canonical capability-authoring and capability-contract
library for the Zero Employee ecosystem. Sovereign Agent is **not** moved
into this package. Short announcement: [RELEASE_NOTES.md](RELEASE_NOTES.md).
Adopter path: install → [`examples/capability_authoring.py`](examples/capability_authoring.py)
→ [GET-STARTED Capabilities](GET-STARTED.md#capabilities) → HTTP/MCP examples.

### Added

- Canonical capability contracts: `CapabilityId` (`namespace.name@semver`),
  `CapabilityDefinition`, `CapabilityManifest`, `CapabilityEffects` /
  `ConcurrencyMode` / `EffectKind`, `CapabilityRequirements`, typed
  `RequestGuard` / `GuardResult`, `CapabilityInvocationRecord` (digests +
  redaction, not execution receipts), and `CapabilityOutcome` layered on
  the existing three-way `CapabilityStatus`. See
  [`src/zeo_core/contracts/README.md`](src/zeo_core/contracts/README.md).
- `@capability` function authoring, `CapabilityRegistry` (including
  `zeo_core.capabilities` entry points), `invoke_sync` / `invoke_async`,
  `tool_to_capability`, and `register_capability_operation` for HTTP/MCP.
- OpenAI function-tool projection in `zeo_core.adapters.llm_tools`
  (`project_openai_tool`): refuses unsupported JSON Schema instead of
  silently weakening it.
- Representative catalog capabilities under `zeo_core.tools.catalog`
  (local add, filesystem checksum, GitHub file read, calendar create,
  pandoc markdown-to-docx). Reference implementations, not a public API.
- Sovereign consumption contract pack (`zeo_core.contract_pack`, tests
  under `tests/contract_pack/`) with no `sovereign_agent` import.
- Runnable examples: `capability_authoring.py`, `capability_guards.py`,
  `tool_to_capability.py`, `llm_tools_usage.py`, rewritten
  `http_adapter_usage.py`. Tutorial:
  [`docs/tutorials/capability-authoring.md`](docs/tutorials/capability-authoring.md).
- Symbol audit and replacement-readiness reports under `docs/reports/`
  (maintainer/ecosystem, not end-user docs).

### Changed

- Package version aligned at `0.5.0` (`pyproject.toml` and
  `zeo_core.__version__`). Contracts module version is `1.1.0`.
- Ecosystem Python ruling recorded: forthcoming Zero Employee releases
  align on Python 3.13; ZeoCore does not restore 3.12 CI.
- README / GET-STARTED / `llms.txt` lead with `@capability` as the
  canonical authoring surface. `BaseZeoTool` remains fully supported.

### Compatibility

- Existing `BaseZeoTool.run()`, HTTP, and MCP `register_tool` paths remain.
- `CapabilityResult.ok` / `.skip` / `.fail` set default outcomes so
  current tools need no changes. `.unavailable()` is the skip path for
  missing declared services.
- Effects are declarations, not authorization. Human approval is not a
  capability result state.

### Named, not fixed

- `zeo_core.tools.compat.sovereign_style_capability` exists for
  keyword-argument functions migrating from Sovereign Agent style. It is
  transitional, not canonical. No deletion timeline in this release.
- LLM provider `chat()` still does not parse `tool_use` response blocks
  into structured `tool_calls` (request-side tool-calling works). Same
  gap as 0.4.x; `adapters.llm_tools` projects *ZeoCore* capabilities
  outward and does not close that inbound parse gap.

## [0.4.0] - 2026-08-21

### Changed

- **`.env` adopted as the documented home for secrets** (RULING-356). Not a
  runtime change — zeocore already merges `os.environ` over YAML config, so
  this changes where a byte is typed, not what the process does with it. It
  is adopted because YAML config files get committed by default and `.env`
  files do not, and this library's stated audience is junior: defaults are
  the whole product for that audience. A new [`.env.example`](.env.example)
  ships at the repo root with placeholder values for `NOTION_TOKEN`,
  `GITHUB_TOKEN`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY`. `.env` is not
  loaded on import — that stays the caller's shell/tooling, a deliberate
  fence (see "Named, not fixed" below).
- **`GET-STARTED.md` fixed — the highest-severity item in this release.**
  The documented "Creating Custom Configuration" walkthrough was teaching a
  junior to construct a secret inline (`MyAppConfig(api_key="your-api-key")`)
  and then `.model_dump()` it into a `ZeoConfig.custom` dict that gets
  persisted to a committed YAML file. A second, previously-uncited instance
  in the same file's "Configuration File Format" section embedded a literal
  `api_key: "your-api-key"` in a worked YAML example. Both rewritten to read
  the secret from the environment and keep it out of anything persisted to
  disk. `README.md` was swept for the same shape; no instance found there.
- **`.gitignore` now anchors the two repo-relative default config
  locations** (`/zeo_config.yaml`, `/config/zeo_config.yaml`, per
  `config/loader.py:52-57`). Defense in depth: a junior who ignores the docs
  and types a secret into config YAML is now stopped by a rule rather than
  their own attention. Verified with `git check-ignore -v` / `git add
  --dry-run`, not by reading the pattern.

### Fixed

- **Credential files are now written `0600`.** A `mode: int | None = None`
  parameter threads through the fs write chain
  (`write_json`/`write_yaml`/`write_bytes`/`write_text`, both the atomic and
  non-atomic branches); default is preserve-current, so general-purpose
  infra (pandoc/jupytext/gmail output, etc.) is unchanged. The three
  credential writers (`google/auth.py`, `notion/auth.py`, `github/auth.py`)
  now pass `mode=0o600` explicitly. The real defect: `_atomic_write`
  preserved a pre-existing loose file mode forever, with no path to tighten
  it, on every subsequent write. New files were already born `0600` under
  this repo's own umask probe — this is not a create-at-`0644` fix; that
  defect does not exist.

### Named, not fixed (recorded per circle-of-control)

- **The env-writeback opt-in** (`zeo_core.integrations.llms.config.
  LLMConfigProvider._setup_environment_variables`, `llms/config.py:228-273`)
  copies an `api_key` found in loaded YAML config into `os.environ` when the
  corresponding env var isn't already set. Real, and ranked, but sized this
  round rather than changed: only **one gated production call site**
  reaches it (`prompt/_internal/enhancer.py:27`, itself behind an explicit
  `use_llm` opt-in at its only caller, `prompt/service.py:161`); the other
  construction site, `llms/__init__.py:73`'s `create_integration()`, is dead
  code — `modules/discovery.py` looks only for `create_plugin` and states so
  in its own comment. `get_llm_client` (`llms/registry.py:41`), the
  function GET-STARTED.md's own worked example actually uses, does not go
  through this path at all. Default-OFF for this behaviour is still a
  breaking change; this release only narrows the estimate from an
  `[INFERRED]` broad surface to one enumerated, already-gated site.
- **Typed secret models (`SecretStr`) cannot be applied as-is.** `ZeoConfig`
  (`models.py:100-138`) has no secret-shaped field to type — `integrations`
  and `custom` are both untyped `dict[str, Any]`, and secrets flow through
  them structurally the same as any other config value. Adopting
  `SecretStr` is a design decision about how to shape that dict, not a
  drop-in annotation change, and stays open.
- **Two of the four default config locations are outside any repo-local
  `.gitignore`'s reach.** `config/loader.py:52-57` also names
  `~/.zeo/config.yaml` and `/etc/zeo/config.yaml`; a secret typed into
  either is not covered by this release's `.gitignore` patterns or by any
  mechanism in this repo, because neither path is inside a git working
  tree. `.env` remains the documented, gitignore-independent mitigation
  regardless of which config location is in use.

## [0.3.0] - 2026-08-20

### Added

- **Google Calendar integration** (`zeo_core.integrations.google.calendar`,
  `zeocore[calendar]` extra): read (`list_calendars`, `get_calendar`,
  `list_events` with date-range filtering, `get_event`) and write
  (`create_event`, `update_event`, `delete_event`) surface, reusing the
  same OAuth (`InstalledAppFlow` + local-server) flow and
  `GoogleAuthProvider`/`GoogleConfigProvider` the Drive and Gmail
  integrations already use — no new auth or config mechanism. See
  [`examples/calendar_usage.py`](examples/calendar_usage.py) and
  [`docs/tutorials/calendar-integration.md`](docs/tutorials/calendar-integration.md).
- `zeo_core.integrations.google` now also re-exports `GoogleCalendarService`,
  `Calendar`, and `CalendarEvent` at the shallow path (alongside the
  existing `GoogleDriveService`/`GoogleMailService` re-exports).

### Named, not fixed (recorded per circle-of-control)

- `zeo_core.integrations.google.drive.operations/*` (`list_files.py`,
  `upload.py`, `download.py`, `folder.py`, `permissions.py`) is dead code:
  confirmed by a repo-wide grep that nothing outside drive's own test
  suite imports it — `drive/service.py`'s public methods duplicate the
  same logic inline rather than calling into `operations/`. Pre-existing,
  not introduced by the Calendar integration (which does not replicate
  this pattern), and out of this work's own scope to fix.
- `GoogleDriveService`/`GoogleCalendarService`'s real `integration_id`
  property (`"googledrive"`/`"googlecalendar"`, derived from
  `name.lower().replace(" ", ".")`) does not match the dotted
  `pyproject.toml` entry-point table key (`"google.drive"`/
  `"google.calendar"`) — the two are different strings by a pre-existing
  pattern (confirmed identical for `GoogleDriveService`), not a defect
  introduced by the Calendar integration.

## [0.2.0] - 2026-08-20

### BREAKING

- **Python floor raised from `>=3.10` to `>=3.13`.** Python 3.10 is inside
  its own final support window (security-only since October 2023, full
  end-of-life October 2026); 3.13 is also the version the `ffmpeg` extra
  (below) needed to resolve at all (`ffmpeg-zeo` requires Python >=3.12).
  If you're pinned to an older interpreter, stay on a pre-`0.2.0` release
  of this package; otherwise upgrade before installing. CI's version
  matrix was trimmed from `["3.10", "3.11", "3.12", "3.13"]` to
  `["3.13"]` to match — the dropped legs could not pass regardless of
  this change (3.10/3.11 fail `ffmpeg-zeo`'s own floor; keeping them past
  the bump would test versions the package no longer claims to support).

### Added

- **MCP server adapter** (`zeo_core.adapters.mcp`, `zeocore[mcp]`
  extra): expose zeocore tools to Claude Code, Cursor, and other
  MCP-native coding agents. Any `BaseZeoTool` becomes MCP-callable with
  **zero MCP-specific code** from the tool author — `register_tool()`
  mechanically derives the MCP tool definition from the tool's own
  `run(request, ctx)` type hint and registers it into the same
  `OperationRegistry` `zeo_core.adapters.http` already reads, so one
  registration reaches both adapters. See
  [`examples/mcp_server_usage.py`](examples/mcp_server_usage.py) and
  GET-STARTED.md's "Exposing Tools as an MCP Server" section.
- **Notion integration** (`zeo_core.integrations.notion`, `zeocore[notion]`
  extra): full read (`get_page`, `list_page_blocks`, `search`,
  `get_database`, `query_database`) and write (`create_page`,
  `create_database_entry`, `update_page`, `append_blocks`) surface,
  bearer-integration-token auth (Notion's own model, not OAuth). Handles
  the `notion-client` SDK's 2025-09-03 database→data-source model change
  transparently — callers still pass a `database_id`. See
  [`examples/notion_usage.py`](examples/notion_usage.py).
- **Jupytext integration** (`zeo_core.integrations.jupytext`,
  `zeocore[jupytext]` extra): `script_to_notebook()` (percent-format
  `.py` → `.ipynb`, matching how `quackslides` uses jupytext today) and
  `notebook_to_script()` (its natural inverse). See
  [`examples/jupytext_usage.py`](examples/jupytext_usage.py).
- **FFmpeg integration** (`zeo_core.integrations.ffmpeg`,
  `zeocore[ffmpeg]` extra): wraps the org's own `ffmpeg-zeo` PyPI package
  (not the raw `ffmpeg` binary directly). `probe()`, `convert()`,
  `transcode_h264()`, `extract_audio()`, `thumbnail()`. See
  [`examples/ffmpeg_usage.py`](examples/ffmpeg_usage.py).
- `LLMOptions.cache_system_prompt`: marks the system prompt cacheable via
  Anthropic's `cache_control: ephemeral` breakpoints. Provider-agnostic
  field on the shared `LLMProviderProtocol`; a no-op on providers without
  caching support (OpenAI, Ollama).
- `llms.txt`: a condensed package summary for coding agents / LLM context
  windows.

### Fixed

- Anthropic client's default model updated (both `clients/anthropic.py`
  and `config.py`'s `AnthropicConfig.default_model`) — the previous
  default, `claude-3-opus-20240229`, was retired 2026-01-05.
- Real Anthropic-shaped tool-use request passthrough:
  `LLMOptions.tools` is converted to Anthropic's flat
  `{name, description, input_schema}` shape and reaches the real request.
  **Known gap, not yet closed**: response-side tool-call extraction is
  incomplete — `chat()` only reads `response.content[0].text`, so a
  `tool_use` response block is not parsed into structured output today.
  See GET-STARTED.md's LLM providers section for the full detail.
- `zeo_core.integrations.google` now re-exports `GoogleDriveService` and
  `GoogleMailService` at the shallow path (previously only reachable at
  `.google.drive`/`.google.mail`).
- `CapabilityResult` re-exported from the top-level `zeo_core` package
  (previously only reachable via `zeo_core.contracts`).
- GET-STARTED.md's configuration quick-start corrected (an unmarked
  placeholder path was being presented as a runnable example); a real,
  runnable [`examples/config_usage.py`](examples/config_usage.py) added.
- `Makefile`'s `install-all` target was missing the `mcp`/`mcp-dev`
  extras, silently skipping the MCP adapter's own dependencies on a
  plain `make install-all` — fixed.
- An order-dependent test failure
  (`test_configure_logger_attaches_real_file_handler`, passed in
  isolation, failed under full-suite ordering) was root-caused to a
  `functools.lru_cache`-backed filesystem-service singleton leaking a
  stale working directory across tests, and fixed by scoping
  `cache_clear()` around the affected test.

### Research (no code shipped)

- Database integration (BigQuery, Supabase, SQLite) was evaluated and
  **not built** this round: BigQuery dropped (zero consumers anywhere in
  the org); Supabase held (its only real consumer, `profrod-site`, is
  TypeScript and architecturally unreachable from this Python package);
  SQLite judged buildable but not urgent (`quackresearch` already
  self-serves with hand-rolled `sqlite3`). The `zeo_core.integrations.database`
  package directories exist as empty stubs — not yet implemented.

### Changed

- `CapabilityResult.machine_message` and `CapabilityError.code` now accept
  `ZEO_<AREA>_<DETAIL>` (new preferred prefix, matching the `zeo_core`
  package name) and `ZC_<AREA>_<DETAIL>` (short-form alias), in addition to
  the legacy `QC_<AREA>_<DETAIL>` inherited from the pre-extraction
  `quack_core` package. Previously only `QC_` was accepted, which
  contradicted this file's own 0.1.0 "full mechanical rename" claim below
  (see that entry's amendment) and had no documented reason for surviving
  the rename. This is a backward-compatible widening, not a breaking
  change: all 0.1.0 code using `QC_*` codes continues to validate
  unchanged. All of this package's own internal call sites
  (`tools/mixins/env_init.py`, `contracts/capabilities/demo/_impl.py`,
  `examples/toolkit_usage.py`, `contracts/EXAMPLES.md`) were migrated to
  `ZEO_*` to lead by example.

## [0.1.0] - 2026-08-17

Initial extraction from the quackverse monorepo as a standalone,
MIT-licensed package.

### Added

- `zeo_core.tools`: a capability-authoring framework (`BaseZeoTool`,
  `ToolContext`, `ZeoToolProtocol`) with optional mixins
  (`IntegrationEnabledMixin`, `LifecycleMixin`, `ToolEnvInitializerMixin`)
  for writing doctrine-compliant tools.
- `zeo_core.contracts`: typed data contracts (`CapabilityResult`, artifact
  and manifest models, common enums and IDs).
- `zeo_core.core`: filesystem operations (`core.fs`), path resolution
  (`core.paths`), a typed error hierarchy (`core.errors`), MIME detection,
  serialization helpers, logging, and an operation registry.
- `zeo_core.config`: YAML/environment-variable configuration loading and
  per-tool configuration models.
- `zeo_core.integrations`: adapters for GitHub, Google Drive, Gmail, LLM
  providers (OpenAI, Anthropic), Notion, and Pandoc.
- `zeo_core.modules`: plugin discovery and explicit-loading registry.
- `zeo_core.prompt`: prompt template selection and enhancement utilities.
- `zeo_core.adapters`: an optional FastAPI-based HTTP adapter for exposing
  tools over a REST API.
- `examples/`: runnable, verified example scripts (`toolkit_usage.py`,
  `minimal_tool.py`, `error_handling.py`).
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1).

### Changed

- Full mechanical rename from the monorepo's `quack_core` package to
  `zeo_core` (PyPI distribution name: `zeocore`), including class names
  (`Quack*` -> `Zeo*`), environment variable prefixes, and config file
  keys. **Amendment (see Unreleased):** this rename did not originally
  extend to the `QC_*` machine-message/error-code prefix enforced by
  `CapabilityResult`/`CapabilityError`'s validators, which kept rejecting
  anything but `QC_` -- contradicting "full" above. Fixed by widening
  those validators to also accept `ZEO_`/`ZC_`, rather than retroactively
  editing this historical entry.
- Fixed a broken version-sourcing path and a production test-detection bug
  found during the extraction (see git history for detail).

### Fixed

- CI now runs the full test suite on every supported Python version
  (3.10-3.13) on real GitHub Actions infrastructure. That surfaced three
  real, previously-invisible cross-version bugs (this package's own
  pre-release local development only ever ran on 3.13), all fixed and
  independently verified across every supported version before release:
  - A `pathlib.Path.__init__`/`__new__` split changed in CPython 3.12;
    an autouse test fixture that monkeypatched `Path.__init__` only was
    crashing the entire test suite on 3.10/3.11 with an `INTERNALERROR`.
    Fixed by coercing on both `__new__` and `__init__`, matching whichever
    one actually does the real work on a given Python version.
  - `unittest.mock`'s dotted-string `@patch(...)` target resolution
    changed in 3.11 (`pkgutil.resolve_name` instead of an eager
    `getattr`-walk). Three call sites depended on the newer resolution
    semantics to reach their intended target; on 3.10 they silently
    patched the wrong object, cascading into shared-state pollution across
    unrelated test files.
  - `typing.Protocol.__instancecheck__`'s structural-membership check
    changed in 3.12 (from plain `hasattr` to `inspect.getattr_static`,
    which does not trigger `MagicMock`'s attribute auto-fabrication).
    Five tests asserting that a bare, unconfigured `MagicMock()` does
    *not* satisfy a runtime-checkable protocol passed on 3.12/3.13 but
    failed on 3.10/3.11. Fixed by constructing the mocks with an explicit
    `spec=`, which is version-stable and expresses the real test intent.

### Removed

- The `BaseZeoToolPlugin` back-compat alias -- unused outside this repo's
  own test suite, and this package has never had a public release, so no
  back-compat was owed for it.

[Unreleased]: https://github.com/zeroemployeeorg/zeocore/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/zeroemployeeorg/zeocore/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/zeroemployeeorg/zeocore/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/zeroemployeeorg/zeocore/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/zeroemployeeorg/zeocore/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/zeroemployeeorg/zeocore/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/zeroemployeeorg/zeocore/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/zeroemployeeorg/zeocore/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/zeroemployeeorg/zeocore/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/zeroemployeeorg/zeocore/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/zeroemployeeorg/zeocore/releases/tag/v0.3.0
[0.2.0]: https://github.com/zeroemployeeorg/zeocore/releases/tag/v0.2.0
[0.1.0]: https://github.com/zeroemployeeorg/zeocore/releases/tag/v0.1.0
