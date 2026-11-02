// Generated from ui.calendar-view.schema.json. Do not edit by hand.
// Schema: https://zeocore.zeo.ac/contracts/zeocore-v1/ui.calendar-view.schema.json
// Rules that tie fields together (for example intervention_ref equal to
// work_ref.work_id) are enforced by the validator, not by these types.

export interface CalendarDay {
  date: string;
  items?: CommitmentItem[];
}

export interface CalendarSource {
  source: string;
  state: "ok" | "partial" | "unavailable" | "stale" | "unknown";
  observed_at?: string;
  collected_at?: string;
  reason?: string;
}

export interface CommitmentItem {
  row_key: string;
  item_ref: string;
  canonical_identity: "resolved" | "unresolved";
  work_ref?: WorkRef;
  what: string;
  accountable_owner: string;
  status: "open" | "late" | "blocked" | "provisional" | "done" | "dropped";
  original_due_utc?: string;
  forecast_due_utc?: string;
  done_at_utc?: string;
  seconds_vs_original?: number;
  seconds_vs_forecast?: number;
  past_original_deadline?: boolean;
  past_forecast?: boolean;
  owner_reports_late?: boolean;
  lateness: Lateness;
  revisions?: DateRevision[];
  revisions_complete: boolean;
  dependency?: string;
  conditional?: ConditionalActivation;
  next_action?: string;
  counted: boolean;
}

export interface ConditionalActivation {
  activation: string;
  decided_by: string;
  activated_at_utc?: string;
}

export interface Counts {
  rows: number;
  live: number;
  finished: number;
  open: number;
  dropped: number;
  conditional_not_activated: number;
}

export interface DateRevision {
  at_utc: string;
  by: string;
  from_due_utc?: string;
  to_due_utc?: string;
  kind: "earlier" | "postponement" | "correction" | "scope change";
  reason?: string;
}

export interface Lateness {
  state: "late" | "not_late" | "not_counted";
  policy: string;
  reason?: string;
}

export interface Paging {
  page_size: number;
  offset: number;
  total_source_rows: number;
  eligible_rows: number;
  returned_rows: number;
  undated_rows: number;
  cursor?: string;
  next_cursor?: string;
}

export interface SnapshotRef {
  schema_name: "zeo-calendar-view/2";
  generated_at: string;
  source_schema: "zeo-commitments/2";
  source_observed_at: string;
  source_sha256_prefix: string;
  source_items: number;
  snapshot_digest: string;
}

export interface SourceCoverage {
  owners_covered: string[];
  registered_seats_without_commitments: string[];
  source_not_covered?: string;
}

export interface WorkRef {
  work_id: string;
  source_ref: string;
  revision: string;
}

export interface CalendarView {
  scope: "today" | "seven_day";
  zone: string;
  start_date: string;
  time_precision: "second" | "minute";
  report: SnapshotRef;
  counts?: Counts;
  source_coverage?: SourceCoverage;
  paging: Paging;
  days: CalendarDay[];
  undated?: CommitmentItem[];
  sources: CalendarSource[];
  coverage: "ok" | "partial" | "unavailable" | "stale" | "unknown";
}
