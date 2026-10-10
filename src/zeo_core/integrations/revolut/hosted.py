"""Revolut Business reads through ZEOconnect, read-only, never a payment.

The Broker holds the Revolut credential; this client only names the operation
(Broker contract 1.0.0 §6). Two wire rules bind it: the query's start field is
``from``, and every monetary amount arrives as a JSON string holding an exact
decimal. A JSON number anywhere in a result is refused, never rounded through
a float.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

from pydantic import JsonValue, TypeAdapter, ValidationError

from zeo_core.integrations.hosted.client import (
    HostedClientError,
    HostedConnectionClient,
    HostedOperationRequest,
    HostedOperationResponse,
    HostedOperationStatus,
    stop_of,
)

from .models import MAX_RESULT_BYTES, Account, TransactionPage, TransactionQuery

ACCOUNTS_LIST = "revolut.business.accounts.list"
TRANSACTIONS_READ_PAGE = "revolut.business.transactions.read_page"
_ACCOUNTS = TypeAdapter(tuple[Account, ...])


class HostedRevolutBusinessClient:
    """The same reads as ``RevolutBusinessClient``, with custody at ZEOconnect."""

    def __init__(self, client: HostedConnectionClient, *, connection_id: str) -> None:
        self._client = client
        self._connection_id = connection_id

    def list_accounts(self) -> tuple[Account, ...]:
        result = self._read(ACCOUNTS_LIST, {})
        try:
            return _ACCOUNTS.validate_python(result)
        except ValidationError:
            raise _unrecognized() from None

    def list_transactions(
        self, query: TransactionQuery | None = None
    ) -> TransactionPage:
        """One page, as the direct client returns it; the caller owns the loop."""
        arguments = (query or TransactionQuery()).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        result = self._read(TRANSACTIONS_READ_PAGE, arguments)
        try:
            page = TransactionPage.model_validate(result)
        except ValidationError:
            raise _unrecognized() from None
        if len(page.model_dump_json().encode()) > MAX_RESULT_BYTES:
            raise HostedClientError("hosted Revolut result exceeds the size limit")
        return page

    def _read(self, operation_id: str, arguments: dict[str, JsonValue]) -> JsonValue:
        response = self._client.invoke(
            HostedOperationRequest(
                connection_id=self._connection_id,
                operation_id=operation_id,
                arguments=arguments,
                # A read has no effect to deduplicate: every call is fresh.
                idempotency_key=f"revolut-read:{uuid.uuid4()}",
            )
        )
        if response.status is not HostedOperationStatus.CONFIRMED:
            raise _not_confirmed(response)
        if response.result is None:
            raise _unrecognized()
        if _has_number(response.result):
            raise HostedClientError(
                "hosted Revolut amounts must be exact decimal strings"
            )
        return response.result


def _not_confirmed(response: HostedOperationResponse) -> HostedClientError:
    return stop_of(response) or HostedClientError(
        f"hosted Revolut read was {response.status.value}"
    )


def _has_number(value: JsonValue) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, Mapping):
        return any(_has_number(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str):
        return any(_has_number(item) for item in value)
    return False


def _unrecognized() -> HostedClientError:
    # Validation detail can quote provider values; it is deliberately discarded.
    return HostedClientError(
        "hosted Revolut response did not match the reviewed read contract"
    )


__all__ = [
    "ACCOUNTS_LIST",
    "TRANSACTIONS_READ_PAGE",
    "HostedRevolutBusinessClient",
]
