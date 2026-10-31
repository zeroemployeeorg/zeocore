"""The widget-routing contract, built to the rasa-ops specification (SOW-02)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]  # no stubs; a dev dependency
import pytest
from pydantic import ValidationError

from zeo_core.cli.schemas import render_schema
from zeo_core.contracts.ui import widgets
from zeo_core.contracts.ui.widgets import (
    COMPONENTS,
    LOCKED_COMPONENTS,
    REGISTRY,
    Assumption,
    DataRef,
    WidgetBoard,
    WidgetRefused,
    WidgetUpsert,
)

FIXTURES = Path(__file__).parent / "fixtures"
T = "2026-10-22T13:40:00Z"

PAYLOADS: dict[str, dict[str, Any]] = {
    "flight_status": {
        "flight_number": "BA117",
        "route": {"from": "LHR", "to": "JFK"},
        "scheduled_departure": "2026-10-22T14:25:00Z",
        "estimated_departure": None,
        "scheduled_arrival": "2026-10-22T21:10:00Z",
        "estimated_arrival": "2026-10-22T21:35:00Z",
        "terminal": None,
        "reported_status": "Delayed",
        "source_timestamp": T,
    },
    "weather_hourly": {
        "location": "New York",
        "timezone": "America/New_York",
        "valid_from": "2026-10-22T20:00:00Z",
        "valid_to": "2026-10-23T02:00:00Z",
        "hourly": [
            {
                "time": "2026-10-22T21:00:00Z",
                "temp_c": 14.5,
                "precip_prob": 30,
                "precip_mm": 0.2,
            }
        ],
        "arrival_hour": "2026-10-22T21:00:00Z",
    },
    "arrival_timeline": {
        "steps": [{"label": "Land", "time": "2026-10-22T21:35:00Z"}],
        "transfer_minutes": {"value": 75, "kind": "assumption"},
        "meeting_time": "2026-10-22T22:00:00Z",
        "margin_minutes": -50,
    },
    "market_watchlist": {
        "symbols": [
            {"symbol": "AAPL", "price": 231.4, "change": -1.9, "change_pct": -0.8}
        ],
        "market_status": "open",
        "quote_time": T,
        "delayed": True,
        "series": {"AAPL": [{"time": T, "value": 231.4}]},
    },
}


def ref(
    component: str = "weather_hourly", ref_id: str = "r1", **over: object
) -> DataRef:  # noqa: ANN003 -- field overrides
    body: dict[str, Any] = {
        "id": ref_id,
        "source": "provider.x",
        "fetched_at": T,
        "as_of": T,
        "replay": False,
        "delayed": True if component == "market_watchlist" else None,
        "assumptions": (
            [
                {
                    "label": "transfer_minutes",
                    "value": 75,
                    "kind": "assumption",
                }
            ]
            if component == "arrival_timeline"
            else []
        ),
        "payload": copy.deepcopy(PAYLOADS[component]),
        "error": None,
    }
    return DataRef(**(body | over))


def upsert(
    component: str = "weather_hourly",
    *,
    request_id: str = "req-1",
    widget_id: str = "arrival-weather",
    data_ref: str = "r1",
    revision: int = 1,
) -> WidgetUpsert:
    return WidgetUpsert(
        type="widget.upsert",
        request_id=request_id,
        widget_id=widget_id,
        component=component,  # type: ignore[arg-type]
        data_ref=data_ref,
        revision=revision,
    )


def test_the_registry_has_the_four_components_and_the_card() -> None:
    assert set(REGISTRY) == set(COMPONENTS)
    assert set(COMPONENTS) == set(PAYLOADS) | {"intervention_card", "calendar_view"}
    assert set(LOCKED_COMPONENTS) == set(PAYLOADS)


@pytest.mark.parametrize("component", LOCKED_COMPONENTS)
def test_each_component_accepts_its_sample(component: str) -> None:
    ref(component).check(component)


@pytest.mark.parametrize("component", LOCKED_COMPONENTS)
def test_a_payload_for_another_component_is_refused(component: str) -> None:
    other = next(name for name in LOCKED_COMPONENTS if name != component)
    with pytest.raises(WidgetRefused) as refused:
        ref(other).check(component)
    assert refused.value.code in {
        "payload_does_not_match_component",
        "delayed_flag_missing_or_inconsistent",
    }


def test_unknown_keys_coercion_and_markup_are_refused() -> None:
    bad = copy.deepcopy(PAYLOADS["flight_status"]) | {"extra": 1}
    with pytest.raises(WidgetRefused):
        ref("flight_status", payload=bad).check("flight_status")
    bad = copy.deepcopy(PAYLOADS["weather_hourly"])
    bad["hourly"][0]["temp_c"] = "14.5"
    with pytest.raises(WidgetRefused):
        ref(payload=bad).check("weather_hourly")
    bad = copy.deepcopy(PAYLOADS["flight_status"]) | {
        "reported_status": "<b>On time</b>"
    }
    with pytest.raises(WidgetRefused):
        ref("flight_status", payload=bad).check("flight_status")
    bad = copy.deepcopy(PAYLOADS["weather_hourly"])
    bad["hourly"][0]["temp_c"] = float("nan")
    with pytest.raises(WidgetRefused):
        ref(payload=bad).check("weather_hourly")


def test_a_missing_provider_update_time_stays_unknown() -> None:
    assert ref(as_of=None).as_of is None
    with pytest.raises(ValidationError):
        DataRef(**(ref().model_dump() | {"as_of": "yesterday"}))


def test_delayed_market_data_is_labelled_delayed_and_is_not_replay() -> None:
    delayed = ref("market_watchlist")
    assert (delayed.delayed, delayed.replay) == (True, False)
    delayed.check("market_watchlist")
    replayed = ref("market_watchlist", replay=True)
    assert (replayed.delayed, replayed.replay) == (True, True)


@pytest.mark.parametrize("flag", [None, False])
def test_delayed_cannot_be_dropped_or_contradicted(flag: bool | None) -> None:
    with pytest.raises(WidgetRefused) as refused:
        ref("market_watchlist", delayed=flag).check("market_watchlist")
    assert refused.value.code == "delayed_flag_missing_or_inconsistent"


def test_a_transfer_time_is_an_assumption() -> None:
    bad = copy.deepcopy(PAYLOADS["arrival_timeline"])
    bad["transfer_minutes"]["kind"] = "traffic"
    with pytest.raises(WidgetRefused):
        ref("arrival_timeline", payload=bad).check("arrival_timeline")
    with pytest.raises(WidgetRefused) as refused:
        ref("arrival_timeline", assumptions=[]).check("arrival_timeline")
    assert refused.value.code == "transfer_time_not_labelled_as_assumption"


def test_a_failed_source_carries_a_reason_and_no_value() -> None:
    failed = ref(payload={}, error={"reason": "provider timed out"})
    assert failed.check("flight_status").error is not None
    with pytest.raises(WidgetRefused) as refused:
        ref(error={"reason": "x"}).check("weather_hourly")
    assert refused.value.code == "error_with_payload"


def test_an_upsert_updates_the_same_card_in_place() -> None:
    board = WidgetBoard()
    board.begin_request("req-1")
    assert board.apply(upsert(revision=1), ref())
    assert board.apply(upsert(revision=2, data_ref="r2"), ref(ref_id="r2"))
    assert list(board.cards()) == ["arrival-weather"]
    assert board.cards()["arrival-weather"].id == "r2"


def test_a_lower_or_equal_revision_is_ignored() -> None:
    board = WidgetBoard()
    board.begin_request("req-1")
    board.apply(upsert(revision=2), ref())
    for revision in (1, 2):
        assert (
            board.apply(upsert(revision=revision, data_ref="r9"), ref(ref_id="r9"))
            is False
        )
    assert board.cards()["arrival-weather"].id == "r1"


def test_the_old_destination_cannot_overwrite_the_new_one() -> None:
    board = WidgetBoard()
    board.begin_request("req-1")
    board.begin_request("req-2")  # the user changed the destination
    board.apply(upsert(request_id="req-2", data_ref="new"), ref(ref_id="new"))
    with pytest.raises(WidgetRefused) as refused:
        board.apply(upsert(request_id="req-1", revision=9), ref())
    assert refused.value.code == "superseded_request"
    assert board.cards()["arrival-weather"].id == "new"


def test_a_request_can_be_marked_superseded_by_itself() -> None:
    board = WidgetBoard()
    board.begin_request("req-1")
    board.supersede("req-1")
    with pytest.raises(WidgetRefused) as refused:
        board.apply(upsert(), ref())
    assert refused.value.code == "superseded_request"
    with pytest.raises(WidgetRefused) as restarted:
        board.begin_request("req-1")
    assert restarted.value.code == "request_already_superseded"


def test_an_event_before_any_request_is_refused() -> None:
    with pytest.raises(WidgetRefused) as refused:
        WidgetBoard().apply(upsert(), ref())
    assert refused.value.code == "superseded_request"


def test_the_event_and_its_data_must_agree() -> None:
    board = WidgetBoard()
    board.begin_request("req-1")
    with pytest.raises(WidgetRefused) as refused:
        board.apply(upsert(data_ref="other"), ref())
    assert refused.value.code == "data_ref_mismatch"
    with pytest.raises(WidgetRefused):
        board.apply(
            upsert("flight_status"), ref()
        )  # a weather payload on a flight card


def test_a_card_cannot_change_its_component() -> None:
    board = WidgetBoard()
    board.begin_request("req-1")
    board.apply(upsert(), ref())
    with pytest.raises(WidgetRefused) as refused:
        board.apply(
            upsert("flight_status", revision=2, data_ref="r2"),
            ref("flight_status", "r2"),
        )
    assert refused.value.code == "component_changed"


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "widget.delete"},
        {"revision": 0},
        {"widget_id": "Bad_Id"},
        {"widget_id": "-lead"},
        {"component": "iframe"},
        {"request_id": ""},
        {"extra": 1},
    ],
)
def test_malformed_events_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WidgetUpsert(**(upsert().model_dump() | bad))


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"rasa-ops-{name}.schema.json").read_text())  # type: ignore[no-any-return]


@pytest.mark.parametrize("component", LOCKED_COMPONENTS)
def test_what_we_accept_conforms_to_the_rasa_ops_schemas(component: str) -> None:
    jsonschema.validate(ref(component).model_dump(by_alias=True), load("data-ref"))
    jsonschema.validate(upsert(component).model_dump(), load("widget-upsert"))


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "widget.delete"},
        {"revision": 0},
        {"widget_id": "Bad_Id"},
        {"component": "iframe"},
        {"extra": 1},
    ],
)
def test_what_the_rasa_ops_schema_rejects_we_reject(bad: dict[str, Any]) -> None:
    event = upsert().model_dump() | bad
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(event, load("widget-upsert"))
    with pytest.raises(ValidationError):
        WidgetUpsert(**event)


def test_the_schemas_are_exported() -> None:
    for name in ("ui.widget-upsert", "ui.widget-data-ref"):
        assert render_schema(name)["additionalProperties"] is False


# --- council round on #159 ---------------------------------------------------------


def test_the_assumption_must_be_the_transfer_time() -> None:
    wrong_value = [{"label": "transfer_minutes", "value": 10, "kind": "assumption"}]
    wrong_label = [{"label": "something else", "value": 75, "kind": "assumption"}]
    for assumptions in (wrong_value, wrong_label):
        with pytest.raises(WidgetRefused) as refused:
            ref("arrival_timeline", assumptions=assumptions).check("arrival_timeline")
        assert refused.value.code == "transfer_time_not_labelled_as_assumption"


def test_the_route_accepts_only_the_wire_names() -> None:
    payload = copy.deepcopy(PAYLOADS["flight_status"])
    payload["route"] = {"origin": "LHR", "destination": "JFK"}
    with pytest.raises(WidgetRefused):
        ref("flight_status", payload=payload).check("flight_status")


def test_a_stored_card_cannot_be_mutated_past_the_checks() -> None:
    board = WidgetBoard()
    board.begin_request("req-1")
    mine = ref("market_watchlist")
    board.apply(upsert("market_watchlist"), mine)
    mine.payload["delayed"] = False  # the caller changes its own object afterwards
    mine.assumptions.append(Assumption(label="x", value=1, kind="assumption"))
    stored = board.cards()["arrival-weather"]
    assert stored.payload["delayed"] is True and stored.assumptions == []
    stored.payload["delayed"] = False  # and a copy handed out is only a copy
    assert board.cards()["arrival-weather"].payload["delayed"] is True


def test_series_keys_are_symbols_that_are_in_the_watchlist() -> None:
    payload = copy.deepcopy(PAYLOADS["market_watchlist"])
    payload["series"]["MSFT"] = payload["series"]["AAPL"]  # not in symbols
    with pytest.raises(WidgetRefused):
        ref("market_watchlist", payload=payload).check("market_watchlist")
    payload = copy.deepcopy(PAYLOADS["market_watchlist"])
    payload["series"] = {"<script>": payload["series"]["AAPL"]}
    with pytest.raises(WidgetRefused):
        ref("market_watchlist", payload=payload).check("market_watchlist")
    payload = copy.deepcopy(PAYLOADS["market_watchlist"])
    payload["series"] = {f"S{i}": [] for i in range(21)}
    with pytest.raises(WidgetRefused):
        ref("market_watchlist", payload=payload).check("market_watchlist")


@pytest.mark.parametrize(
    "bad",
    ["2026-13-45T99:99:99Z", "2026-02-30T10:00:00Z", "2026-10-22T24:00:00Z"],
)
def test_a_date_time_must_be_a_real_one(bad: str) -> None:
    with pytest.raises(ValidationError):
        DataRef(**(ref().model_dump() | {"fetched_at": bad}))


@pytest.mark.parametrize("char", ["\u202e", "\u200b", "\u2028", "\x00"])
def test_format_and_separator_characters_are_refused_in_text(char: str) -> None:
    payload = copy.deepcopy(PAYLOADS["flight_status"])
    payload["reported_status"] = f"On{char}time"
    with pytest.raises(WidgetRefused):
        ref("flight_status", payload=payload).check("flight_status")


def test_assumptions_are_capped_and_finite() -> None:
    many = [{"label": "transfer_minutes", "value": 75, "kind": "assumption"}] * 11
    with pytest.raises(ValidationError):
        ref("arrival_timeline", assumptions=many)
    with pytest.raises(ValidationError):
        Assumption(label="x", value=float("nan"), kind="assumption")


def test_the_registry_cannot_be_changed() -> None:
    with pytest.raises(TypeError):
        REGISTRY["iframe"] = REGISTRY["flight_status"]  # type: ignore[index]


def test_the_set_of_retired_requests_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(widgets, "MAX_RETIRED", 3)
    board = WidgetBoard()
    with pytest.raises(WidgetRefused) as refused:
        for number in range(10):
            board.begin_request(f"req-{number}")
    assert refused.value.code == "too_many_requests"


def test_the_exported_data_ref_schema_says_it_is_not_sufficient() -> None:
    assert (
        "NECESSARY BUT NOT SUFFICIENT"
        in render_schema("ui.widget-data-ref")["description"]
    )


# --- rasa-ops S5b: a follow-up replaces only its own widget ------------------------


def two_cards() -> WidgetBoard:
    board = WidgetBoard()
    board.begin_request("r1")
    board.apply(
        upsert("flight_status", request_id="r1", widget_id="flight", data_ref="f1"),
        ref("flight_status", "f1"),
    )
    board.apply(
        upsert(request_id="r1", widget_id="weather", data_ref="w1"), ref(ref_id="w1")
    )
    return board


def test_a_follow_up_replaces_only_its_own_widget() -> None:
    board = two_cards()
    board.begin_request("r2", replaces=["weather"])
    assert board.apply(
        upsert(request_id="r2", widget_id="weather", data_ref="w2"), ref(ref_id="w2")
    )
    assert {key: card.id for key, card in board.cards().items()} == {
        "flight": "f1",
        "weather": "w2",
    }


def test_a_replaced_widget_keeps_its_card_until_the_new_one_arrives() -> None:
    board = two_cards()
    board.begin_request("r2", replaces=["weather"])
    assert board.cards()["weather"].id == "w1"


def test_the_old_request_cannot_write_a_replaced_widget_but_can_write_the_rest() -> (
    None
):
    board = two_cards()
    board.begin_request("r2", replaces=["weather"])
    with pytest.raises(WidgetRefused) as refused:
        board.apply(
            upsert(request_id="r1", widget_id="weather", data_ref="late", revision=9),
            ref(ref_id="late"),
        )
    assert refused.value.code == "superseded_request"
    assert board.apply(
        upsert(
            "flight_status",
            request_id="r1",
            widget_id="flight",
            data_ref="f2",
            revision=2,
        ),
        ref("flight_status", "f2"),
    )
    assert board.cards()["flight"].id == "f2" and board.cards()["weather"].id == "w1"


def test_revisions_start_again_for_a_replaced_widget_only() -> None:
    board = two_cards()
    board.apply(
        upsert(request_id="r1", widget_id="weather", data_ref="w9", revision=5),
        ref(ref_id="w9"),
    )
    board.begin_request("r2", replaces=["weather"])
    assert board.apply(
        upsert(request_id="r2", widget_id="weather", data_ref="n1", revision=1),
        ref(ref_id="n1"),
    )
    assert not board.apply(
        upsert(request_id="r2", widget_id="weather", data_ref="n0", revision=1),
        ref(ref_id="n0"),
    )


def test_a_whole_request_still_supersedes_everything() -> None:
    board = two_cards()
    board.begin_request("r2")
    assert board.cards() == {}
    with pytest.raises(WidgetRefused):
        board.apply(
            upsert(
                "flight_status",
                request_id="r1",
                widget_id="flight",
                data_ref="f3",
                revision=9,
            ),
            ref("flight_status", "f3"),
        )


def test_a_request_id_cannot_be_started_twice() -> None:
    board = two_cards()
    with pytest.raises(WidgetRefused) as refused:
        board.begin_request("r1", replaces=["weather"])
    assert refused.value.code == "request_already_superseded"


def test_marking_a_request_superseded_keeps_the_cards_it_wrote() -> None:
    board = two_cards()
    board.supersede("r1")
    assert set(board.cards()) == {"flight", "weather"}


def test_the_snapshot_keeps_each_cards_own_request() -> None:
    board = two_cards()
    board.begin_request("r2", replaces=["weather"])
    before = {c.widget_id: (c.request_id, c.replaced) for c in board.snapshot()}
    assert before == {"flight": ("r1", False), "weather": ("r1", True)}
    board.apply(
        upsert(request_id="r2", widget_id="weather", data_ref="w2"), ref(ref_id="w2")
    )
    after = {
        c.widget_id: (c.request_id, c.replaced, c.data_ref.id) for c in board.snapshot()
    }
    assert after == {"flight": ("r1", False, "f1"), "weather": ("r2", False, "w2")}


def test_the_snapshot_orders_cards_and_carries_revisions() -> None:
    board = two_cards()
    board.apply(
        upsert(request_id="r1", widget_id="weather", data_ref="w9", revision=4),
        ref(ref_id="w9"),
    )
    cards = board.snapshot()
    assert [(c.widget_id, c.revision, c.component) for c in cards] == [
        ("flight", 1, "flight_status"),
        ("weather", 4, "weather_hourly"),
    ]


def test_a_whole_new_request_leaves_nothing_marked_replaced() -> None:
    board = two_cards()
    board.begin_request("r2", replaces=["weather"])
    board.begin_request("r3")
    assert board.snapshot() == []
    board.apply(
        upsert(request_id="r3", widget_id="weather", data_ref="w3"), ref(ref_id="w3")
    )
    assert [c.replaced for c in board.snapshot()] == [False]


def test_a_snapshot_is_a_copy() -> None:
    board = two_cards()
    board.snapshot()[0].data_ref.payload["x"] = 1
    assert "x" not in board.snapshot()[0].data_ref.payload


# --- council round 2 on #159 (N1): replaces is a list of real widget ids ----------


@pytest.mark.parametrize(
    "bad",
    ["weather", b"weather", ["Weather"], ["a b"], [""], [1], ["x" * 64], [None]],
)
def test_replaces_must_be_a_list_of_real_widget_ids(bad: Any) -> None:  # noqa: ANN401
    board = two_cards()
    with pytest.raises(WidgetRefused) as refused:
        board.begin_request("r2", replaces=bad)
    assert refused.value.code in {"replaces_must_be_a_list", "bad_replaces"}


def test_a_bad_replaces_changes_nothing() -> None:
    board = two_cards()
    with pytest.raises(WidgetRefused):
        board.begin_request("r2", replaces="weather")
    # r1 is still live and may still write weather, and r2 never started
    assert board.apply(
        upsert(request_id="r1", widget_id="weather", data_ref="w2", revision=2),
        ref(ref_id="w2"),
    )
    with pytest.raises(WidgetRefused) as refused:
        board.apply(
            upsert(request_id="r2", widget_id="weather", data_ref="w3"),
            ref(ref_id="w3"),
        )
    assert refused.value.code == "superseded_request"


def test_replaces_is_capped() -> None:
    board = two_cards()
    too_many = [f"w{i}" for i in range(widgets.MAX_REPLACES + 1)]
    with pytest.raises(WidgetRefused) as refused:
        board.begin_request("r2", replaces=too_many)
    assert refused.value.code == "bad_replaces"
    board.begin_request("r3", replaces=too_many[: widgets.MAX_REPLACES])


def test_an_empty_replaces_replaces_nothing() -> None:
    board = two_cards()
    board.begin_request("r2", replaces=[])
    assert {c.widget_id for c in board.snapshot()} == {"flight", "weather"}
    assert board.apply(
        upsert(request_id="r1", widget_id="weather", data_ref="w2", revision=2),
        ref(ref_id="w2"),
    )


# --- real sources never invent a value (rasa, 2026-10-22) -----------------------------


def test_a_flight_without_schedules_is_valid_and_keeps_them_absent() -> None:
    payload = copy.deepcopy(PAYLOADS["flight_status"])
    payload["scheduled_departure"] = None
    payload["scheduled_arrival"] = None
    ref("flight_status", payload=payload).check("flight_status")
    omitted = {k: v for k, v in payload.items() if not k.startswith("scheduled_")}
    ref("flight_status", payload=omitted).check("flight_status")  # keyless ADS-B


@pytest.mark.parametrize("value", ["tomorrow", "2026-13-45T99:99:99Z", 5, ""])
def test_a_schedule_that_is_given_must_still_be_a_real_time(value: object) -> None:
    payload = copy.deepcopy(PAYLOADS["flight_status"])
    payload["scheduled_arrival"] = value
    with pytest.raises(WidgetRefused):
        ref("flight_status", payload=payload).check("flight_status")


def test_a_forecast_without_a_probability_is_valid_and_is_not_zero() -> None:
    payload = copy.deepcopy(PAYLOADS["weather_hourly"])
    payload["hourly"][0]["precip_prob"] = None
    checked = ref(payload=payload).check("weather_hourly")
    model = REGISTRY["weather_hourly"].model_validate(checked.payload)
    assert model.hourly[0].precip_prob is None  # type: ignore[attr-defined]
    del payload["hourly"][0]["precip_prob"]
    ref(payload=payload).check("weather_hourly")


def test_a_probability_that_is_given_must_be_a_number() -> None:
    payload = copy.deepcopy(PAYLOADS["weather_hourly"])
    payload["hourly"][0]["precip_prob"] = "30%"
    with pytest.raises(WidgetRefused):
        ref(payload=payload).check("weather_hourly")


def test_a_daily_reference_rate_carries_its_date_and_its_basis() -> None:
    payload = copy.deepcopy(PAYLOADS["market_watchlist"])
    payload["quote_time"] = None  # the source publishes a DATE, no midnight is invented
    payload["quote_date"] = "2026-10-21"
    payload["rate_basis"] = "daily_reference"
    payload["delayed"] = False
    payload["series"] = {
        "AAPL": [
            {"time": "2026-10-20", "value": 1.16},
            {"time": "2026-10-21", "value": 1.17},
        ]
    }
    ref("market_watchlist", payload=payload, delayed=False).check("market_watchlist")


@pytest.mark.parametrize(
    "day", ["2026-02-30", "21-10-2026", "2026-10-21T00:00:00Z", "", "2026-13-01"]
)
def test_a_quote_date_must_be_a_real_calendar_day(day: str) -> None:
    payload = copy.deepcopy(PAYLOADS["market_watchlist"])
    payload["quote_date"] = day
    payload["rate_basis"] = "daily_reference"  # so only the day itself is wrong
    payload["delayed"] = False
    with pytest.raises(WidgetRefused):
        ref("market_watchlist", payload=payload).check("market_watchlist")


def test_the_rate_basis_is_one_of_two_values() -> None:
    payload = copy.deepcopy(PAYLOADS["market_watchlist"])
    payload["rate_basis"] = "realtime"
    with pytest.raises(WidgetRefused):
        ref("market_watchlist", payload=payload).check("market_watchlist")


def test_a_series_day_must_be_real_too() -> None:
    payload = copy.deepcopy(PAYLOADS["market_watchlist"])
    payload["series"] = {"AAPL": [{"time": "2026-02-30", "value": 1.0}]}
    with pytest.raises(WidgetRefused):
        ref("market_watchlist", payload=payload).check("market_watchlist")


def test_the_old_payloads_are_still_valid() -> None:
    for component, payload in PAYLOADS.items():
        ref(component, payload=copy.deepcopy(payload)).check(component)


def test_a_quote_without_a_previous_value_has_no_change_and_is_not_zero() -> None:
    payload = copy.deepcopy(PAYLOADS["market_watchlist"])
    payload["symbols"][0]["change"] = None
    payload["symbols"][0]["change_pct"] = None
    checked = ref("market_watchlist", payload=payload).check("market_watchlist")
    model = REGISTRY["market_watchlist"].model_validate(checked.payload)
    assert model.symbols[0].change is None and model.symbols[0].change_pct is None  # type: ignore[attr-defined]
    del payload["symbols"][0]["change"], payload["symbols"][0]["change_pct"]
    ref("market_watchlist", payload=payload).check("market_watchlist")


def test_a_change_that_is_given_must_be_a_finite_number() -> None:
    for bad in ("0.5", float("nan"), float("inf")):
        payload = copy.deepcopy(PAYLOADS["market_watchlist"])
        payload["symbols"][0]["change"] = bad
        with pytest.raises(WidgetRefused):
            ref("market_watchlist", payload=payload).check("market_watchlist")


def test_a_price_is_still_required() -> None:
    payload = copy.deepcopy(PAYLOADS["market_watchlist"])
    del payload["symbols"][0]["price"]
    with pytest.raises(WidgetRefused):
        ref("market_watchlist", payload=payload).check("market_watchlist")


# --- one freshness per market card (Council, PR162) ---------------------------


def _market(**over: object) -> dict[str, Any]:
    payload = copy.deepcopy(PAYLOADS["market_watchlist"])
    payload.update(over)
    return payload


def test_a_daily_rate_with_its_day_and_not_delayed_is_accepted() -> None:
    payload = _market(
        quote_time=None,
        quote_date="2026-10-21",
        rate_basis="daily_reference",
        delayed=False,
    )
    ref("market_watchlist", payload=payload, delayed=False).check("market_watchlist")


def test_a_live_or_delayed_quote_with_a_quote_time_is_accepted() -> None:
    ref(
        "market_watchlist",
        payload=_market(rate_basis="live", delayed=False),
        delayed=False,
    ).check("market_watchlist")
    ref("market_watchlist", payload=_market()).check(
        "market_watchlist"
    )  # delayed, no basis


@pytest.mark.parametrize(
    "over",
    [
        {"rate_basis": "daily_reference", "quote_date": "2026-10-21", "delayed": True},
        {"rate_basis": "daily_reference", "quote_date": None, "delayed": False},
        {"rate_basis": "daily_reference", "delayed": False},
        {"quote_date": "2026-10-21", "rate_basis": "live", "delayed": False},
        {"quote_date": "2026-10-21", "rate_basis": None, "delayed": False},
    ],
)
def test_the_combinations_that_would_show_two_freshness_lines_or_none_are_refused(
    over: dict[str, Any],
) -> None:
    with pytest.raises(WidgetRefused):
        ref(
            "market_watchlist",
            payload=_market(**over),
            delayed=bool(over.get("delayed")),
        ).check("market_watchlist")


@pytest.mark.parametrize("component", ["intervention_card", "calendar_view"])
def test_the_locked_schema_refuses_a_component_of_core_s_profile(
    component: str,
) -> None:
    # These components are Core's profile only: a consumer pinned to the locked spec
    # must never receive one, and the locked schema says so by refusing the name.
    assert component not in LOCKED_COMPONENTS
    event = upsert().model_dump() | {"component": component}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(event, load("widget-upsert"))
    assert WidgetUpsert(**event).component == component


def test_check_still_refuses_an_unregistered_component_and_a_wrong_payload() -> None:
    with pytest.raises(WidgetRefused) as unknown:
        ref("flight_status").check("iframe")
    assert unknown.value.code == "unknown_component"
    for component in ("intervention_card", "calendar_view"):
        with pytest.raises(WidgetRefused) as wrong:
            ref("flight_status").check(component)
        assert wrong.value.code == "payload_does_not_match_component"


def test_no_briefing_source_can_emit_a_component_of_core_s_profile() -> None:
    # The briefing route feeds consumers pinned to the locked four; the card and the
    # calendar are Core's profile only. A source module that names one fails here.
    import zeo_core.integrations.briefing_sources as sources

    root = Path(sources.__file__).parent
    names = sorted(root.rglob("*.py"))
    assert names
    for path in names:
        text = path.read_text(encoding="utf-8")
        assert "intervention_card" not in text, path.name
        assert "intervention_cards" not in text, path.name
        assert "calendar_view" not in text, path.name
        assert "calendar_views" not in text, path.name
