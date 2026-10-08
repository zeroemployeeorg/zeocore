"""Credential-free Revolut Business read models under one declared normalization.

Provider fields that are not declared here are dropped, never passed through.
The result is normalized application data: it is not raw provider evidence and
must not be labelled as such.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

NORMALIZATION_VERSION: Final = "revolut-business-read-1"
MAX_TRANSACTION_COUNT = 1000
# Revolut's published bound for one expense page and for one label page.
MAX_EXPENSE_COUNT = 500
MAX_LABEL_LIMIT = 500
# A receipt is a file, bounded while it is read. Equal to the hosted artifact
# limit, so a receipt that fits here also fits through ZEOconnect.
MAX_RECEIPT_BYTES = 10 * 1024 * 1024
# Below the 1 MiB hosted JSON limit, leaving room for the response envelope.
MAX_RESULT_BYTES = 768 * 1024

Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
# Provider enumerations grow; a new value must not fail a whole bank page.
ProviderToken = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ProviderText = Annotated[str, StringConstraints(max_length=2000)]
# Opaque provider identifiers that become URL path segments. The character set
# leaves no way to add a segment, a query or a traversal to the path.
ProviderId = Annotated[
    str, StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
]
# Revolut's label pagination token: opaque base64, passed back unchanged.
PageToken = Annotated[
    str,
    StringConstraints(min_length=1, max_length=2048, pattern=r"^[A-Za-z0-9+/=_-]+$"),
]


class RevolutEnvironment(StrEnum):
    """Selects one of two fixed provider origins; never a caller-supplied URL."""

    PRODUCTION = "production"
    SANDBOX = "sandbox"


class _Normalized(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class Account(_Normalized):
    id: UUID
    name: ProviderText | None = None
    balance: Decimal
    currency: Currency
    state: ProviderToken
    public: bool
    account_type: ProviderToken | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Merchant(_Normalized):
    id: UUID | None = None
    name: ProviderText | None = None
    full_name: ProviderText | None = None
    city: ProviderText | None = None
    category_code: ProviderText | None = None
    country: ProviderText | None = None


class Counterparty(_Normalized):
    id: UUID | None = None
    account_id: UUID | None = None
    account_type: ProviderToken | None = None


class CardReference(_Normalized):
    """Card identity only: holder name, phone and card number are dropped."""

    id: UUID | None = None


class TransactionLeg(_Normalized):
    leg_id: UUID
    account_id: UUID
    amount: Decimal
    currency: Currency
    fee: Decimal | None = None
    bill_amount: Decimal | None = None
    bill_currency: Currency | None = None
    balance: Decimal | None = None
    counterparty: Counterparty | None = None
    description: ProviderText | None = None


class Transaction(_Normalized):
    id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    type: ProviderToken
    state: ProviderToken
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    scheduled_for: date | None = None
    request_id: ProviderText | None = None
    reason_code: ProviderText | None = None
    related_transaction_id: UUID | None = None
    reference: ProviderText | None = None
    merchant: Merchant | None = None
    card: CardReference | None = None
    legs: tuple[TransactionLeg, ...]


class TransactionQuery(BaseModel):
    """One bounded page. ``to`` is the cursor returned by the previous page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_: AwareDatetime | None = None
    to: AwareDatetime | None = None
    account_id: UUID | None = None
    type: ProviderToken | None = None
    count: int = Field(default=100, ge=1, le=MAX_TRANSACTION_COUNT)

    @model_validator(mode="after")
    def _window_is_ordered(self) -> TransactionQuery:
        if self.from_ is not None and self.to is not None and self.from_ >= self.to:
            raise ValueError("transaction window must start before it ends")
        return self


class TransactionPage(_Normalized):
    """One observation of provider state, not a stable snapshot.

    Pages may overlap at ``next_to`` and a transaction may change between
    observations. Key by ``id``; the projection with the later ``updated_at``
    replaces the earlier one, and ``observed_at`` dates each observation.
    """

    transactions: tuple[Transaction, ...]
    observed_at: AwareDatetime
    next_to: datetime | None = None
    normalization_version: Literal["revolut-business-read-1"] = NORMALIZATION_VERSION


# -- Expenses -------------------------------------------------------------------


class Money(_Normalized):
    amount: Decimal
    currency: Currency


class SplitAmount(_Normalized):
    """A split's amount; Revolut's contract makes both parts optional."""

    amount: Decimal | None = None
    currency: Currency | None = None


class AccountingCategoryReference(_Normalized):
    id: ProviderId
    name: ProviderText
    code: ProviderText | None = None


class TaxRateReference(_Normalized):
    id: ProviderId
    name: ProviderText
    percentage: Decimal | None = None


class ExpenseSplit(_Normalized):
    amount: SplitAmount
    category: AccountingCategoryReference | None = None
    tax_rate: TaxRateReference | None = None


# One label name per label group, at most five groups, as Revolut publishes it.
ExpenseLabels = Annotated[
    dict[
        ProviderText, Annotated[tuple[ProviderText], Field(min_length=1, max_length=1)]
    ],
    Field(max_length=5),
]


class Expense(_Normalized):
    """An expense as Revolut records it, minus the payer's name.

    ``payer`` is a person's name. As with a card holder's name, it is dropped.
    """

    id: ProviderId
    state: ProviderToken
    transaction_type: ProviderToken
    expense_date: AwareDatetime
    submitted_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    merchant: ProviderText | None = None
    transaction_id: ProviderId | None = None
    labels: ExpenseLabels
    splits: tuple[ExpenseSplit, ...]
    receipt_ids: tuple[ProviderId, ...]
    spent_amount: Money


class ExpenseQuery(BaseModel):
    """One bounded page of expenses. ``to`` is the previous page's cursor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_: AwareDatetime | None = None
    to: AwareDatetime | None = None
    state: ProviderToken | None = None
    transaction_type: ProviderToken | None = None
    count: int = Field(default=100, ge=1, le=MAX_EXPENSE_COUNT)

    @model_validator(mode="after")
    def _window_is_ordered(self) -> ExpenseQuery:
        if self.from_ is not None and self.to is not None and self.from_ >= self.to:
            raise ValueError("expense window must start before it ends")
        return self


class ExpensePage(_Normalized):
    """One observation of expenses; key by ``id``, as with transactions.

    ``next_to`` is derived from the oldest ``expense_date`` on a full page.
    Revolut's published contract does not state which date ``from``/``to``
    filter on. ``expense_date`` is the assumption, and it is unverified until
    a live sandbox run.
    """

    expenses: tuple[Expense, ...]
    observed_at: AwareDatetime
    next_to: datetime | None = None
    normalization_version: Literal["revolut-business-read-1"] = NORMALIZATION_VERSION


# -- Receipts -------------------------------------------------------------------


class Receipt(BaseModel):
    """One receipt file, read whole and bounded. Its bytes never appear in repr."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expense_id: ProviderId
    receipt_id: ProviderId
    media_type: Annotated[str, StringConstraints(max_length=255)]
    content: bytes = Field(repr=False, max_length=MAX_RECEIPT_BYTES)
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    observed_at: AwareDatetime


# -- Labels ---------------------------------------------------------------------


class LabelGroup(_Normalized):
    id: UUID
    name: ProviderText
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Label(_Normalized):
    id: UUID
    name: ProviderText
    created_at: AwareDatetime
    updated_at: AwareDatetime


class LabelQuery(BaseModel):
    """One page of labels or label groups; ``page_token`` comes from the last page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: int = Field(default=100, ge=1, le=MAX_LABEL_LIMIT)
    page_token: PageToken | None = None


class LabelGroupPage(_Normalized):
    label_groups: tuple[LabelGroup, ...]
    next_page_token: PageToken | None = None
    observed_at: AwareDatetime


class LabelPage(_Normalized):
    labels: tuple[Label, ...]
    next_page_token: PageToken | None = None
    observed_at: AwareDatetime
