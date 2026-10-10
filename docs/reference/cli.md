# The `zeocore` command

`zeocore` lets another service or language use zeocore without importing
Python: the ZEOconnect Broker and WEB, a TypeScript pipeline, a shell
script. Each call reads JSON on stdin and writes JSON lines on stdout, the
result last.

```console
$ zeocore version
{"cli_protocol": "1", "commands": ["artifact", "connections", "digest", "invoke", "login", "logout", "schema", "validate", "version", "whoami"], "ok": true, "zeoconnect_protocol": "1", "zeocore": "…"}
$ zeocore schema list
$ zeocore schema hosted.operation-request
$ echo '{"code": "RATE_LIMITED", "message": "slow down"}' | zeocore validate connections.normalized-error
$ echo '{"b": 1, "a": [1, 2]}' | zeocore digest
{"ok": true, "sha256": "sha256:…"}
```

## Commands

| Command | Reads stdin | Answers |
|---|---|---|
| `version` | no | the release, `cli_protocol`, the ZEOconnect protocol, the commands |
| `schema list` | no | the stable schema names |
| `schema <name>` | no | that contract as JSON Schema 2020-12 |
| `validate <name>` | yes | `{"ok": true, "value": …}`, the normalized value, or `{"ok": false, "errors": [{"loc": […], "type": …}]}` |
| `digest` | yes | the sha256 of the value's RFC 8785 canonical bytes |
| `login`, `logout`, `whoami`, `connections`, `invoke`, `artifact get` | see below | the ZEOconnect client |

## The ZEOconnect client

Apps reach the ZEOconnect Broker through these commands instead of writing a
Broker client of their own. An app never holds a provider token. The machine
holds only that app's own device grant, in the macOS Keychain.

| Command | Reads stdin | Does |
|---|---|---|
| `login [--device-name N]` | no | Pairs this device. Emits `{"event": "pair", "verification_url", "user_code", "expires_at"}`, waits for a person to approve in ZEOconnect, then stores the grant. |
| `logout` | no | Revokes the grant and deletes it. |
| `whoami` | no | Says whether this profile is paired, with its device id and expiry. Local only: no network, never a token. |
| `connections [--service S]` | no | Lists the grant's connections. |
| `invoke <operation_id> --connection C [--occurrence L] [--wait-approval SECONDS]` | the arguments object | Runs one operation and answers the Broker's response plus `request_key` and `retry`. |
| `artifact get --out PATH` | an artifact descriptor | Downloads an output to a new file and checks its size and digest. |

Every client command takes `--profile P`, or reads `ZEOCORE_PROFILE`, to choose
the grant, so one machine can run several apps, each paired on its own. A
profile is attribution, not a security boundary: all profiles of one macOS user
can read each other. With no usable Keychain, a command fails closed with exit
12. There is no plaintext fallback.

The origin is the production Broker unless `ZEOCONNECT_URL` names another.
Development origins also need `ZEOCONNECT_DEVELOPMENT=1`.

### Invoking

- **The idempotency key is the request.** `request_key` is derived from the
  connection, the operation, the canonical arguments and `--occurrence`, so
  running the same invocation again is an exact replay of the stored outcome
  and never a second act. Use a new `--occurrence` for a deliberate new act.
- **`--wait-approval SECONDS`** handles an effect that needs a person's
  approval. It emits `{"event": "approval", "approval_url", "request_key"}`
  once, then asks again with the same key every 5 s until the answer is no
  longer `approval_required`, or the time runs out (exit 10).
- **`retry`** says what another attempt needs:
  - `same_request`: ask again as it is;
  - `new_occurrence`: the failure is recorded, so a retry is a new act;
  - `none`: change the request.
- **Exit codes:**
  - 0 `confirmed`;
  - 10 `approval_required`;
  - 11 still in flight, or ZEOconnect is unavailable;
  - 12 not paired, or no session store;
  - 13 `ambiguous`, or no answer arrived (asking again with the same request is
    safe);
  - 20 refused, `failed_safe` or stopped.

## Rules every command keeps (`cli_protocol` 1)

- **stdout is JSON lines and nothing else.** Events (a pairing code, an approval
  link, waiting) carry an `event` key. The result is always the last line: it
  carries `ok` and never `event`. A command with no events prints exactly one
  line. Diagnostics go to stderr.
- **Input is strict JSON:** no duplicate keys, no `NaN` or infinities, at most
  1 MiB.
- **Errors never echo input.** A validation error is its location and type only.
  A location keeps declared field names and list positions; any other key (inside
  a free-form object such as `arguments`, or an unknown extra) shows as `*`.
- **Exit status** is one family, the same numbers as the YouTube publish
  command's:

  | Code | Meaning |
  |---|---|
  | 0 | done |
  | 2 | invalid input or command; nothing was sent |
  | 10 | approval required |
  | 11 | waiting: try the same request later |
  | 12 | not paired: run `zeocore login` |
  | 13 | ambiguous; never retried by the command |
  | 20 | held or refused, including a value that doesn't match its schema |
  | 1 | internal error in zeocore: `{"ok": false, "outcome": "internal"}`, no detail. For an operation, treat it like 13 |

A change to these rules is a new `cli_protocol`, announced like a contract change.

## Schemas

| Name | Contract |
|---|---|
| `connections.normalized-error` | `NormalizedError` |
| `connections.observation-artifact` | `ObservationArtifact` |
| `hosted.artifact-descriptor` | `HostedArtifactDescriptor` |
| `hosted.availability-snapshot` | `AvailabilitySnapshot` |
| `hosted.connection-summary` | one `GET /v1/connections` entry (`HostedConnectionWire`) |
| `hosted.operation-request` | `HostedOperationRequest` |
| `hosted.operation-response` | `HostedOperationResponse` |
| `revolut.account` | Revolut `Account` |
| `revolut.transaction-page` | Revolut `TransactionPage` |

- **A name is a promise.** It keeps naming the same contract until a major
  version. New names are additive.
- **The files are committed** in the repository under `contracts/zeocore-v1/`
  as `<name>.schema.json`, for callers that vendor files and run no zeocore at
  all. A test fails if a model and its committed schema drift.
- **Not listed yet:** Revolut's `TransactionQuery`. Its wire field is `from`
  (Broker contract §6), but the model's field is `from_` with no alias, so its
  schema would name the wrong field.

## Digests

`digest` is the sha256 of the value's [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785)
(JCS) canonical bytes, prefixed `sha256:`. Two parties that each canonicalize
the same JSON value get the same digest without sharing code.
