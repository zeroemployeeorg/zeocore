# The commitment calendar

`zeo_core.contracts.ui.calendar_views.CalendarView` validates the `DataRef.payload` of a `widget.upsert` whose component is `calendar_view`. It is a read-only snapshot of the commitments a team has made, as the owner of that feed publishes them. Core carries it and computes nothing: no lateness, no countdown, no duration, no clock or time-zone arithmetic, no sort.

## What an item keeps apart

- **Dates.** `original_due_utc`, `forecast_due_utc` and `done_at_utc` are three facts, each of which may be absent. A forecast that moves never replaces the original deadline, and an item is never given an interval it was not given.
- **Finish.** `done_at_utc` is present only on a done item, and a done item may have none: the owner did not give a finish time, so it is unknown. It is not invented, and the item is not dropped or hidden.
- **Status and lateness.** The status is the feed's word (`open`, `late`, `blocked`, `provisional`, `done`, `dropped`). The lateness verdict (`late`, `not_late`, `not_counted`) carries the feed's policy text. The model checks that a verdict agrees with the causes the feed published (past the original deadline, past the current forecast, reported late by the owner) and never reads a cause the feed has not published as false.
- **Signed differences.** `seconds_vs_original` and `seconds_vs_forecast` are as of the snapshot; positive is late, negative is early or time left. A negative value is valid.
- **History.** `revisions` lists date changes in time order, and `revisions_complete` says whether earlier changes are missing. A missing history is never read as no history.
- **Identity.** `item_ref` is the feed's own legacy id, unique in the snapshot. The canonical work identity is `unresolved` unless the owner registered a mapping that supplies a `work_ref`. A row key, a title or a position never resolves it.
- **Counted.** `counted` is false exactly for a dropped item and for a conditional item that is still waiting for its activation. A dropped item stays visible.

## Days, undated items and the window

Which of an item's dates places it in a day is the owner's rule, stated by the owner; Core does not choose. The card shows every date an item carries, whichever day it sits in, and an empty day means no row was placed there, not that nothing is due that day: another item may carry that date as its forecast or finish.

A view is a Today or seven-day civil window. A view that exists has exactly one or seven dated buckets, each possibly empty, on consecutive local dates starting at `start_date`; an empty bucket is a quiet day, not a missing one. An item with neither an original nor a forecast due time is listed under `undated`, never under a day. When the whole view is `unavailable` or `unknown`, it has no days and no undated items.

## Pages: a cut is never silent

A snapshot can hold more rows than a page. A page is a stable, source-ordered cut of one immutable snapshot, and `paging` says exactly where it sits: `report.snapshot_digest` names the snapshot (the same on every page), `total_source_rows` the whole snapshot, `eligible_rows` the rows in the selected window plus the undated rows, `offset` where this page starts, `returned_rows` how many it holds, `undated_rows` the undated rows in the whole snapshot, `cursor` this page (absent on the first) and `next_cursor` the next (absent on the last). The model checks the arithmetic: a page with rows after it returns at least one row (it must make progress), the undated rows shown are within `undated_rows`, and `returned_rows` is the number of items on the page. A changed snapshot is a new first page; a cursor of another snapshot is the owner's to refuse. The owner supplies every count; a viewer derives none.

## Text and the schema

Every string is plain text and refuses control and format characters and `<` and `>`; a viewer shows each one as text, never as markup. The published schema, `contracts/zeocore-v1/ui.calendar-view.schema.json`, describes shape only and has no `null` branch; the model rules above (lateness against its causes, counts, page arithmetic, consecutive dates, identity) are not in it, so validate with the models or a validator that implements the same rules. `contracts/zeocore-v1/ui.calendar-view.d.ts` holds the TypeScript types generated from the schema.

## The React renderer

`packages/react` also renders this component: `CommitmentCalendar` takes a payload, validates it and shows it read only; `parseCalendar` and `validateCalendar` are the byte and object ingress, the same strict checks as for cards (duplicate keys, byte order marks, invalid UTF-8, trailing data, floats where the contract has an integer, lone surrogates and integers beyond the safe range are refused). The schema and the rules above are generated into the package by `scripts/generate_react_contract.py`, with a drift test, and the model rules that the schema cannot express (lateness against its causes, counts, page arithmetic, consecutive dates, item identity, revision order by instant) are implemented in the validator and tested against every fixture, valid and negative.

The renderer computes nothing. It shows the original due time, the current forecast and the finish time as three separate facts, each exactly as published; a missing time reads "Not published" and a done item without a finish time says so; signed differences are shown as published seconds; the owner's lateness verdict is shown with its policy text; the date changes are listed with a note when the history is incomplete; the source id is labelled as the owner's id and the canonical work identity as unresolved unless a work reference is present; and the page line says which rows of how many are shown and whether a next page exists. A payload that does not validate renders as unavailable, never as partial content.
