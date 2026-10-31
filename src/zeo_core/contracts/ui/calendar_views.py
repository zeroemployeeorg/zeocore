"""The commitment calendar: the payload of the ``calendar_view`` widget component.

A read-only snapshot of the commitments a team has made, as the owner of that feed
publishes them: each item keeps its ORIGINAL due time, its CURRENT forecast and its
finish time apart (each may be absent), its source-owned status and lateness verdict,
its signed differences, its revision history and whether that history is complete.
Core computes nothing: no lateness, no countdown, no duration, no clock or zone
arithmetic, no sort. An item is never given an interval it was not given, a status
it does not carry or an identity it does not have; the source's own id stays a
legacy id and the canonical work identity stays explicitly unresolved unless the
owner registered a mapping.

A snapshot can hold more rows than a page. A page is a stable, source-ordered cut of
ONE immutable snapshot: the payload says which snapshot (its digest), how many rows
the whole snapshot has, how many are eligible for the selected window, where this
page starts, how many it returns, and the cursor of the next page when there is one.
Nothing is dropped silently, and a changed snapshot is a new first page.

This module imports only the standard library, pydantic and its sibling contracts.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, StrictBool, model_validator

from zeo_core.contracts.ui.intervention_cards import (
    DateTime,
    Digest,
    Ref,
    SeatName,
    SourceRef,
    WorkRef,
    _Closed,
    _Opt,
    _plain,
)

__all__ = [
    "CalendarDay",
    "CalendarView",
    "CommitmentItem",
    "Paging",
]


def _local_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        msg = "not a real calendar date"
        raise ValueError(msg) from None
    if parsed.isoformat() != value:
        msg = "a calendar date is YYYY-MM-DD"
        raise ValueError(msg)
    return value


LocalDate = Annotated[
    str, Field(min_length=10, max_length=10), AfterValidator(_local_date)
]
#: An IANA zone NAME (for example ``Europe/London``). The viewer owns the rules.
Zone = Annotated[
    str, Field(pattern=r"^[A-Za-z]+(?:/[A-Za-z0-9_+-]+){0,2}$", max_length=64)
]
#: A presentation key scoped to one immutable snapshot and a row position. Never a
#: task identity, never a hash of a title.
RowKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:/-]{1,160}$")]
#: One line of plain text of at most 200 code points.
Line = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_plain)]


Note120 = Annotated[str, Field(min_length=1, max_length=120), AfterValidator(_plain)]
Note300 = Annotated[str, Field(min_length=1, max_length=300), AfterValidator(_plain)]
Note400 = Annotated[str, Field(min_length=1, max_length=400), AfterValidator(_plain)]
Count = Annotated[int, Field(ge=0)]

#: The six words observed in the feed; a seventh is a gap in the source's shape.
ItemStatus = Literal["open", "late", "blocked", "provisional", "done", "dropped"]
#: The feed defines four kinds of date change.
RevisionKind = Literal["earlier", "postponement", "correction", "scope change"]
#: ``not_counted`` is neither on time nor late: the item is outside the count.
LatenessState = Literal["late", "not_late", "not_counted"]
CoverageState = Literal["ok", "partial", "unavailable", "stale", "unknown"]
#: The feed's times have minute precision; the seconds written are ``:00``.
TimePrecision = Literal["second", "minute"]


class Lateness(_Opt):
    """The owner's lateness object, passed through with its policy text verbatim."""

    state: LatenessState
    policy: Note400
    reason: Note120 | None = None


class DateRevision(_Opt):
    """One change of a due date as published.

    ``revisions_complete`` false on the item means earlier changes are NOT in the
    list: never infer none.
    """

    at_utc: DateTime
    by: SeatName
    from_due_utc: DateTime | None = None
    to_due_utc: DateTime | None = None
    kind: RevisionKind
    reason: (
        Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_plain)]
        | None
    ) = None


class ConditionalActivation(_Opt):
    activation: Note300
    decided_by: SeatName
    activated_at_utc: DateTime | None = None  # absent: not yet activated


class CommitmentItem(_Opt):
    """One commitment, as the feed publishes it."""

    row_key: RowKey
    item_ref: SourceRef  # the feed's legacy id, unique in the snapshot: NOT a work id
    canonical_identity: Literal["resolved", "unresolved"]
    work_ref: WorkRef | None = None
    what: Line
    accountable_owner: SeatName
    status: ItemStatus
    original_due_utc: DateTime | None = None
    # The CURRENT forecast. It is not the obligation: a moved forecast never replaces
    # the accepted original deadline.
    forecast_due_utc: DateTime | None = None
    # Absent on a done item means the finish time is UNKNOWN: it is not invented, the
    # item is not dropped and the row is not hidden.
    done_at_utc: DateTime | None = None
    # SIGNED differences as of the snapshot. Positive is late, negative is early or
    # time left; a negative value is never clamped or refused.
    seconds_vs_original: Annotated[float, Field(allow_inf_nan=False)] | None = None
    seconds_vs_forecast: Annotated[float, Field(allow_inf_nan=False)] | None = None
    past_original_deadline: StrictBool | None = None
    past_forecast: StrictBool | None = None  # absent until the feed publishes it
    owner_reports_late: StrictBool | None = None
    lateness: Lateness
    revisions: Annotated[list[DateRevision], Field(max_length=16)] = Field(
        default_factory=list
    )
    revisions_complete: StrictBool
    dependency: Note300 | None = None
    conditional: ConditionalActivation | None = None
    next_action: Note300 | None = None
    counted: StrictBool

    @model_validator(mode="after")
    def _facts(self) -> Self:
        if self.status != "done" and self.done_at_utc is not None:
            msg = "only a done item carries a finish time"
            raise ValueError(msg)
        pending = (
            self.conditional is not None and self.conditional.activated_at_utc is None
        )
        if self.counted != (self.status != "dropped" and not pending):
            msg = (
                "counted exactly when the item is not dropped and not a conditional"
                " still waiting for its activation"
            )
            raise ValueError(msg)
        if (self.lateness.state == "not_counted") != (not self.counted):
            msg = "lateness not_counted exactly when the item is not counted"
            raise ValueError(msg)
        if (self.canonical_identity == "resolved") != (self.work_ref is not None):
            msg = "canonical identity is resolved exactly when a work_ref is supplied"
            raise ValueError(msg)
        times = [r.at_utc for r in self.revisions]
        if times != sorted(times):
            msg = "revisions are in time order"
            raise ValueError(msg)
        if self.status == "late" and not self.owner_reports_late:
            msg = "a status late is the owner's report"
            raise ValueError(msg)
        if not self.counted:
            if (
                self.past_original_deadline is not None
                or self.owner_reports_late is not None
            ):
                msg = "a not-counted item has no past-deadline or owner-late verdict"
                raise ValueError(msg)
            return self
        if self.past_original_deadline is None or self.owner_reports_late is None:
            msg = (
                "a counted item states whether it is past its original deadline and"
                " whether its owner reports it late"
            )
            raise ValueError(msg)
        self._lateness_follows_its_causes()
        return self

    def _lateness_follows_its_causes(self) -> None:
        """The feed's policy has three causes: past the original deadline, past the
        current forecast, or reported late by the owner. A missing forecast cause is
        never read as false."""
        causes = [self.past_original_deadline, self.owner_reports_late]
        difference = self.seconds_vs_forecast
        if self.status != "done" and difference is not None:
            if self.lateness.state == "not_late" and difference > 0:
                msg = "not_late while past the current forecast"
                raise ValueError(msg)
            if self.past_forecast is True and difference <= 0:
                msg = "past_forecast true needs a positive seconds_vs_forecast"
                raise ValueError(msg)
            if self.past_forecast is False and difference > 0:
                msg = "past_forecast false contradicts a positive seconds_vs_forecast"
                raise ValueError(msg)
        if self.past_forecast is not None:
            causes.append(self.past_forecast)
            if (self.lateness.state == "late") != any(causes):
                msg = (
                    "late exactly when past the original deadline, past the current"
                    " forecast, or reported late by its owner"
                )
                raise ValueError(msg)
            return
        if self.lateness.state == "not_late" and any(causes):
            msg = "not_late while a published cause says late"
            raise ValueError(msg)
        if (
            self.lateness.state == "late"
            and not any(causes)
            and (self.status == "done" or difference is None or difference <= 0)
        ):
            msg = "late with no published cause needs a positive seconds_vs_forecast"
            raise ValueError(msg)


class SnapshotRef(_Closed):
    """The immutable snapshot every page of a view is cut from."""

    schema_name: Literal["zeo-calendar-view/2"]
    generated_at: DateTime
    source_schema: Literal["zeo-commitments/2"]
    source_observed_at: (
        DateTime  # when the feed was observed: not generation, not fetch
    )
    source_sha256_prefix: Annotated[str, Field(pattern=r"^[0-9a-f]{12}$")]  # a prefix
    source_items: Count
    snapshot_digest: (
        Digest  # sha256 of the full immutable snapshot, the same on every page
    )


class Counts(_Opt):
    """Published aggregates, source-wide. Filtered counts are the viewer's own."""

    rows: Count
    live: Count
    finished: Count
    open: Count
    dropped: Count
    conditional_not_activated: Count

    @model_validator(mode="after")
    def _reconcile(self) -> Self:
        if self.live != self.rows - self.dropped - self.conditional_not_activated:
            msg = "live is rows minus dropped minus conditionals not yet activated"
            raise ValueError(msg)
        return self


class SourceCoverage(_Opt):
    owners_covered: Annotated[list[SeatName], Field(max_length=64)]
    # A gap shown as a gap: no accusation, no invented commitment.
    registered_seats_without_commitments: Annotated[
        list[SeatName], Field(max_length=128)
    ]
    source_not_covered: Note300 | None = None


class CalendarDay(_Closed):
    date: LocalDate
    items: Annotated[list[CommitmentItem], Field(max_length=64)] = Field(
        default_factory=list
    )


class CalendarSource(_Opt):
    source: Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_plain)]
    state: CoverageState
    observed_at: DateTime | None = None
    collected_at: DateTime | None = None
    reason: Note120 | None = None


class Paging(_Opt):
    """Where this page sits in the snapshot. The owner supplies every count."""

    page_size: Annotated[int, Field(ge=1, le=64)]
    offset: Count  # eligible rows before this page, in source order
    total_source_rows: Count  # the whole snapshot
    eligible_rows: Count  # rows in the selected window plus the undated rows
    returned_rows: Count
    undated_rows: Count  # undated rows in the whole snapshot
    cursor: Ref | None = None  # this page; absent on the first page
    next_cursor: Ref | None = None  # the next page; absent on the last page

    @model_validator(mode="after")
    def _adds_up(self) -> Self:
        if self.returned_rows > self.page_size:
            msg = "a page returns at most its page size"
            raise ValueError(msg)
        if not (
            self.offset + self.returned_rows
            <= self.eligible_rows
            <= self.total_source_rows
        ):
            msg = "offset plus returned within eligible within the whole snapshot"
            raise ValueError(msg)
        if self.undated_rows > self.eligible_rows:
            msg = "undated rows are eligible rows"
            raise ValueError(msg)
        if self.returned_rows == 0 and self.offset < self.eligible_rows:
            msg = (
                "a page with rows after it returns at least one row (it must progress)"
            )
            raise ValueError(msg)
        if (self.cursor is None) != (self.offset == 0):
            msg = "a cursor is absent exactly on the first page"
            raise ValueError(msg)
        last = self.offset + self.returned_rows == self.eligible_rows
        if (self.next_cursor is None) != last:
            msg = "a next cursor is absent exactly on the last page"
            raise ValueError(msg)
        return self


class CalendarView(_Opt):
    """The payload of ``calendar_view``: a Today or seven-day civil window."""

    scope: Literal["today", "seven_day"]
    zone: Zone
    start_date: LocalDate
    time_precision: TimePrecision
    report: SnapshotRef
    counts: Counts | None = None
    source_coverage: SourceCoverage | None = None
    paging: Paging
    days: Annotated[list[CalendarDay], Field(max_length=7)]
    undated: Annotated[list[CommitmentItem], Field(max_length=64)] = Field(
        default_factory=list
    )
    sources: Annotated[list[CalendarSource], Field(min_length=1, max_length=12)]
    coverage: CoverageState

    @model_validator(mode="after")
    def _shape(self) -> Self:
        expected = 1 if self.scope == "today" else 7
        whole_view_missing = self.coverage in ("unavailable", "unknown")
        if not whole_view_missing and len(self.days) != expected:
            msg = "a view has exactly 1 (today) or 7 (seven_day) dated buckets"
            raise ValueError(msg)
        if whole_view_missing and (self.days or self.undated):
            msg = (
                "coverage unavailable or unknown is the WHOLE view: no days, no undated"
            )
            raise ValueError(msg)
        dates = [date.fromisoformat(d.date) for d in self.days]
        if dates and dates[0] != date.fromisoformat(self.start_date):
            msg = "the first day is the start date"
            raise ValueError(msg)
        if any((b - a).days != 1 for a, b in zip(dates, dates[1:], strict=False)):
            msg = "consecutive local calendar dates, each exactly the next date"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _items(self) -> Self:
        items = [i for d in self.days for i in d.items] + self.undated
        refs = [i.item_ref for i in items]
        if len(refs) != len(set(refs)):
            msg = "an item appears once in a page (item_ref is unique)"
            raise ValueError(msg)
        if any(
            i.original_due_utc is None and i.forecast_due_utc is None
            for d in self.days
            for i in d.items
        ):
            msg = "an item with neither an original nor a forecast due time is undated"
            raise ValueError(msg)
        if any(
            i.original_due_utc is not None or i.forecast_due_utc is not None
            for i in self.undated
        ):
            msg = "an item with a due time belongs to a day"
            raise ValueError(msg)
        if self.paging.returned_rows != len(items):
            msg = "returned_rows is the number of items on this page"
            raise ValueError(msg)
        if len(self.undated) > self.paging.undated_rows:
            msg = "the undated rows shown are within the undated rows of the snapshot"
            raise ValueError(msg)
        if self.paging.total_source_rows != self.report.source_items:
            msg = "the whole snapshot has the source_items the report states"
            raise ValueError(msg)
        return self
