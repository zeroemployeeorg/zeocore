import { useId, type ReactNode } from "react";
import { validateCalendar } from "./calendar-validator.js";
import type {
  CalendarView as CalendarViewPayload,
  CommitmentItem,
  DateRevision,
} from "./generated/calendar.js";

export interface CommitmentCalendarProps {
  value: unknown;
}
const label = (s: string) => s.replaceAll("_", " ");
/** A signed number of seconds exactly as published; nothing is converted, clamped or rounded. */
const seconds = (n: number) => `${n > 0 ? "+" : ""}${n} s`;
const yesNo = (b?: boolean) =>
  b === undefined ? "Not published" : b ? "Yes" : "No";
function Time({ value, missing }: { value?: string; missing: string }) {
  return value ? <time dateTime={value}>{value}</time> : <>{missing}</>;
}
function Row({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="zc-row">
      <dt>{name}</dt>
      <dd>{children}</dd>
    </div>
  );
}
function Revision({ r }: { r: DateRevision }) {
  return (
    <li>
      <strong>{r.kind}</strong> at <Time value={r.at_utc} missing="" /> by{" "}
      {r.by}
      {(r.from_due_utc || r.to_due_utc) && (
        <>
          : <Time value={r.from_due_utc} missing="no earlier time" /> to{" "}
          <Time value={r.to_due_utc} missing="no later time" />
        </>
      )}
      {r.reason && <> ({r.reason})</>}
    </li>
  );
}
function Item({ item }: { item: CommitmentItem }) {
  const revisions = item.revisions ?? [];
  return (
    <li className="zc-cal-item">
      <h4>{item.what}</h4>
      <p className="zc-cal-badges">
        <span className="zc-cal-badge">Status: {item.status}</span>
        <span className="zc-cal-badge">
          Lateness: {label(item.lateness.state)}
        </span>
        {!item.counted && <span className="zc-cal-badge">Not counted</span>}
      </p>
      <p className="zc-muted">
        {item.lateness.policy}
        {item.lateness.reason && <> {item.lateness.reason}</>}
      </p>
      <dl>
        <Row name="Owner">{item.accountable_owner}</Row>
        <Row name="Original due">
          <Time value={item.original_due_utc} missing="Not published" />
        </Row>
        <Row name="Current forecast">
          <Time value={item.forecast_due_utc} missing="Not published" />
        </Row>
        {item.status === "done" && (
          <Row name="Finished">
            <Time
              value={item.done_at_utc}
              missing="Finish time not published"
            />
          </Row>
        )}
        <Row name="Seconds against original">
          {item.seconds_vs_original === undefined
            ? "Not published"
            : seconds(item.seconds_vs_original)}
        </Row>
        <Row name="Seconds against forecast">
          {item.seconds_vs_forecast === undefined
            ? "Not published"
            : seconds(item.seconds_vs_forecast)}
        </Row>
        {item.counted && (
          <>
            <Row name="Past original deadline">
              {yesNo(item.past_original_deadline)}
            </Row>
            <Row name="Past current forecast">{yesNo(item.past_forecast)}</Row>
            <Row name="Owner reports late">
              {yesNo(item.owner_reports_late)}
            </Row>
          </>
        )}
        {item.dependency && <Row name="Dependency">{item.dependency}</Row>}
        {item.conditional && (
          <Row name="Conditional">
            {item.conditional.activation}; decided by{" "}
            {item.conditional.decided_by};{" "}
            {item.conditional.activated_at_utc ? (
              <>
                activated{" "}
                <Time value={item.conditional.activated_at_utc} missing="" />
              </>
            ) : (
              "not yet activated"
            )}
          </Row>
        )}
        {item.next_action && <Row name="Next action">{item.next_action}</Row>}
        <Row name="Source id">
          <code>{item.item_ref}</code> (the owner's id, not a work id)
        </Row>
        <Row name="Work identity">
          {item.work_ref ? (
            <code>{item.work_ref.work_id}</code>
          ) : (
            "Unresolved: the owner registered no work id"
          )}
        </Row>
      </dl>
      <details>
        <summary>Date changes ({revisions.length})</summary>
        {revisions.length > 0 && (
          <ul>
            {revisions.map((r, i) => (
              <Revision key={i} r={r} />
            ))}
          </ul>
        )}
        <p className="zc-muted">
          {item.revisions_complete
            ? "This history is complete."
            : "Earlier changes may be missing from this history."}
        </p>
      </details>
    </li>
  );
}
function Items({ items }: { items: CommitmentItem[] }) {
  return (
    <ul className="zc-cal-items">
      {items.map((item) => (
        <Item key={item.row_key} item={item} />
      ))}
    </ul>
  );
}
function Calendar({ view }: { view: CalendarViewPayload }) {
  const id = useId();
  const { report, paging, counts } = view;
  const first = paging.offset + 1,
    last = paging.offset + paging.returned_rows;
  const undated = view.undated ?? [];
  return (
    <section className="zc-card zc-cal" aria-labelledby={id}>
      <header>
        <span className="zc-eyebrow">Commitment calendar</span>
        <h2 id={id}>
          {view.scope === "today" ? "Today" : "Seven days"} from{" "}
          {view.start_date}
        </h2>
        <p className="zc-muted">
          Zone {view.zone}. Times are shown exactly as the owner published them
          {view.time_precision === "minute" ? ", to the minute" : ""}.
        </p>
      </header>
      <p>
        Coverage: <strong>{view.coverage}</strong>
        {(view.coverage === "unavailable" || view.coverage === "unknown") &&
          ". No commitments are shown because the source is not available."}
      </p>
      <p>
        {paging.returned_rows === 0
          ? `No rows on this page of ${paging.eligible_rows} eligible`
          : `Rows ${first} to ${last} of ${paging.eligible_rows} eligible`}{" "}
        (the snapshot has {paging.total_source_rows}; {paging.undated_rows}{" "}
        undated).{" "}
        {paging.next_cursor ? (
          <>
            Next page <code>{paging.next_cursor}</code>.
          </>
        ) : (
          "This is the last page."
        )}
      </p>
      {counts && (
        <dl className="zc-cal-counts">
          {Object.entries(counts).map(([k, n]) => (
            <Row key={k} name={label(k)}>
              {n}
            </Row>
          ))}
        </dl>
      )}
      {view.days.map((day) => (
        <section key={day.date} className="zc-cal-day">
          <h3>
            <time dateTime={day.date}>{day.date}</time>
          </h3>
          {day.items?.length ? (
            <Items items={day.items} />
          ) : (
            <p className="zc-muted">No commitments published for this day.</p>
          )}
        </section>
      ))}
      {undated.length > 0 && (
        <section className="zc-cal-day">
          <h3>No due time</h3>
          <Items items={undated} />
        </section>
      )}
      <details>
        <summary>Sources and snapshot</summary>
        <dl>
          <Row name="Generated">
            <Time value={report.generated_at} missing="" />
          </Row>
          <Row name="Source observed">
            <Time value={report.source_observed_at} missing="" />
          </Row>
          <Row name="Snapshot digest">
            <code>{report.snapshot_digest}</code>
          </Row>
          <Row name="Source digest prefix">
            <code>{report.source_sha256_prefix}</code>
          </Row>
        </dl>
        {view.sources.map((s) => (
          <dl key={s.source}>
            <Row name={s.source}>{s.state}</Row>
            <Row name="Observed">
              <Time value={s.observed_at} missing="Unknown" />
            </Row>
            <Row name="Collected">
              <Time value={s.collected_at} missing="Unknown" />
            </Row>
            {s.reason && <Row name="Reason">{s.reason}</Row>}
          </dl>
        ))}
        {view.source_coverage && (
          <dl>
            <Row name="Owners covered">
              {view.source_coverage.owners_covered.join(", ") || "None"}
            </Row>
            <Row name="Seats without commitments">
              {view.source_coverage.registered_seats_without_commitments.join(
                ", ",
              ) || "None"}
            </Row>
            {view.source_coverage.source_not_covered && (
              <Row name="Not covered">
                {view.source_coverage.source_not_covered}
              </Row>
            )}
          </dl>
        )}
      </details>
    </section>
  );
}
/** Validates before rendering. Read-only: it computes no lateness, countdown, sort or zone arithmetic. */
export function CommitmentCalendar(props: CommitmentCalendarProps) {
  const result = validateCalendar(props.value);
  if (!result.ok)
    return (
      <section className="zc-card zc-cal zc-unavailable" role="status">
        <h2>Unavailable</h2>
        <p>This calendar could not be validated.</p>
      </section>
    );
  return <Calendar view={result.view} />;
}
