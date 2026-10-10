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
# Below the 1 MiB hosted JSON limit, leaving room for the response envelope.
MAX_RESULT_BYTES = 768 * 1024

Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
# Provider enumerations grow; a new value must not fail a whole bank page.
ProviderToken = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ProviderText = Annotated[str, StringConstraints(max_length=2000)]


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
    """One bounded page. ``to`` is the cursor returned by the previous page.

    On the wire (ZEOconnect, contract 1.0.0 §6) the start field is ``from``:
    dump with ``by_alias=True``. Python callers keep writing ``from_``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_by_name=True)

    from_: AwareDatetime | None = Field(default=None, alias="from")
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
