import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { validateCalendar, parseCalendar } from "../dist/calendar-validator.js";
import { CommitmentCalendar } from "../dist/calendar.js";
const root = new URL(
  "../../../tests/test_contracts/fixtures/calendar_view/",
  import.meta.url,
);
const load = (p) => JSON.parse(readFileSync(p, "utf8"));
const validDir = new URL("valid/", root),
  negDir = new URL("negative/", root);
const validFiles = readdirSync(validDir).filter((f) =>
  f.endsWith(".dataref.json"),
);
const render = (value) =>
  renderToStaticMarkup(createElement(CommitmentCalendar, { value }));
const today = load(new URL("calendar-today.dataref.json", validDir)).payload;

for (const f of validFiles) {
  const { payload } = load(new URL(f, validDir));
  test("valid + render " + f, () => {
    assert.equal(validateCalendar(payload).ok, true);
    assert.equal(parseCalendar(JSON.stringify(payload)).ok, true);
    const html = render(payload);
    assert.ok(!html.includes("could not be validated"));
    assert.ok(!/<script|onclick=|onerror=/i.test(html));
    assert.ok(
      html.includes("Original due") && html.includes("Current forecast"),
    );
  });
}
test("there are six valid and eighty-nine negative fixtures", () => {
  assert.equal(validFiles.length, 6);
  assert.equal(
    readdirSync(negDir).filter((f) => f.endsWith(".json")).length,
    89,
  );
});
for (const f of readdirSync(negDir).filter((f) => f.endsWith(".json"))) {
  const { payload, expect } = load(new URL(f, negDir));
  test("fixture parity " + f, () =>
    assert.equal(validateCalendar(payload).ok, expect === "accepted"),
  );
}
test("revisions are ordered by instant, not by string", () => {
  const cases = [
    [["2026-10-29T23:55:00Z", "2026-10-29T23:55:00.1Z"], true],
    [["2026-10-29T23:55:00.1Z", "2026-10-29T23:55:00Z"], false],
    [["2026-10-30T00:30:00+01:00", "2026-10-29T23:55:00Z"], true],
    [["2026-10-29T23:55:00Z", "2026-10-30T00:30:00+01:00"], false],
    [["2026-10-30T00:55:00+01:00", "2026-10-29T23:55:00Z"], true],
    [["2026-10-29T23:55:00Z", "2026-10-30T00:55:00+01:00"], true],
  ];
  for (const [times, accepted] of cases) {
    const payload = structuredClone(today);
    const item = payload.days[0].items[0];
    item.revisions = times.map((at_utc) => ({ ...item.revisions[0], at_utc }));
    assert.equal(validateCalendar(payload).ok, accepted, times.join(" "));
  }
});
test("duplicate keys, BOM, trailing data, floats for integers, huge input refused", () => {
  const text = JSON.stringify(today);
  for (const input of [
    text.replace('"scope":', '"scope":"today","scope":'),
    "﻿" + text,
    text + "{}",
    new Uint8Array([0xff]),
    " ".repeat(1048577),
    text.replace(/"page_size":\d+/, '"page_size":8.0'),
    text.replace(/"page_size":\d+/, '"page_size":8e0'),
  ])
    assert.equal(parseCalendar(input).ok, false);
});
test("a lone surrogate and an unsafe integer are refused", () => {
  const surrogate = structuredClone(today);
  surrogate.days[0].items[0].what = "x\ud800y";
  assert.equal(validateCalendar(surrogate).ok, false);
  const big = structuredClone(today);
  big.paging.page_size = 1e300;
  assert.equal(validateCalendar(big).ok, false);
});
test("snapshots are detached, frozen, accessor free", () => {
  const x = structuredClone(today),
    result = validateCalendar(x);
  assert.equal(result.ok, true);
  x.days[0].items[0].what = "changed";
  assert.notEqual(result.view.days[0].items[0].what, "changed");
  assert.ok(Object.isFrozen(result.view.days[0].items[0].lateness));
  assert.equal(
    validateCalendar({
      get scope() {
        throw new Error("must not execute");
      },
    }).ok,
    false,
  );
});
test("markup in a field is refused and the calendar renders unavailable", () => {
  const x = structuredClone(today);
  x.days[0].items[0].what = "<img src=x onerror=alert(1)>";
  assert.equal(validateCalendar(x).ok, false);
  const html = render(x);
  assert.match(html, /could not be validated/);
  assert.ok(!html.includes("<img"));
  assert.match(render({ scope: "execute" }), /Unavailable/);
});
test("a done item without a finish time says so and is not hidden", () => {
  const x = structuredClone(today);
  const done = x.days
    .flatMap((d) => d.items ?? [])
    .concat(x.undated ?? [])
    .find((i) => i.status === "done");
  assert.ok(done, "a fixture has a done item");
  delete done.done_at_utc;
  assert.equal(validateCalendar(x).ok, true);
  assert.match(render(x), /Finish time not published/);
});
test("the original deadline and the forecast stay apart and nothing is computed", () => {
  const html = render(today);
  const item = today.days[0].items.find((i) => i.original_due_utc);
  assert.ok(html.includes(item.original_due_utc));
  assert.ok(!/ago|from now|overdue by|in \d+ (hours|minutes)/i.test(html));
});
