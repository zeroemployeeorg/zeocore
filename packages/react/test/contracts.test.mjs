import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { validateCard, parseCard } from "../dist/validator.js";
import { Card, supportedSituations } from "../dist/index.js";
const root = new URL(
  "../../../tests/test_contracts/fixtures/intervention_cards/",
  import.meta.url,
);
const load = (p) => JSON.parse(readFileSync(p, "utf8"));
const validDir = new URL("valid/", root),
  negDir = new URL("negative/", root);
const situations = new Set();
for (const f of readdirSync(validDir).filter((f) =>
  f.endsWith(".dataref.json"),
)) {
  const { payload } = load(new URL(f, validDir));
  situations.add(payload.situation);
  test("valid + render " + f, () => {
    assert.equal(validateCard(payload).ok, true);
    assert.equal(parseCard(JSON.stringify(payload)).ok, true);
    const html = renderToStaticMarkup(createElement(Card, { value: payload }));
    assert.ok(!html.includes("This card could not be validated"));
    assert.ok(!/<script|onclick=|onerror=/i.test(html));
  });
}
test("registry covers every situation", () =>
  assert.deepEqual([...situations].sort(), [...supportedSituations].sort()));
for (const f of readdirSync(negDir).filter((f) => f.endsWith(".json"))) {
  const { payload, expect } = load(new URL(f, negDir));
  test("fixture parity " + f, () =>
    assert.equal(validateCard(payload).ok, expect === "accepted"),
  );
}
const base = load(new URL("message-text-reply.dataref.json", validDir)).payload;
test("duplicate keys, BOM, invalid UTF8, trailing data, huge input refused", () => {
  for (const input of [
    '{"situation":"message","situation":"message"}',
    "\ufeff" + JSON.stringify(base),
    JSON.stringify(base) + "{}",
    new Uint8Array([0xff]),
    " ".repeat(1048577),
  ])
    assert.equal(parseCard(input).ok, false);
});
test("snapshots are detached, frozen, accessor free", () => {
  const x = structuredClone(base),
    result = validateCard(x);
  assert.equal(result.ok, true);
  x.body = "changed";
  assert.notEqual(result.card.body, x.body);
  assert.ok(Object.isFrozen(result.card.reply));
  assert.equal(
    validateCard({
      get situation() {
        throw new Error("must not execute");
      },
    }).ok,
    false,
  );
});
test("unknown cards render unavailable, approvals have no buttons", () => {
  assert.match(
    renderToStaticMarkup(
      createElement(Card, { value: { situation: "execute" } }),
    ),
    /Unavailable/,
  );
  const value = load(
    new URL("approval-pending.dataref.json", validDir),
  ).payload;
  assert.ok(
    !renderToStaticMarkup(
      createElement(Card, {
        value,
        interaction: { enabled: true },
        onIntent: () => {},
      }),
    ).includes("<button"),
  );
});

test("wire integers do not accept floating-point JSON tokens", () => {
  const text = JSON.stringify(base);
  for (const token of ["8.0", "8e0", "0.8e1"]) {
    const changed = text.replace(/"max_length":\d+/, '"max_length":' + token);
    assert.equal(parseCard(changed).ok, false);
  }
});
test("structured task results enforce field, row and aggregate limits", async () => {
  const { validResults } = await import("../dist/results.js");
  const card = load(
    new URL("decision-do-database.dataref.json", validDir),
  ).payload;
  const values = {};
  for (const slot of card.result_slots ?? [])
    values[slot.slot_id] =
      slot.value_type === "confirm"
        ? false
        : slot.value_type === "timestamp"
          ? "2026-10-31T10:00:00Z"
          : "ok";
  assert.equal(validResults(card, values), true);
  const text = card.result_slots.find((s) => s.value_type === "text");
  if (text) {
    assert.equal(
      validResults(card, {
        ...values,
        [text.slot_id]: "😀".repeat(text.max_utf8_bytes),
      }),
      false,
    );
  }
  assert.equal(validResults(card, {}), false);
});
