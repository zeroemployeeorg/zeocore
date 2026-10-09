"""Revolut reads through ZEOconnect: the two wire rules of contract 1.0.0 §6."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from zeo_core.integrations.hosted import (
    HostedClientError,
    HostedConnectionClient,
    HostedOperationRequest,
    HostedOperationResponse,
    HostedStoppedError,
)
from zeo_core.integrations.revolut import HostedRevolutBusinessClient, TransactionQuery
from zeo_core.integrations.revolut.hosted import ACCOUNTS_LIST, TRANSACTIONS_READ_PAGE

ACCOUNT_ID = "2a0d4d05-4d4f-4f3a-9a53-1a0f6f6d3b11"
LEG_ID = "5b1c7f0e-0c6e-4f0b-8a51-7e7a2b9d1c22"
SINCE = datetime(2026, 9, 1, tzinfo=UTC)


class _Transport:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.requests: list[HostedOperationRequest] = []

    def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse:
        self.requests.append(request)
        return HostedOperationResponse.model_validate(self.answer)

    def fetch_artifact(self, *, artifact_id: str, max_bytes: int) -> bytes:
        raise AssertionError("reads carry no artifact")


def _client(answer: dict[str, Any]) -> tuple[HostedRevolutBusinessClient, _Transport]:
    fake = _Transport(answer)
    client = HostedRevolutBusinessClient(
        HostedConnectionClient(transport=fake), connection_id="con_revolut_1"
    )
    return client, fake


def _confirmed(result: object) -> dict[str, Any]:
    return {"status": "confirmed", "execution_id": "exe-1", "result": result}


def _account(balance: object = "1234.56") -> dict[str, Any]:
    return {
        "id": ACCOUNT_ID,
        "balance": balance,
        "currency": "EUR",
        "state": "active",
        "public": False,
        "created_at": "2025-01-02T03:04:05Z",
        "updated_at": "2025-06-02T03:04:05Z",
    }


def _page(amount: object = "-0.30") -> dict[str, Any]:
    return {
        "transactions": [
            {
                "id": "tx-1",
                "type": "card_payment",
                "state": "completed",
                "created_at": "2026-09-02T10:00:00Z",
                "updated_at": "2026-09-02T10:00:00Z",
                "legs": [
                    {
                        "leg_id": LEG_ID,
                        "account_id": ACCOUNT_ID,
                        "amount": amount,
                        "currency": "EUR",
                    }
                ],
            }
        ],
        "observed_at": "2026-09-03T00:00:00Z",
        "next_to": None,
    }


def test_the_query_start_field_is_from_on_the_wire() -> None:
    client, fake = _client(_confirmed(_page()))
    client.list_transactions(TransactionQuery(from_=SINCE, count=50))
    sent = fake.requests[0]
    assert sent.operation_id == TRANSACTIONS_READ_PAGE
    assert sent.arguments == {"from": "2026-09-01T00:00:00Z", "count": 50}
    # Python callers may also pass the wire name.
    assert TransactionQuery.model_validate({"from": SINCE}).from_ == SINCE


def test_string_amounts_stay_exact() -> None:
    client, _ = _client(_confirmed(_page("-0.30")))
    leg = client.list_transactions().transactions[0].legs[0]
    assert leg.amount == Decimal("-0.30") and str(leg.amount) == "-0.30"
    accounts, fake = _client(_confirmed([_account("0.10")]))
    assert accounts.list_accounts()[0].balance == Decimal("0.10")
    assert fake.requests[0].operation_id == ACCOUNTS_LIST
    assert fake.requests[0].arguments == {}


@pytest.mark.parametrize("amount", [-0.3, 5])
def test_a_json_number_amount_is_refused_never_rounded(amount: object) -> None:
    client, _ = _client(_confirmed(_page(amount)))
    with pytest.raises(HostedClientError, match="exact decimal strings"):
        client.list_transactions()
    accounts, _ = _client(_confirmed([_account(amount)]))
    with pytest.raises(HostedClientError, match="exact decimal strings"):
        accounts.list_accounts()


def test_each_read_has_a_fresh_idempotency_key() -> None:
    client, fake = _client(_confirmed([_account()]))
    client.list_accounts()
    client.list_accounts()
    first, second = (request.idempotency_key for request in fake.requests)
    assert first != second


@pytest.mark.parametrize("code", ["STOPPED", "REQUEST_REFUSED"])
def test_an_orchestrated_stop_is_reported_as_a_stop(code: str) -> None:
    client, _ = _client(
        {
            "status": "failed_safe",
            "execution_id": "exe-1",
            "normalized_error": {"code": code, "message": "stopped:dispatch:org-1"},
        }
    )
    with pytest.raises(HostedStoppedError) as caught:
        client.list_accounts()
    assert (caught.value.control, caught.value.scope) == ("dispatch", "org-1")


def test_a_non_confirmed_read_or_an_unknown_shape_is_an_error() -> None:
    refused, _ = _client({"status": "refused", "execution_id": "exe-1"})
    with pytest.raises(HostedClientError, match="was refused"):
        refused.list_accounts()
    garbled, _ = _client(_confirmed({"accounts": []}))
    with pytest.raises(HostedClientError, match="reviewed read contract"):
        garbled.list_accounts()
