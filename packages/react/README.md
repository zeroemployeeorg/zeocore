# @zeocore/react

Data-only React components for a host application and custom Mantle front ends. Ten closed
situations share the Python contract; no credentials, transport, approval engine,
model invocation, durable store or automatic URL fetch is included.

## Use

Node 22 or later, React 19. From the repository root:

```sh
npm ci --prefix packages/react
npm test --prefix packages/react
npm run gallery --prefix packages/react
```

Build a local, versioned consumer artifact with `npm pack` inside this directory.
Install the resulting tarball in the host application and pin its integrity in the consumer lock.
This package is marked private to prevent accidental registry publication. It is
not released by the Python publisher; npm publication requires its own reviewed
release workflow. No package has been published by this change.

```tsx
import {Card, parseCard} from '@zeocore/react';
import '@zeocore/react/styles.css';

const checked = parseCard(receivedBytes);
return <Card value={checked.ok ? checked.card : undefined} />;
```

Use `parseCard` at byte ingress: it refuses duplicate keys, invalid UTF-8, BOMs,
trailing input, excessive nesting and values over 1 MiB. `validateCard` accepts
already-decoded plain JSON data, enforces the schema plus Python cross-field
rules, and returns a detached, recursively frozen snapshot. It cannot detect
keys lost by somebody else's JSON parser. No input value is included in errors.

## The commitment calendar

`CommitmentCalendar` renders the read-only `calendar_view` payload. It has no controls and no intents:

```tsx
import {CommitmentCalendar, parseCalendar} from '@zeocore/react';

const checked = parseCalendar(receivedBytes);
return <CommitmentCalendar value={checked.ok ? checked.view : undefined} />;
```

It computes no lateness, countdown, sort or zone arithmetic: every time is shown as the owner published it, and a payload that fails `validateCalendar` renders as unavailable.

## Interaction is an intent, never authority

All controls are disabled by default. To enable them, the host must explicitly
provide both `interaction={{enabled: true}}` and `onIntent`. That flag represents
the host's current result after reading the source and checking identity, expiry,
revision/digest and capability. A renderer cannot perform those checks itself.

The callback carries the action and exact validated card, or a reply reference,
answers digest and selected text/option. Decisions have bounded choice/integer
controls; `do` tasks collect typed result fields with per-field, row and aggregate
byte bounds. The callback does **not** complete a task or approve a request.

Before dispatch the owning service must re-read current policy and source state,
recheck the authenticated person and exact binding, and enforce idempotency. The
card's route and enabled flag are untrusted presentation data. Pending/busy state,
failures and successful completion are supplied by the host. The library never
optimistically removes a card. Changed card data resets all draft answers.

Approval cards are always display-only, including when interaction is enabled.
Review-image descriptors show metadata; actual media delivery requires the host's
authenticated media surface. The component never fetches arbitrary URLs. Source
links navigate only after the user clicks, with no referrer or opener.

## Ephemeral lifecycle

The host owns a current snapshot and renders a card for each stable source/widget
identity. Remove it when the source ends the situation, update it when the source
changes. Durable records stay with Agenda, Runtime, Messenger or the service that owns the decision.
Use the existing Python `WidgetBoard` for tool-turn replacement and stale revision
handling. Do not create a second browser database or infer completion from a reply.
`zeo.ui-result/v1` remains the bounded read-only tool-result envelope; it is not
silently renamed to the intervention payload. Adapt at the existing
`widget.upsert`/`DataRef` boundary, validating the envelope in its owning service.

## Validation and generation

`python scripts/generate_react_contract.py` regenerates the published schema,
TypeScript declarations and browser validation metadata. Python tests detect drift.
The build compiles Ajv JSON Schema 2020-12 checks to static JavaScript, so the
browser needs no runtime compiler or unsafe-eval CSP permission. It adds semantic checks for
cross-field rules. Unicode 16 unassigned ranges and field validators are generated
from Python, not the browser's Unicode version. An unknown Python field rule fails
closed. The shared corpus runs against Python, browser validation and React SSR.
JSON Schema alone remains a structural contract; it cannot check pinned-text hashes
or every relationship. Do not substitute raw Ajv validation for `validateCard`.

Dependencies: React is a peer, as requested. Ajv supplies the build-time standard schema
engine (esbuild bundles its tiny runtime helpers); @noble/hashes verifies pinned text synchronously without a bespoke crypto
implementation. TypeScript builds declarations, Vite runs the gallery, Prettier
keeps source readable, Playwright and axe-core test browser interaction and
accessibility. All exact development/runtime versions are in package-lock.json.
No dependency was added to the Python distribution for the React library.

## Theme and accessibility

Override `--zc-bg`, `--zc-surface`, `--zc-text`, `--zc-muted`, `--zc-border`,
`--zc-accent`, `--zc-radius`, `--zc-font`, `--zc-focus`. These are design tokens,
not card fields. Components use text nodes, native buttons, labels, fieldsets,
radio groups, disclosure controls and visible focus rings. Controls are usable
by keyboard and at phone width; reduced-motion preferences disable motion.
Cached navigation, PWA installation, routing and offline retention are host-app
responsibilities. Keep authenticated responses out of a generic service-worker
cache. There is no PWA service worker bundled with the library.

Run `npm run test:browser` after `npx playwright install chromium`. This opens a
local fixture gallery only; no live provider or account is used.
