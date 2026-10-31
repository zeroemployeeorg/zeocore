# Intervention cards

`zeo_core.contracts.ui.intervention_cards.InterventionCardPayload` validates the
`DataRef.payload` of a `widget.upsert` whose component is `intervention_card`
(first slice). One closed model per `situation`:

| `situation` | Shows |
|---|---|
| `work_location` | where claimed work lives (repo, branch, commit, PR, local only) |
| `checkpoint` | a requested checkpoint, its delivery and response |
| `blocker` | a reported blocker, since when, its release condition |
| `decision` | one ask: `decide`, `review`, `do` or `session`, with exactly that ask's fields |
| `request_status` | delivery, response, outcome and an explicit deadline |
| `outcome` | `verified` (with evidence and the verification ref), `failed` or `cancelled` |
| `project_checkpoint` | one project: seat answers, last product vs administrative progress, wait, local-only work, per-source freshness |
| `source_item` | a read-only item as its source states it: its own id, the digest of the bytes read, project, label, title, state, next action and the line to copy. No work id, no counter, no action |
| `message` | a read-only message to the operator: the sender's id, the digest of the message bytes, who sent it and when, a one-line subject, a plain-text body, optional links, and optionally how a reply renders |
| `approval` | a read-only approval request: its own id, the digest of exactly what is approved, the operation, the preview shown, its status and times. The decision stays with the service that owns it |

## Rules that hold everywhere

- A model is closed and strict: an unknown key is an error, nothing is coerced.
- An optional key is **absent** when unknown, never `null`.
- Text refuses markup and control, format and separator characters.
- A card computes nothing: no age, lateness or order. The owning service supplies them.
- `intervention_ref`, when present, equals `work_ref.work_id`. A `wi_` id must be a
  canonical ULID; a pre-existing source id is accepted otherwise.
- An action names exactly one id from the closed list of its `route`
  (`intervention`, `reserved` or `task`). The route is descriptive: the backend
  decides authority from the action id and the signed-in person.
- A disabled action states its reason. A project card offers only
  `request_checkpoint`, `request_unblock` and `explain_blocker`.
- A review ask carries exactly two image descriptors (metadata only, at most 8 MiB
  each and 16 MiB together, 4096 on a side). A `do` ask may carry bounded result
  slots and display-only pinned texts whose `sha256` is of the exact UTF-8 bytes.

## The Core profile of `widget.upsert`

`intervention_card` is the fifth component of Core's own widget profile
(`contracts/zeocore-v1/ui.widget-upsert.schema.json`). The four components of the
locked briefing spec (`flight_status`, `weather_hourly`, `arrival_timeline`,
`market_watchlist`) are unchanged and are listed separately as `LOCKED_COMPONENTS`;
the conformance tests run against exactly those four. The locked schema is closed, so
a consumer validating against it refuses `intervention_card`, and that is correct.

Core emits no card itself. A service that sends `widget.upsert` events to a consumer
pinned to the locked four must not send it this component: the card belongs to boards
that read the Core profile.

## For a front end

A front end reads the card as JSON and does not import Python.

- **Schema:** `contracts/zeocore-v1/ui.intervention-card.schema.json`, also printed by `zeocore schema ui.intervention-card`. It is JSON Schema 2020-12 and closed: no unknown keys. A key that may be absent is optional and has no `null` branch: an explicit `null` is refused by the schema and by the models.
- **TypeScript types:** `contracts/zeocore-v1/ui.intervention-card.d.ts`, generated from the schema. `InterventionCard` is a union discriminated by `situation`. A key that may be absent is optional in the type and is never `null` on the wire.
- **What the types cannot say:** rules that tie fields together, such as `intervention_ref` equal to `work_ref.work_id`, are checked by the validator. A front end shows what it is given and does not rely on the types for those rules.
- **Examples:** `tests/test_contracts/fixtures/intervention_cards/valid/*.dataref.json` holds one valid card for each situation and shape. All data is fake.

Both files are regenerated from the model; a test fails when they drift.

## Messages, replies and approvals

`message` and `approval` are read-only like `source_item`: no work id, no counter, no action.

- **Plain text only.** A `subject` or `label` is one line of at most 200 code points and refuses `<`, `>`, control characters and format characters (bidi, zero-width, line and paragraph separators). A `body` or an approval `preview` is plain text of at most 4096 UTF-8 bytes (a preview at most 2000 characters), line feed allowed, and refuses control and format characters. It may hold `<` and `>`, because a body or a preview can quote an address in angle brackets; none of it is markup. Nothing is truncated or repaired: an invalid card is refused. A consumer shows every string as text, never as markup, and counts one-line fields by code points and long text by bytes.
- **Links are data.** `links` holds at most 5 entries of a one-line `label` and an `https` `url`; a body cannot carry a link.
- **A reply names how an answer renders, not where it goes.** `reply` is absent when no answer is wanted. `mode` is `text` (with `max_length`, at most 4096) or `choice` (2 to 4 options with distinct ids); the other mode's keys are refused. `answers_digest` must equal the message's own `source_digest`, so an answer to a changed message can be refused by the service that owns the reply slot. The card sends nothing.
- **A message id is the sender's own id**, never a work id (`wi_` and `todo_` ids are refused). A message does not reply to itself.
- **An approval is display only.** `approval_id` is `apr_` and a canonical lowercase uuid (groups of 8, 4, 4, 4 and 12 hex digits). `request_digest` is the digest of exactly what is approved, so the owner can refuse a stale or changed request. `decided_at` is present exactly when the status is not `pending`. The card carries no identity of the person and no audit.

### The schema is shape only

`ui.intervention-card.schema.json` describes the shape of a card: keys, types, enums, patterns and sizes in characters. It cannot express the model's other rules, so a consumer that validates with the schema alone accepts a card the models refuse. Those rules are: control and format characters in text, the byte length of long text, `wi_` and `todo_` ids in a message, `decided_at` against `status`, `answers_digest` against the message's `source_digest`, a message that replies to itself, and the cross-field rules of the older situations. Validate with the models, or with a validator that implements the same rules.
