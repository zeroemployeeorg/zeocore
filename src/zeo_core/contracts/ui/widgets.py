"""
The widget-routing contract of a Mantle briefing agent (masterclass SOW-02).

Built to the locked rasa-ops specification (SPEC-brief-me-before-i-fly-v2, schemas
``widget-upsert`` and ``data-ref``). A service picks one of four predefined components
and carries a ``widget.upsert`` event with the answer; the event names a ``data_ref``
whose payload is the component's data. Nothing here generates code, HTML or URLs.

The honesty rules are checked, not left to prose: ``replay`` and ``delayed`` are
separate flags; delayed market data cannot be presented as undelayed; a transfer time
is typed as an assumption; a failed source carries an error and an EMPTY payload, so no
stale value can be shown as current. :class:`WidgetBoard` keeps the routing rules: a
revision at or below the card's current one is ignored, and a result for a superseded
request is refused, so it cannot overwrite the card of the changed destination.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

from zeo_core.contracts.ui.calendar_views import CalendarView
from zeo_core.contracts.ui.intervention_cards import InterventionCardPayload

#: The four components of the locked v1 widget specification: byte-compatible, never
#: extended.
LOCKED_COMPONENTS: Final = (
    "flight_status",
    "weather_hourly",
    "arrival_timeline",
    "market_watchlist",
)
#: Core's own profile: the locked four plus the operator-situation card and the
#: commitment calendar. The locked schema refuses both; Core's published v1 schema
#: allows them.
COMPONENTS: Final = (*LOCKED_COMPONENTS, "intervention_card", "calendar_view")
Component = Literal[
    "flight_status",
    "weather_hourly",
    "arrival_timeline",
    "market_watchlist",
    "intervention_card",
    "calendar_view",
]

_DATE_TIME: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?"
    r"(Z|[+-][0-9]{2}:[0-9]{2})"
)
_NO_MARKUP: Final = re.compile(r"[^<>]*")
#: Control (Cc), format (Cf: bidi, zero-width, tags) and the line and paragraph
#: separators (Zl, Zp), as ranges so that no table lookup is needed.
_REFUSED: Final = re.compile(
    "[\u0000-\u001f\u007f-\u009f\u00ad\u0600-\u0605\u061c\u06dd\u070f"
    "\u0890\u0891\u08e2\u180e\u200b-\u200f\u2028-\u202e\u2060-\u2064"
    "\u2066-\u206f\ufeff\ufff9-\ufffb\U000110bd\U000110cd\U00013430-\U0001343f"
    "\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0001\U000e0020-\U000e007f]"
)
TRANSFER_LABEL: Final = "transfer_minutes"
MAX_RETIRED: Final = 4096
MAX_REPLACES: Final = 32
_WIDGET_ID: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


def _date_time(value: str) -> str:
    if not _DATE_TIME.fullmatch(value):
        msg = "not an RFC 3339 date-time"
        raise ValueError(msg)
    try:
        datetime.fromisoformat(value)  # the range: month 13 or hour 99 is refused
    except ValueError:
        msg = "not a real date-time"
        raise ValueError(msg) from None
    return value


def _plain(value: str) -> str:
    if not _NO_MARKUP.fullmatch(value) or _REFUSED.search(value):
        msg = "text holds a control or format character, or markup"
        raise ValueError(msg)
    return value


def _day(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        msg = "not a YYYY-MM-DD date"
        raise ValueError(msg)
    try:
        date.fromisoformat(value)  # a real calendar day
    except ValueError:
        msg = "not a real date"
        raise ValueError(msg) from None
    return value


DateTime = Annotated[str, Field(max_length=40), AfterValidator(_date_time)]
#: A calendar day, for a source that publishes a DATE and no time of day; a midnight
#: time must never be invented for it.
Day = Annotated[str, Field(max_length=10), AfterValidator(_day)]
Text = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_plain)]
Number = Annotated[float, Field(allow_inf_nan=False)]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Route(_Closed):
    """The wire names are ``from`` and ``to``; the Python names are not accepted."""

    origin: Text = Field(alias="from")
    destination: Text = Field(alias="to")


class FlightStatusPayload(_Closed):
    flight_number: Text
    route: Route
    #: Null when the source gives no schedule (keyless ADS-B): shown as not provided.
    scheduled_departure: DateTime | None = None
    estimated_departure: DateTime | None
    scheduled_arrival: DateTime | None = None
    estimated_arrival: DateTime | None
    terminal: Text | None
    #: The provider's own string. "Delayed" is repeated from it, never computed here.
    reported_status: Text
    source_timestamp: DateTime | None


class WeatherHour(_Closed):
    time: DateTime
    temp_c: Number
    #: Null when the forecast gives no probability: never filled with 0.
    precip_prob: Number | None = None
    precip_mm: Number


class WeatherHourlyPayload(_Closed):
    location: Text
    timezone: Text
    valid_from: DateTime
    valid_to: DateTime
    hourly: Annotated[list[WeatherHour], Field(min_length=1, max_length=72)]
    arrival_hour: DateTime | None


class TimelineStep(_Closed):
    label: Text
    time: DateTime


class Stated(_Closed):
    """A number the service states as an assumption, never as measured data."""

    value: Number
    kind: Literal["assumption"]


class ArrivalTimelinePayload(_Closed):
    steps: Annotated[list[TimelineStep], Field(min_length=1, max_length=12)]
    transfer_minutes: Stated
    meeting_time: DateTime
    #: Computed by the service from payload values and shown with its inputs.
    margin_minutes: Number


Symbol = Annotated[str, Field(pattern=r"^[A-Z0-9.\-]{1,12}$")]


class Quote(_Closed):
    """One symbol. A null change means "change unavailable" on the card, never 0."""

    symbol: Symbol
    price: Number
    #: Null when there is no previous value to compare with: never filled with 0.
    change: Number | None = None
    change_pct: Number | None = None


class SeriesPoint(_Closed):
    time: DateTime | Day
    value: Number


class MarketWatchlistPayload(_Closed):
    symbols: Annotated[list[Quote], Field(min_length=1, max_length=20)]
    market_status: Text
    quote_time: DateTime | None
    #: The day a daily reference rate was published, when the source gives a date only.
    quote_date: Day | None = None
    #: "daily_reference": a rate published once a day (ECB-style), not a live quote.
    rate_basis: Literal["live", "daily_reference"] | None = None
    #: True when the provider says the quotes are delayed.
    delayed: StrictBool
    series: Annotated[
        dict[Symbol, Annotated[list[SeriesPoint], Field(max_length=500)]],
        Field(max_length=20),
    ]

    @model_validator(mode="after")
    def _one_freshness(self) -> MarketWatchlistPayload:
        """A card carries ONE freshness line: daily, delayed or live, never two."""
        if self.rate_basis == "daily_reference" and (
            self.quote_date is None or self.delayed
        ):
            msg = "a daily reference rate needs its quote_date and is not delayed"
            raise ValueError(msg)
        if self.quote_date is not None and self.rate_basis != "daily_reference":
            msg = "a quote_date belongs to a daily reference rate"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _series_belong_to_symbols(self) -> MarketWatchlistPayload:
        if not set(self.series) <= {quote.symbol for quote in self.symbols}:
            msg = "a series key is not one of the symbols"
            raise ValueError(msg)
        return self


class _Registry(Mapping[str, type[BaseModel]]):
    """The component registry: read-only, so no component can be added at runtime."""

    def __init__(self, items: dict[str, type[BaseModel]]) -> None:
        self._items = dict(items)

    def __getitem__(self, key: str) -> type[BaseModel]:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


#: The payload model each component accepts.
REGISTRY: Final = _Registry(
    {
        "flight_status": FlightStatusPayload,
        "weather_hourly": WeatherHourlyPayload,
        "arrival_timeline": ArrivalTimelinePayload,
        "market_watchlist": MarketWatchlistPayload,
        "intervention_card": InterventionCardPayload,
        "calendar_view": CalendarView,
    }
)


class Assumption(_Closed):
    label: Text
    value: Annotated[str, Field(max_length=200)] | Number
    kind: Literal["assumption"]


class DataError(_Closed):
    reason: Annotated[str, Field(max_length=200), AfterValidator(_plain)]


class WidgetRefused(ValueError):  # noqa: N818 -- like UiRefused: a refusal, not a fault
    """An event the board will not apply; ``code`` says why and carries no data."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DataRef(_Closed):
    """
    What a ``data_ref`` resolves to: provenance plus the component's payload.

    This JSON Schema is NECESSARY BUT NOT SUFFICIENT: it cannot say that an error needs
    an empty payload, that ``delayed`` must match the market payload, or that a transfer
    time needs its assumption. :meth:`check` and :class:`WidgetBoard` enforce those.
    """

    id: Annotated[str, Field(min_length=1, max_length=80)]
    source: Annotated[str, Field(min_length=1, max_length=120)]
    fetched_at: DateTime
    #: The provider's own update time, or null when it gives none; never fetched_at.
    as_of: DateTime | None
    replay: StrictBool
    delayed: StrictBool | None = None
    assumptions: list[Assumption] = Field(default_factory=list, max_length=10)
    payload: dict[str, Any]
    error: DataError | None = None

    def check(self, component: str) -> DataRef:
        """Validate the payload against ``component``; return self when it fits."""
        if component not in REGISTRY:
            raise WidgetRefused("unknown_component")
        if self.error is not None:
            # A failed source shows an unavailable state; no stale value is current.
            if self.payload:
                raise WidgetRefused("error_with_payload")
            return self
        try:
            model = REGISTRY[component].model_validate(self.payload)
        except ValueError:
            raise WidgetRefused("payload_does_not_match_component") from None
        if component == "market_watchlist" and (
            # Delayed and replay are distinct; delayed must be said, not guessed.
            self.delayed is None or self.delayed != getattr(model, "delayed", None)
        ):
            raise WidgetRefused("delayed_flag_missing_or_inconsistent")
        if component == "arrival_timeline" and not any(
            # The transfer time is the stated assumption: same value, fixed label.
            item.label == TRANSFER_LABEL
            and item.value
            == getattr(getattr(model, "transfer_minutes", None), "value", None)
            for item in self.assumptions
        ):
            raise WidgetRefused("transfer_time_not_labelled_as_assumption")
        return self


class WidgetUpsert(_Closed):
    """``widget.upsert``: put the referenced data on one card."""

    type: Literal["widget.upsert"]
    request_id: Annotated[str, Field(min_length=1, max_length=80)]
    widget_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
    component: Component
    data_ref: Annotated[str, Field(min_length=1, max_length=80)]
    revision: Annotated[int, Field(ge=1, le=2**31 - 1)]


class CardState(_Closed):
    """
    One card in a snapshot, with its own provenance.

    ``request_id`` is the request that wrote the card, so a preserved flight card can
    sit beside a weather card from a newer request. ``replaced`` is true while a newer
    request has taken the widget over but has not written it yet: the surface marks
    the old card as superseded rather than showing it as current.
    """

    widget_id: str
    component: Component
    revision: Annotated[int, Field(ge=0)]
    request_id: str
    replaced: StrictBool
    data_ref: DataRef


class WidgetBoard:
    """
    The cards of one conversation, and the rules that route an event onto them.

    A request supersedes the older ones, either wholly (``begin_request(id)``) or only
    for the widgets it replaces (``begin_request(id, replaces=[...])``). An older
    request may no longer write a widget that a newer request replaced, so a slow
    result for the old destination cannot overwrite the new destination's card, while
    its other widgets (the flight, say) stay valid and in place. A replaced widget
    keeps its card until the new request writes it, and its revision count starts
    again. Within a widget, a revision at or below the current one is ignored
    (``apply`` returns False). Cards are kept by ``widget_id``, so an update changes
    the existing card.
    """

    def __init__(self) -> None:
        self._live: set[str] = set()
        self._retired: set[str] = set()
        self._blocked: dict[str, set[str]] = {}
        self._cards: dict[str, tuple[int, str, DataRef, str]] = {}
        self._pending: set[str] = set()

    def begin_request(
        self, request_id: str, *, replaces: Iterable[str] | None = None
    ) -> None:
        """Start a request; it supersedes the others wholly, or only ``replaces``."""
        if request_id in self._retired or request_id in self._live:
            raise WidgetRefused("request_already_superseded")
        if len(self._retired) + len(self._live) >= MAX_RETIRED:
            raise WidgetRefused("too_many_requests")
        widgets = None if replaces is None else self._widget_ids(replaces)
        if widgets is None:
            self._retired |= self._live
            self._live = {request_id}
            self._blocked.clear()
            self._cards.clear()
            self._pending.clear()
            return
        for other in self._live:
            self._blocked.setdefault(other, set()).update(widgets)
        for widget_id in widgets & self._cards.keys():
            _, component, data, owner = self._cards[widget_id]
            self._cards[widget_id] = (0, component, data, owner)  # revisions restart
            self._pending.add(widget_id)
        self._live.add(request_id)

    @staticmethod
    def _widget_ids(replaces: Iterable[str]) -> set[str]:
        """The widget ids to replace: a list of real ids, never a bare string."""
        if isinstance(replaces, str | bytes):
            raise WidgetRefused("replaces_must_be_a_list")
        widgets = list(replaces)
        if len(widgets) > MAX_REPLACES or not all(
            isinstance(widget, str) and _WIDGET_ID.fullmatch(widget)
            for widget in widgets
        ):
            raise WidgetRefused("bad_replaces")
        return set(widgets)

    def supersede(self, request_id: str) -> None:
        """Mark a request superseded without starting another: its late results fail."""
        self._live.discard(request_id)
        self._blocked.pop(request_id, None)
        self._retired.add(request_id)

    def apply(self, event: WidgetUpsert, data: DataRef) -> bool:
        """Apply an event; False when ignored (a stale revision), raise when refused."""
        if event.request_id not in self._live or event.widget_id in self._blocked.get(
            event.request_id, ()
        ):
            raise WidgetRefused("superseded_request")
        if data.id != event.data_ref:
            raise WidgetRefused("data_ref_mismatch")
        data.check(event.component)
        current = self._cards.get(event.widget_id)
        if current is not None:
            if current[1] != event.component:
                raise WidgetRefused("component_changed")
            if event.revision <= current[0]:
                return False
        # A copy, so a caller cannot mutate the stored payload past the checks.
        self._cards[event.widget_id] = (
            event.revision,
            event.component,
            _copy(data),
            event.request_id,
        )
        self._pending.discard(event.widget_id)
        return True

    def cards(self) -> dict[str, DataRef]:
        return {key: _copy(value[2]) for key, value in self._cards.items()}

    def snapshot(self) -> list[CardState]:
        """Every card with its own request and revision, in widget order."""
        return [
            CardState(
                widget_id=widget_id,
                component=component,  # type: ignore[arg-type]
                revision=revision,
                request_id=owner,
                replaced=widget_id in self._pending,
                data_ref=_copy(data),
            )
            for widget_id, (revision, component, data, owner) in sorted(
                self._cards.items()
            )
        ]


def _copy(data: DataRef) -> DataRef:
    return DataRef.model_validate(data.model_dump(by_alias=True))
