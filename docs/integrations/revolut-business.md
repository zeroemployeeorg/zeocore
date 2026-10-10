# Revolut Business

Created 2026-09-20; local enrollment added 2026-09-21. Doctrine Rev 17 with the
active operator delegation.

ZeoCore reads a Revolut Business account: list accounts and read one bounded
page of transactions. It never pays, transfers or changes settings.

You can use it **on your own**, with your own Revolut account, on your own
machine, with no hosted service and no database. That is the **local profile**,
described first. Organizations that prefer guided browser enrollment and
managed custody can use the **hosted profile** through ZEOconnect instead.

Profiles are selected explicitly. Nothing falls back from one to the other: a
hosted failure stays a hosted failure and never reaches for local credentials.

**Live behaviour is unverified.** Models, routes and the enrollment flow follow
Revolut's published Business API description and are exercised against offline
fixtures only. No sandbox or production request has been made by this code.

## Local profile: enroll your own account

Install the extra, which adds `cryptography` for the client key and certificate:

```bash
pip install "zeocore[revolut]"
```

Start with [managed environments](environments.md). Configuration, and only
configuration, may come from `.env` or the launcher:

| Variable | Meaning |
|---|---|
| `REVOLUT_ENVIRONMENT` | `sandbox` or `production`. Separate enrollments. |
| `REVOLUT_REDIRECT_URI` | An `https` URL you control or simply own the name of. |
| `REVOLUT_CLIENT_ID` | Issued by Revolut after step 1. Not a secret. |

**No variable holds the private key or a token, and none is read.** Those live
in owner-only files (mode 0600, directory 0700) under the selected managed
environment, or under your per-user configuration directory, in
`revolut/<environment>/`. The store refuses a directory inside a git
repository, a symlink, or a file other users can read.

Revolut issues a client id only after you register a certificate by hand, so
enrollment is three explicit steps:

```bash
# 1. Create your key and certificate, then upload the certificate at
#    Revolut Business -> Settings -> APIs -> Business API, with the same
#    redirect URI. Revolut shows you a client id.
python -m zeo_core.integrations.revolut.local setup --redirect-uri https://example.com/revolut

# 2. Record the client id. Open the URL it prints and approve READ access.
python -m zeo_core.integrations.revolut.local authorize --client-id <client id>

# 3. Revolut redirects your browser to the redirect URI with a one-time code.
#    The page need not exist: copy the address bar and paste it here. Input is
#    hidden so the code does not land in your shell history.
python -m zeo_core.integrations.revolut.local complete

python -m zeo_core.integrations.revolut.local status     # never prints a secret
python -m zeo_core.integrations.revolut.local accounts   # first live check
```

The same steps are available in Python as
`LocalRevolutEnrollment(environment).setup(...)`, `.authorize(...)`,
`.complete(...)`. The JWT `iss` Revolut checks is the host of your redirect URI.

### Reading

```python
from zeo_core.integrations.revolut import RevolutEnvironment, TransactionQuery
from zeo_core.integrations.revolut.local import LocalRevolutEnrollment

revolut = LocalRevolutEnrollment(RevolutEnvironment.SANDBOX)
accounts = revolut.read(lambda client: client.list_accounts())
page = revolut.read(
    lambda client: client.list_transactions(TransactionQuery(from_=since, count=200))
)
```

`read` runs one logical operation with **at most one token refresh and one
repeat**, including the proactive refresh shortly before expiry. Do not wrap it
in your own retry.

### Local does not mean race-free

Two of your scripts can share these files, and a process can die after Revolut
has processed a refresh. Revolut invalidates the previous access token when it
refreshes, so a lost answer leaves the client unable to say whether its stored
token still works. The client therefore:

- takes an exclusive file lock for the whole operation, so two local processes
  never refresh at once. POSIX only;
- writes a marker **durably before** a refresh request is sent, and removes it
  only once a definite answer is stored;
- treats a marker found later as an **unknown outcome**. That state is blocked:
  nothing refreshes again, however long you wait, because waiting proves
  nothing about what Revolut did. The stored token keeps serving reads while
  Revolut accepts it;
- reports what it observed, never a diagnosis. `OUTCOME_UNKNOWN`,
  `GRANT_REFUSED` and `TOKEN_REJECTED` do not establish that you revoked
  consent. `UNAVAILABLE` means nothing reached Revolut and nothing changed.
- holds the file lock only on your machine. It says nothing about what Revolut
  is still processing.

**What a fresh consent does and does not do.** Running `authorize` and
`complete` again on the same registration gives you new tokens. It answers a
`GRANT_REFUSED` or a `TOKEN_REJECTED`, which are definite answers, and clears
them. It does **not** clear an unknown outcome: your consent authorizes new
tokens, it does not settle what Revolut did with the request whose answer was
lost. The new access token serves reads while it is valid and accepted;
refreshing stays blocked.

**There is no qualified recovery from an unknown outcome yet.** You can start a
new registration with `setup --new-key`, which discards every stored token,
creates a new key and certificate, and needs a new client id from Revolut. That
is a fresh local binding. It is **not established** that a new key and client
id, or deleting the old certificate in Revolut Business, isolates the new
registration from a late refresh of the old one; this has not been verified
against Revolut and this guide does not promise it. The new enrollment records
`follows_unresolved_refresh` so `status` keeps saying so. Removing local files
alone removes nothing at Revolut.

**An enrollment belongs to its environment.** Files written for sandbox are
refused for production, and the reverse, with `ENVIRONMENT_MISMATCH`, before
any client is built or any credential is sent. This holds even if you copy a
directory or pass a store explicitly. Moving to another environment is an
explicit `setup --new-key` there, never a reinterpretation of existing secrets.

## Hosted profile: ZEOconnect

`RevolutBusinessClient` holds no credentials of its own. A credential owner
constructs it with a current access token:

| Concern | Owner |
|---|---|
| Typed operations, fixed-origin transport, normalization | The client, in both profiles |
| Key, certificate, consent, exchange, refresh, files | The local profile above, for one person |
| Guided enrollment, custody, coordinated refresh across workers, revocation | ZEOconnect, for the hosted profile |
| Paging loop, checkpoints, deduplication, evidence storage, matching | The consuming application |

Hosted enrollment is not yet admitted; the setup catalogue reports its state.

From 0.14.0, `HostedRevolutBusinessClient` makes the same two reads through
ZEOconnect, with the credential held by the Broker (Broker contract `1.0.0`
§6). It is read-only and never makes a payment. Pass it a
`HostedConnectionClient` and the connection's id:

```python
from zeo_core.integrations.revolut import (
    HostedRevolutBusinessClient,
    TransactionQuery,
)

revolut = HostedRevolutBusinessClient(client, connection_id=connection_id)
accounts = revolut.list_accounts()
page = revolut.list_transactions(TransactionQuery(from_=since, count=200))
```

Paging works as with the direct client. Two wire rules apply: the query's
start field is sent as `from`, and every amount must arrive as an exact
decimal string. A result that carries a JSON number is refused, not rounded. A
Broker stop raises `HostedStoppedError`.
Error codes from the client are sanitized categories, not diagnoses:
`AUTHENTICATION` (401) does not establish that a token expired, and `ACCESS`
(403) does not establish that a connection was revoked.

## Using the client directly

```python
from pydantic import SecretStr
from zeo_core.integrations.revolut import (
    RevolutBusinessClient,
    RevolutEnvironment,
    TransactionQuery,
)

client = RevolutBusinessClient(token, environment=RevolutEnvironment.SANDBOX)
try:
    accounts = client.list_accounts()
    page = client.list_transactions(TransactionQuery(from_=since, count=200))
    while page.next_to is not None:
        page = client.list_transactions(
            TransactionQuery(from_=since, to=page.next_to, count=200)
        )
finally:
    client.close()
```

`token` is a `SecretStr` supplied by the credential owner. `environment`
selects one of two fixed origins; a caller can never supply a provider URL.

## Paging contract

Revolut's published description pages transactions by a `from`/`to` window on
`created_at` with a `count`, and has no cursor token. `list_transactions`
returns one page. When the page is full, `next_to` is the oldest `created_at`
in it, computed without assuming any provider ordering; pass it as the next `to`.

**A page is an observation, not a snapshot.** The same window and cursor can
return different records later: transactions change state, and new ones can
appear inside a window already read. Each page carries `observed_at`.

- Key stored transactions by `id`. A projection with a later `updated_at`
  replaces the earlier one; keep earlier observations as history, do not
  discard them as duplicates.
- Adjacent pages can overlap at `next_to`. Whether Revolut treats `to` as
  inclusive, how it orders results and how it breaks `created_at` ties are
  **unverified against the live API**; replacement by `id` is what makes the
  loop safe under any of those answers.
- Progress is detected, not assumed: a full page whose oldest instant does not
  move below the requested `to` raises `PAGINATION_STALLED`. Narrow the window
  or raise `count` (maximum 1000).
- Any error means the window was **not** fully read. Record an incomplete sync
  with the last good cursor; never report completion.
- There is no collect-everything helper.

## Size bounds

A bounded count does not bound bytes, so both sides are bounded explicitly.

- The upstream body is streamed and abandoned once it passes
  `MAX_UPSTREAM_BYTES` (8 MiB), before any parsing. Error bodies are never read.
- The normalized, serialized result must fit `MAX_RESULT_BYTES` (768 KiB, below
  the 1 MiB hosted JSON limit with room for its envelope).

Either breach raises `RESPONSE_TOO_LARGE` and returns nothing. Results are never
truncated: a silently shortened page would lose evidence needed for matching.
Lower `count` or narrow the window and read again.

## Normalization

Results carry `normalization_version = "revolut-business-read-1"`.

- Amounts are parsed from JSON numbers directly into `Decimal`; they never pass
  through a binary float.
- Provider fields not declared by the models are dropped. From `card`, only the
  card `id` is kept: card number, holder name and phone are discarded.
- Provider enumerations (`type`, `state`, `account_type`) are kept as bounded
  lowercase tokens, so a new provider value does not fail a whole page.

This output is normalized application data. It is not raw provider evidence
and must not be stored or labelled as raw.

## What the credential tests do and do not show

Only declared, typed fields leave the client, errors carry fixed messages with
no provider body, URL or validation detail, and representations of the client,
transport and errors omit the token. Tests plant a canary credential and check
those outputs, malformed and undecodable responses, and debug-level logs.

That shows the **supplied credential** does not leak through the exercised
paths. It does not show that provider text can never contain some other
token-like value: free-text fields such as `reference` are passed through as
data. As defence in depth, a response containing the request's own token is
refused; this is not a general secret scanner.

## Test account track

Use a Revolut Business **sandbox** account with `REVOLUT_ENVIRONMENT=sandbox`.
The sandbox has its own certificate registration, client id and consent, fully
separate from production, and its own directory of private files.

## Production account track

Set `REVOLUT_ENVIRONMENT=production` and enroll again from step 1: a production
enrollment shares nothing with a sandbox one. Approve READ access only. If your
Revolut account enforces an IP allowlist, register the address you read from.

## Bounded E2E

Not yet run. After enrolling, `python -m zeo_core.integrations.revolut.local
accounts` is the first live check. Then: list accounts, read one page of
at most ten transactions from a one-day window, and confirm that amounts match
the Revolut web interface to the minor unit. Record the environment, date and
outcome alongside the change that claims it.
