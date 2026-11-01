import shape from "./generated/calendar-shape.js";
import { contract } from "./generated/calendar-contract.js";
import type { CalendarView } from "./generated/calendar.js";
import {
  assert,
  has,
  instantMicros,
  localDay,
  makeValidator,
  type Contract,
  type Obj,
} from "./core.js";

export type CalendarValidation =
  | { ok: true; view: CalendarView }
  | { ok: false; reason: string };
export const MAX_CALENDAR_BYTES = 1048576;

/** The rules that tie one commitment's fields together, as the Python model states them. */
function item(v: Obj): void {
  assert(v.status === "done" || !has(v, "done_at_utc"));
  const pending =
    has(v, "conditional") && !has(v.conditional, "activated_at_utc");
  assert(v.counted === (v.status !== "dropped" && !pending));
  assert((v.lateness.state === "not_counted") === !v.counted);
  assert((v.canonical_identity === "resolved") === has(v, "work_ref"));
  // Compare the instants, not the published strings: offsets and fractions differ.
  const times: bigint[] = (v.revisions ?? []).map((r: Obj) =>
    instantMicros(r.at_utc),
  );
  assert(times.every((t, i) => i === 0 || times[i - 1] <= t));
  assert(v.status !== "late" || v.owner_reports_late === true);
  if (!v.counted) {
    assert(!has(v, "past_original_deadline") && !has(v, "owner_reports_late"));
    return;
  }
  assert(has(v, "past_original_deadline") && has(v, "owner_reports_late"));
  // The feed's policy has three causes: past the original deadline, past the current forecast,
  // or reported late by the owner. A missing forecast cause is never read as false.
  const causes: boolean[] = [v.past_original_deadline, v.owner_reports_late];
  const difference: number | undefined = v.seconds_vs_forecast;
  if (v.status !== "done" && difference !== undefined) {
    assert(!(v.lateness.state === "not_late" && difference > 0));
    assert(!(v.past_forecast === true && difference <= 0));
    assert(!(v.past_forecast === false && difference > 0));
  }
  if (has(v, "past_forecast")) {
    causes.push(v.past_forecast);
    assert((v.lateness.state === "late") === causes.some(Boolean));
    return;
  }
  assert(!(v.lateness.state === "not_late" && causes.some(Boolean)));
  assert(
    !(
      v.lateness.state === "late" &&
      !causes.some(Boolean) &&
      (v.status === "done" || difference === undefined || difference <= 0)
    ),
  );
}

function paging(v: Obj): void {
  assert(v.returned_rows <= v.page_size);
  assert(
    v.offset + v.returned_rows <= v.eligible_rows &&
      v.eligible_rows <= v.total_source_rows,
  );
  assert(v.undated_rows <= v.eligible_rows);
  assert(!(v.returned_rows === 0 && v.offset < v.eligible_rows));
  assert(!has(v, "cursor") === (v.offset === 0));
  assert(
    !has(v, "next_cursor") === (v.offset + v.returned_rows === v.eligible_rows),
  );
}

function view(v: Obj): void {
  const expected = v.scope === "today" ? 1 : 7;
  const wholeViewMissing =
    v.coverage === "unavailable" || v.coverage === "unknown";
  const undated: Obj[] = v.undated ?? [];
  assert(wholeViewMissing || v.days.length === expected);
  assert(!(wholeViewMissing && (v.days.length || undated.length)));
  const dates: number[] = v.days.map((d: Obj) => localDay(d.date));
  assert(!dates.length || dates[0] === localDay(v.start_date));
  assert(dates.every((d, i) => i === 0 || d - dates[i - 1] === 1));
  const dated: Obj[] = v.days.flatMap((d: Obj) => d.items ?? []);
  const refs = [...dated, ...undated].map((i) => i.item_ref);
  assert(new Set(refs).size === refs.length);
  assert(
    dated.every(
      (i) => has(i, "original_due_utc") || has(i, "forecast_due_utc"),
    ),
  );
  assert(
    undated.every(
      (i) => !has(i, "original_due_utc") && !has(i, "forecast_due_utc"),
    ),
  );
  assert(v.paging.returned_rows === dated.length + undated.length);
  assert(undated.length <= v.paging.undated_rows);
  assert(v.paging.total_source_rows === v.report.source_items);
}

function semantic(name: string, v: Obj): void {
  switch (name) {
    case "CommitmentItem":
      item(v);
      break;
    case "Counts":
      assert(v.live === v.rows - v.dropped - v.conditional_not_activated);
      break;
    case "Paging":
      paging(v);
      break;
    case "CalendarView":
      view(v);
      break;
  }
}

const checker = makeValidator<CalendarView>({
  contract: contract as unknown as Contract,
  shape,
  semantic,
  maxBytes: MAX_CALENDAR_BYTES,
  noun: "Calendar",
});
const asView = (r: ReturnType<typeof checker.validate>): CalendarValidation =>
  r.ok ? { ok: true, view: r.value } : r;
export function validateCalendar(value: unknown): CalendarValidation {
  return asView(checker.validate(value));
}
/** Strict byte ingress. Duplicate keys are refused before JSON.parse loses them. */
export function parseCalendar(input: string | Uint8Array): CalendarValidation {
  return asView(checker.parse(input));
}
