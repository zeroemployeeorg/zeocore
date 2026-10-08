"""Revolut Business reads only: accounts, transactions, expenses, receipts, labels."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import SecretStr, TypeAdapter, ValidationError

from .models import (
    MAX_RECEIPT_BYTES,
    MAX_RESULT_BYTES,
    Account,
    Expense,
    ExpensePage,
    ExpenseQuery,
    Label,
    LabelGroup,
    LabelGroupPage,
    LabelPage,
    LabelQuery,
    ProviderId,
    Receipt,
    RevolutEnvironment,
    Transaction,
    TransactionPage,
    TransactionQuery,
)
from .transport import RevolutAPIError, RevolutTransport

_ACCOUNTS = TypeAdapter(tuple[Account, ...])
_TRANSACTIONS = TypeAdapter(tuple[Transaction, ...])
_EXPENSES = TypeAdapter(tuple[Expense, ...])
_ID = TypeAdapter(ProviderId)
_TOO_LARGE = "Normalized Revolut result exceeded the size limit; nothing was returned"


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class RevolutBusinessClient:
    """Explicit read operations; credentials never appear in result objects."""

    def __init__(
        self,
        access_token: SecretStr | None = None,
        *,
        environment: RevolutEnvironment = RevolutEnvironment.PRODUCTION,
        transport: RevolutTransport | None = None,
    ) -> None:
        if transport is None:
            if access_token is None:
                raise ValueError("Revolut access token is required")
            transport = RevolutTransport(access_token, environment=environment)
        self._transport = transport

    def close(self) -> None:
        self._transport.close()

    def list_accounts(self) -> tuple[Account, ...]:
        try:
            accounts = _ACCOUNTS.validate_python(self._transport.get("/accounts"))
        except ValidationError:
            raise _unrecognized() from None
        if len(_ACCOUNTS.dump_json(accounts)) > MAX_RESULT_BYTES:
            raise RevolutAPIError("RESPONSE_TOO_LARGE", _TOO_LARGE)
        return accounts

    def list_transactions(
        self, query: TransactionQuery | None = None
    ) -> TransactionPage:
        """Return one observed page and the cursor for the next older page.

        There is no collect-everything form: the caller owns the loop, its
        checkpoint and replacement by transaction ``id``. Any error, including
        ``PAGINATION_STALLED`` and ``RESPONSE_TOO_LARGE``, means the window
        was not fully read: record an incomplete sync, never completion.
        """
        query = query or TransactionQuery()
        params: dict[str, str | int] = {"count": query.count}
        if query.from_ is not None:
            params["from"] = _instant(query.from_)
        if query.to is not None:
            params["to"] = _instant(query.to)
        if query.account_id is not None:
            params["account"] = str(query.account_id)
        if query.type is not None:
            params["type"] = query.type
        try:
            transactions = _TRANSACTIONS.validate_python(
                self._transport.get("/transactions", params=params)
            )
        except ValidationError:
            raise _unrecognized() from None
        observed_at = datetime.now(UTC)
        next_to = None
        if len(transactions) >= query.count:
            next_to = min(item.created_at for item in transactions)
            if query.to is not None and next_to >= query.to:
                raise RevolutAPIError(
                    "PAGINATION_STALLED",
                    "Revolut returned a full page that does not advance the cursor",
                )
        page = TransactionPage(
            transactions=transactions, observed_at=observed_at, next_to=next_to
        )
        # Never truncate: a partial page would silently lose matching evidence.
        if len(page.model_dump_json().encode()) > MAX_RESULT_BYTES:
            raise RevolutAPIError(
                "RESPONSE_TOO_LARGE", _TOO_LARGE + "; lower count or narrow the window"
            )
        return page

    def list_expenses(self, query: ExpenseQuery | None = None) -> ExpensePage:
        """Return one observed page of expenses and the cursor for the next older page.

        As with transactions, the caller owns the loop and replaces by ``id``.
        Any error means the window was not fully read. Reaching
        ``next_to=None`` does not prove the opposite: the cursor rests on an
        unverified date assumption, so every page reports
        ``completeness="unverified"`` (see ``ExpensePage``).
        """
        query = query or ExpenseQuery()
        params: dict[str, str | int] = {"count": query.count}
        if query.from_ is not None:
            params["from"] = _instant(query.from_)
        if query.to is not None:
            params["to"] = _instant(query.to)
        if query.state is not None:
            params["state"] = query.state
        if query.transaction_type is not None:
            params["transaction_type"] = query.transaction_type
        try:
            expenses = _EXPENSES.validate_python(
                self._transport.get("/expenses", params=params)
            )
        except ValidationError:
            raise _unrecognized() from None
        observed_at = datetime.now(UTC)
        next_to = None
        if len(expenses) >= query.count:
            next_to = min(item.expense_date for item in expenses)
            if query.to is not None and next_to >= query.to:
                raise RevolutAPIError(
                    "PAGINATION_STALLED",
                    "Revolut returned a full page that does not advance the cursor",
                )
        page = ExpensePage(expenses=expenses, observed_at=observed_at, next_to=next_to)
        if len(page.model_dump_json().encode()) > MAX_RESULT_BYTES:
            raise RevolutAPIError(
                "RESPONSE_TOO_LARGE", _TOO_LARGE + "; lower count or narrow the window"
            )
        return page

    def get_expense(self, expense_id: str) -> Expense:
        identifier = _identifier(expense_id)
        try:
            return Expense.model_validate(
                self._transport.get_object(f"/expenses/{identifier}")
            )
        except ValidationError:
            raise _unrecognized() from None

    def download_receipt(self, expense_id: str, receipt_id: str) -> Receipt:
        """One receipt file, read whole, bounded to ``MAX_RECEIPT_BYTES`` in transfer.

        Redirects are never followed, so the access token goes only to
        Revolut's fixed origin. The bytes are returned with their digest and
        are never parsed or logged.
        """
        expense, receipt = _identifier(expense_id), _identifier(receipt_id)
        content, media_type = self._transport.get_bytes(
            f"/expenses/{expense}/receipts/{receipt}/content",
            max_bytes=MAX_RECEIPT_BYTES,
        )
        return Receipt(
            expense_id=expense,
            receipt_id=receipt,
            media_type=media_type.split(";", 1)[0].strip()[:255]
            or "application/octet-stream",
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            observed_at=datetime.now(UTC),
        )

    def list_label_groups(self, query: LabelQuery | None = None) -> LabelGroupPage:
        query = query or LabelQuery()
        data = self._transport.get_object("/label-groups", params=_label_params(query))
        try:
            page = LabelGroupPage(
                label_groups=tuple(
                    LabelGroup.model_validate(item)
                    for item in _items(data, "label_groups")
                ),
                next_page_token=data.get("next_page_token") or None,
                observed_at=datetime.now(UTC),
            )
        except ValidationError:
            raise _unrecognized() from None
        _token_advances(query, page.next_page_token)
        _bounded(page.model_dump_json())
        return page

    def list_labels(self, group_id: str, query: LabelQuery | None = None) -> LabelPage:
        query = query or LabelQuery()
        group = _identifier(group_id)
        data = self._transport.get_object(
            f"/label-groups/{group}/labels", params=_label_params(query)
        )
        try:
            page = LabelPage(
                labels=tuple(
                    Label.model_validate(item) for item in _items(data, "labels")
                ),
                next_page_token=data.get("next_page_token") or None,
                observed_at=datetime.now(UTC),
            )
        except ValidationError:
            raise _unrecognized() from None
        _token_advances(query, page.next_page_token)
        _bounded(page.model_dump_json())
        return page


def _identifier(value: str) -> str:
    """A provider identifier as one safe path segment, or a refusal."""

    try:
        return _ID.validate_python(value)
    except ValidationError:
        raise ValueError("Revolut identifier is not a valid path segment") from None


def _label_params(query: LabelQuery) -> dict[str, str | int]:
    params: dict[str, str | int] = {"limit": query.limit}
    if query.page_token is not None:
        params["page_token"] = query.page_token
    return params


def _items(data: dict[str, object], key: str) -> list[object]:
    items = data.get(key)
    if not isinstance(items, list):
        raise _unrecognized()
    return items


def _token_advances(query: LabelQuery, next_token: str | None) -> None:
    if next_token is not None and next_token == query.page_token:
        raise RevolutAPIError(
            "PAGINATION_STALLED",
            "Revolut returned the same page token it was given",
        )


def _bounded(serialized: str) -> None:
    if len(serialized.encode()) > MAX_RESULT_BYTES:
        raise RevolutAPIError("RESPONSE_TOO_LARGE", _TOO_LARGE + "; lower limit")


def _unrecognized() -> RevolutAPIError:
    # Validation detail can quote provider values; it is deliberately discarded.
    return RevolutAPIError(
        "RESPONSE", "Revolut response did not match the reviewed read contract"
    )
