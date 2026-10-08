"""Offline contract tests for the 0.13.0 read expansion: expenses, receipts, labels.

No Revolut account or network is involved. Nothing here is live validation:
the shapes come from Revolut's published OpenAPI contract, not from a response
observed from a real account.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from zeo_core.integrations.revolut import (
    MAX_RECEIPT_BYTES,
    ExpensePage,
    ExpenseQuery,
    LabelQuery,
    RevolutAPIError,
    RevolutBusinessClient,
    RevolutEnvironment,
    RevolutTransport,
)

CANARY = "canary-credential-7f3e9a"  # noqa: S105 -- synthetic test value
GROUP_ID = "6f1d2c3b-4a5e-4f60-8a71-0b2c3d4e5f60"
LABEL_ID = "7a2e3d4c-5b6f-4071-9b82-1c3d4e5f6071"
Handler = Callable[[httpx.Request], httpx.Response]
START = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
# Synthetic pagination cursors, not credentials.
CURSOR_1 = "dG9rMQ=="  # noqa: S105 -- synthetic pagination cursor
CURSOR_2 = "dG9rMg=="  # noqa: S105 -- synthetic pagination cursor


def _client(handler: Handler) -> RevolutBusinessClient:
    return RevolutBusinessClient(
        transport=RevolutTransport(
            SecretStr(CANARY),
            environment=RevolutEnvironment.SANDBOX,
            transport=httpx.MockTransport(handler),
        )
    )


def _json(payload: object, status: int = 200) -> Handler:
    return lambda request: httpx.Response(status, json=payload)


def _never(_request: httpx.Request) -> httpx.Response:
    raise AssertionError("no request may be sent")


def _expense(index: int, when: datetime, **extra: object) -> dict[str, Any]:
    return {
        "id": f"exp-{index}",
        "state": "approved",
        "transaction_type": "card_payment",
        "expense_date": when.isoformat(),
        "submitted_at": when.isoformat(),
        "payer": "A Person",
        "merchant": "Hetzner",
        "transaction_id": f"tx-{index}",
        "labels": {"Project": ["Dreamhuggers"]},
        "splits": [
            {
                "amount": {"amount": 12.3, "currency": "EUR"},
                "category": {"id": "cat-1", "name": "Software", "code": "400"},
                "tax_rate": {"id": "vat-1", "name": "VAT", "percentage": 19.0},
            }
        ],
        "receipt_ids": [f"rcpt-{index}"],
        "spent_amount": {"amount": 0.30000000000000004, "currency": "EUR"},
        **extra,
    }


# -- expenses -------------------------------------------------------------------


def test_expenses_are_exact_drop_the_payer_and_send_only_declared_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_expense(1, START)])

    query = ExpenseQuery(
        from_=START - timedelta(days=30),
        to=START + timedelta(days=1),
        state="approved",
        transaction_type="card_payment",
        count=50,
    )
    page = _client(handler).list_expenses(query)
    (expense,) = page.expenses
    # A JSON number is parsed straight to Decimal, never through a float.
    assert expense.spent_amount.amount == Decimal("0.30000000000000004")
    assert expense.splits[0].amount.amount == Decimal("12.3")
    assert expense.splits[0].tax_rate is not None
    assert expense.splits[0].tax_rate.percentage == Decimal("19.0")
    assert (
        "payer" not in expense.model_dump() and "A Person" not in page.model_dump_json()
    )
    assert page.next_to is None  # not a full page
    # A short page ends the loop, but it is not proof the window was complete.
    assert page.completeness == "unverified"
    (request,) = seen
    assert request.url.host == "sandbox-b2b.revolut.com"
    assert request.url.path == "/api/1.0/expenses"
    assert dict(request.url.params) == {
        "count": "50",
        "from": "2026-08-31T12:00:00Z",
        "to": "2026-10-01T12:00:00Z",
        "state": "approved",
        "transaction_type": "card_payment",
    }
    assert request.headers["Authorization"] == f"Bearer {CANARY}"


def test_a_full_expense_page_yields_the_oldest_expense_date_as_its_cursor() -> None:
    items = [_expense(i, START - timedelta(hours=i)) for i in range(3)]
    page = _client(_json(items)).list_expenses(ExpenseQuery(count=3))
    assert page.next_to == START - timedelta(hours=2)
    assert page.completeness == "unverified"


def test_an_expense_page_cannot_claim_a_complete_window() -> None:
    page = _client(_json([])).list_expenses()
    assert page.next_to is None and page.completeness == "unverified"
    with pytest.raises(ValidationError):
        ExpensePage.model_validate({**page.model_dump(), "completeness": "complete"})


def test_an_expense_cursor_that_does_not_advance_is_refused() -> None:
    items = [_expense(i, START) for i in range(2)]
    with pytest.raises(RevolutAPIError) as caught:
        _client(_json(items)).list_expenses(ExpenseQuery(to=START, count=2))
    assert caught.value.code == "PAGINATION_STALLED"


@pytest.mark.parametrize(
    "broken",
    [
        {"labels": {"Project": ["one", "two"]}},  # one label per group, published
        {"labels": {f"g{i}": ["x"] for i in range(6)}},  # at most five groups
        {"spent_amount": {"amount": 1, "currency": "eur"}},  # ISO 4217, upper case
        {"id": "../escape"},  # an id that would steer a later path
    ],
)
def test_malformed_expenses_are_refused_without_echoing_provider_values(
    broken: dict[str, object],
) -> None:
    with pytest.raises(RevolutAPIError) as caught:
        _client(_json([_expense(1, START, **broken)])).list_expenses()
    assert caught.value.code == "RESPONSE"
    assert "escape" not in str(caught.value) and "two" not in str(caught.value)


def test_a_non_list_expense_response_is_refused() -> None:
    with pytest.raises(RevolutAPIError) as caught:
        _client(_json({"expenses": []})).list_expenses()
    assert caught.value.code == "RESPONSE"


def test_expense_query_bounds_follow_the_published_contract() -> None:
    for count in (0, 501):
        with pytest.raises(ValidationError):
            ExpenseQuery(count=count)
    with pytest.raises(ValidationError):
        ExpenseQuery(from_=START, to=START)


def test_get_expense_reads_one_object() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=_expense(7, START))

    assert _client(handler).get_expense("exp-7").id == "exp-7"
    assert seen == ["/api/1.0/expenses/exp-7"]


@pytest.mark.parametrize(
    "bad", ["../x", "a/b", "a?b", "a#b", "%2e%2e", "", "a.b", "x" * 101]
)
def test_an_identifier_cannot_steer_the_path_and_nothing_is_sent(bad: str) -> None:
    client = _client(_never)
    with pytest.raises(ValueError, match="not a valid path segment"):
        client.get_expense(bad)
    with pytest.raises(ValueError, match="not a valid path segment"):
        client.download_receipt("exp-1", bad)
    with pytest.raises(ValueError, match="not a valid path segment"):
        client.list_labels(bad)


# -- receipts -------------------------------------------------------------------


def test_a_receipt_is_returned_whole_with_its_digest_and_kept_out_of_repr() -> None:
    body = b"%PDF-1.7 synthetic receipt"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, content=body, headers={"Content-Type": "application/pdf; name=r.pdf"}
        )

    receipt = _client(handler).download_receipt("exp-1", "rcpt-1")
    assert receipt.content == body
    assert receipt.sha256 == hashlib.sha256(body).hexdigest()
    assert receipt.media_type == "application/pdf"
    assert "synthetic" not in repr(receipt)
    (request,) = seen
    assert request.url.path == "/api/1.0/expenses/exp-1/receipts/rcpt-1/content"
    assert request.headers["Accept"] == "*/*"


def test_an_oversized_receipt_is_refused_and_reading_stops_early() -> None:
    chunk = b"\0" * (1024 * 1024)
    sent = [0]

    def chunks() -> Iterator[bytes]:
        for _ in range(64):  # a 64 MiB body, far over the limit
            sent[0] += len(chunk)
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunks())

    with pytest.raises(RevolutAPIError) as caught:
        _client(handler).download_receipt("exp-1", "rcpt-1")
    assert caught.value.code == "RESPONSE_TOO_LARGE"
    # Bounded during the transfer: reading stopped one chunk past the limit.
    assert sent[0] <= MAX_RECEIPT_BYTES + len(chunk)


def test_a_redirected_receipt_is_not_followed_and_the_token_goes_nowhere_else() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(
            302, headers={"Location": "https://attacker.example/steal"}
        )

    with pytest.raises(RevolutAPIError) as caught:
        _client(handler).download_receipt("exp-1", "rcpt-1")
    assert (caught.value.code, caught.value.status_code) == ("HTTP", 302)
    assert hosts == ["sandbox-b2b.revolut.com"]  # one request, to Revolut only


def test_a_receipt_that_echoes_the_credential_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=CANARY.encode())

    with pytest.raises(RevolutAPIError) as caught:
        _client(handler).download_receipt("exp-1", "rcpt-1")
    assert caught.value.code == "RESPONSE"


# -- labels ---------------------------------------------------------------------


def _named(identifier: str, name: str) -> dict[str, str]:
    return {
        "id": identifier,
        "name": name,
        "created_at": START.isoformat(),
        "updated_at": START.isoformat(),
    }


def test_label_pages_pass_the_token_back_unchanged() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/1.0/label-groups":
            return httpx.Response(
                200,
                json={
                    "label_groups": [_named(GROUP_ID, "Project")],
                    "next_page_token": CURSOR_2,
                },
            )
        return httpx.Response(200, json={"labels": [_named(LABEL_ID, "Dreamhuggers")]})

    client = _client(handler)
    groups = client.list_label_groups(LabelQuery(limit=10, page_token=CURSOR_1))
    assert [g.name for g in groups.label_groups] == ["Project"]
    assert groups.next_page_token == CURSOR_2
    labels = client.list_labels(GROUP_ID)
    assert [label.name for label in labels.labels] == ["Dreamhuggers"]
    assert labels.next_page_token is None
    assert dict(seen[0].url.params) == {"limit": "10", "page_token": CURSOR_1}
    assert seen[1].url.path == f"/api/1.0/label-groups/{GROUP_ID}/labels"


def test_a_label_token_that_does_not_advance_is_refused() -> None:
    payload = {"label_groups": [], "next_page_token": CURSOR_1}
    with pytest.raises(RevolutAPIError) as caught:
        _client(_json(payload)).list_label_groups(LabelQuery(page_token=CURSOR_1))
    assert caught.value.code == "PAGINATION_STALLED"


@pytest.mark.parametrize(
    "payload",
    [
        {"labels": "not-a-list"},
        {"next_page_token": None},  # the list is missing altogether
        {"labels": [{"id": "not-a-uuid", "name": "x"}]},
        {"labels": [], "next_page_token": "not base64 at all!"},
    ],
)
def test_malformed_label_pages_are_refused(payload: dict[str, object]) -> None:
    with pytest.raises(RevolutAPIError) as caught:
        _client(_json(payload)).list_labels(GROUP_ID)
    assert caught.value.code == "RESPONSE"


def test_label_limit_follows_the_published_contract() -> None:
    for limit in (0, 501):
        with pytest.raises(ValidationError):
            LabelQuery(limit=limit)


def test_the_transport_refuses_any_route_outside_the_read_integration() -> None:
    transport = RevolutTransport(
        SecretStr(CANARY),
        environment=RevolutEnvironment.SANDBOX,
        transport=httpx.MockTransport(_never),
    )
    for path in (
        "/payments",
        "/expenses/x/receipts",
        "/expenses/a/../b",
        "/label-groups/x",
    ):
        with pytest.raises(ValueError, match="outside the read integration"):
            transport.get_object(path)
