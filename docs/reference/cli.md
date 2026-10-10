# The `zeocore` command

`zeocore` lets another service or language use zeocore without importing
Python: the ZEOconnect Broker and WEB, a TypeScript pipeline, a shell
script. Each call reads JSON on stdin and writes one JSON object on stdout.

```console
$ zeocore version
{"cli_protocol": "1", "commands": ["digest", "image", "schema", "validate", "version"], "ok": true, "zeoconnect_protocol": "1", "zeocore": "…"}
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
| `image` | yes | one Nano Banana or Recraft call ([Images](../integrations/images.md)) |

## Rules every command keeps (`cli_protocol` 1)

- **stdout is exactly one JSON object**, followed by a newline. Diagnostics go to
  stderr. Never parse anything else from stdout.
- **Input is strict JSON:** no duplicate keys, no `NaN` or infinities, at most
  1 MiB.
- **Errors never echo input values.** A validation error is its location and type
  only, so nothing secret or personal leaks through an error.
- **Exit status:**
  - 0: done;
  - 2: the command or its input can't be used (an unknown command or schema, or
    input that isn't strict JSON);
  - 3: the command ran and said no (a value that doesn't match its schema, or
    an operation that was refused or failed).

A change to these rules is a new `cli_protocol`, announced like a contract change.

## Schemas

| Name | Contract |
|---|---|
| `connections.normalized-error` | `NormalizedError` |
| `connections.observation-artifact` | `ObservationArtifact` |
| `hosted.artifact-descriptor` | `HostedArtifactDescriptor` |
| `hosted.availability-snapshot` | `AvailabilitySnapshot` |
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
