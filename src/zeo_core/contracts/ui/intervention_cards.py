"""The ``intervention_card`` payload: one closed card for an operator situation.

A card names ONE situation (a work location, a checkpoint, a blocker, a decision,
a request status, an outcome, a project checkpoint, or a read-only source item) and
carries only DATA: a subject, evidence, per-source freshness, the facts of that
situation and the typed action ids the backend may offer. It holds no intervention
state, grants no authority and computes nothing (no age, no lateness, no order).
Every durable record, every authority check and every stale check belongs to the
services that own them; a card only shows what they supplied.

The payload is the ``DataRef.payload`` of a ``widget.upsert`` whose component is
``intervention_card``. Rules that hold for every field:

* A model is closed: an unknown key is an error and nothing is coerced.
* An optional key is ABSENT when unknown, never ``null`` (as in ``card.py``).
* A text field refuses markup and control characters; a reference is a short
  identifier, never a URL (the one exception, an evidence link, is display text).
* ``route`` on an action is descriptive: the backend decides authority from the
  action id and the authenticated person, and ignores a client-supplied route.

This module imports nothing from ``widgets.py`` so that ``widgets.py`` can register
the component without an import cycle; ``tests/test_contracts`` checks that the text
and date rules copied here agree with the ones in ``widgets.py``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictBool,
    model_validator,
)

from zeo_core.contracts.ui.card import LongText

__all__ = [
    "IMAGE_MAX_BYTES",
    "IMAGE_MAX_DIMENSION",
    "IMAGE_MAX_PIXELS",
    "PAIR_MAX_BYTES",
    "Action",
    "Approval",
    "Blocker",
    "Checkpoint",
    "Decision",
    "InterventionCard",
    "InterventionCardPayload",
    "Message",
    "Outcome",
    "ProjectCheckpoint",
    "RequestStatus",
    "SourceItem",
    "WorkLocation",
]

# -- text and time (the rules of widgets.py, kept in step by a test) -------------

_DATE_TIME: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})"
)
_NO_MARKUP: Final = re.compile(r"[^<>]*")
#: Control (Cc), format (Cf: bidi, zero-width, tags) and the line and paragraph
#: separators (Zl, Zp), as ranges so that no table lookup is needed.
_REFUSED: Final = re.compile(
    "[\u0000-\u001f\u007f-\u009f­؀-؅؜۝܏"
    "࢐࢑࣢᠎​-‏ -‮⁠-⁤"
    "⁦-⁯﻿￹-￻\U000110bd\U000110cd\U00013430-\U0001343f"
    "\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0001\U000e0020-\U000e007f]"
)


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


def _plain_long(value: str) -> str:
    """Long text with no control or format character; ``<`` and ``>`` are fine.

    A body or a preview may quote an address in angle brackets. Nothing here is markup:
    a consumer shows every string as text.
    """
    if _REFUSED.search(value.replace("\n", "")):
        msg = "text holds a control or format character"
        raise ValueError(msg)
    return value


DateTime = Annotated[str, Field(max_length=40), AfterValidator(_date_time)]
Text = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_plain)]
#: A message body or an approval preview: S1 ``LongText`` (at most 4096 UTF-8 bytes,
#: line feed allowed) plus the ``Text`` refusals of control and format characters.
#: Unlike ``Text`` it allows ``<`` and ``>``.
PlainLong = Annotated[LongText, Field(min_length=1), AfterValidator(_plain_long)]
Text120 = Annotated[str, Field(min_length=1, max_length=120), AfterValidator(_plain)]
Text300 = Annotated[str, Field(min_length=1, max_length=300), AfterValidator(_plain)]

# -- identifiers -------------------------------------------------------------------

#: A stable identifier of the services that mint it (fake ones in the fixtures).
Ident = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,62}$")]
#: A short reference: card.py's ``OwnerRef`` class, so no ``#`` and no ``@``.
Ref = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:/-]{1,128}$")]
_ULID: Final = re.compile(r"wi_[0-7][0-9A-HJKMNP-TV-Z]{25}")


def _work_id(value: str) -> str:
    if value.startswith("wi_") and not _ULID.fullmatch(value):
        msg = "wi_ is reserved for a canonical ULID work id"
        raise ValueError(msg)
    return value


#: A work id. ``wi_`` is RESERVED: an id that starts with it must be a canonical
#: ULID (26 uppercase Crockford characters, the first one 0-7 so that the 128-bit
#: value fits). A pre-existing source id (a SOW id, ``repo#PR``, a commitment id)
#: may hold ``#``. Never lowercased, never normalised. (The regex engine has no
#: look-ahead, so the reserved prefix is checked here, not in the pattern.)
WorkId = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9._:/#-]{1,128}$"),
    AfterValidator(_work_id),
]
#: Display data for a pre-existing source: the work-id class, never ``@``.
SourceRef = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:/#-]{1,128}$")]
#: ``sha256:`` and 64 lowercase hex: an artifact digest, never a counter.
# Quotes are written as \x22 and \x27: valid in Python and in an ECMAScript ``u`` regex.
_HTTPS_URL: Final = r"^https://[^\s<>\x22\x27]{1,500}$"
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
#: An opaque media reference of the App: no ``:`` and no ``/``, so a URL or a path
#: cannot satisfy it.
MediaRef = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")]
RunId = Annotated[str, Field(max_length=40, pattern=r"^run-[0-9]{8}T[0-9]{4}Z$")]
ProjectRef = Annotated[str, Field(max_length=64, pattern=r"^[a-z0-9-]{1,64}$")]
SeatName = Annotated[str, Field(max_length=64, pattern=r"^[A-Za-z0-9._-]{1,64}$")]
MailId = Annotated[str, Field(max_length=128, pattern=r"^[A-Za-z0-9._:/-]{1,128}$")]
#: A PR or commit link kept as DISPLAY text: https only, never fetched here.
EvidenceLink = Annotated[
    str,
    Field(
        max_length=300,
        pattern=r"^https://[A-Za-z0-9][A-Za-z0-9._~:/?#@!$&()*+,;=%\[\]-]*$",
    ),
]
Int31 = Annotated[int, Field(ge=0, le=2**31 - 1)]
Hours = Annotated[float, Field(ge=0, allow_inf_nan=False)]

SourceKind = Literal["operation", "receipt", "document", "source"]
SourceState = Literal["healthy", "degraded", "error", "unknown", "stale", "unavailable"]
YesNoUnknown = Literal["yes", "no", "unknown"]

#: The eight interventions a card may name. There is no other type.
ActionType = Literal[
    "locate_work",
    "request_checkpoint",
    "request_update",
    "explain_blocker",
    "request_unblock",
    "verify_delivery",
    "request_recovery_plan",
    "escalate",
]
#: Ordinary task operations (a closed list owned by the task service). The card
#: carries the id and never the behaviour.
TaskOperation = Literal[
    "task.create_request",
    "task.acknowledge",
    "task.report_blocker",
    "task.submit_evidence",
    "task.record_answer",
    "day.propose",
    "day.accept",
    "day.add_today",
    "day.schedule",
    "day.defer",
    "day.delegate",
    "day.decline",
    "day.session_propose",
    "day.session_accept",
]
#: The operator's reserved decisions (the maintainers' allowlist v1). Additions need
#: a dated maintainer decision. This lists names; it activates nothing.
ReservedOperation = Literal[
    "decision.publishing_notice",
    "decision.public_release",
    "decision.spend",
    "decision.repo_visibility",
    "decision.production_go",
    "decision.gold_tag",
    "decision.external_application",
    "decision.outside_org_write",
    "decision.brand_rule",
    "review.own_content",
]
#: A project card offers only these three interventions.
CHECKPOINT_ACTIONS: Final[tuple[ActionType, ...]] = (
    "request_checkpoint",
    "request_unblock",
    "explain_blocker",
)

#: 8 MiB per image, 16 MiB for the pair, 4096 on a side, 16 Mpixel (the App's
#: profile; it enforces authority, bytes, digest and frame validation).
IMAGE_MAX_BYTES: Final = 8 * 1024 * 1024
PAIR_MAX_BYTES: Final = 16 * 1024 * 1024
IMAGE_MAX_DIMENSION: Final = 4096
IMAGE_MAX_PIXELS: Final = 16_777_216


class _Closed(BaseModel):
    """Closed, strict, immutable: an unknown key is an error, nothing is coerced."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Opt(_Closed):
    """A model whose optional keys are absent, never ``null``."""

    @model_validator(mode="before")
    @classmethod
    def _no_null(cls, data: object) -> object:
        if isinstance(data, dict) and any(v is None for v in data.values()):
            msg = "an optional key is absent, never null"
            raise ValueError(msg)
        return data


# -- the parts every card shares ---------------------------------------------------


class SubjectRef(_Opt):
    project_id: Ident
    seat_id: Ident
    assignment_id: Ident
    actor_id: Ident | None = None  # absent when no actor is bound yet


class Evidence(_Closed):
    kind: SourceKind
    ref: Ref  # a short id; full URLs and digests are resolved elsewhere


class Freshness(_Closed):
    source: Ref
    observed_at: DateTime
    collected_at: DateTime
    state: SourceState  # per source: a stale source is shown stale


class WorkRef(_Closed):
    """One id for a work item on every view; ``intervention_ref`` is the SAME id."""

    work_id: WorkId
    source_ref: SourceRef  # presentation data only
    revision: Digest  # the artifact digest; ``work_id@revision`` is built elsewhere


class Action(_Opt):
    """One typed action id. The route says which closed list the name belongs to."""

    route: Literal["intervention", "reserved", "task"]
    action_id: Ident  # the typed id the backend rechecks; never an instruction
    type: ActionType | None = None  # route intervention
    reserved_operation: ReservedOperation | None = None  # route reserved
    task_operation: TaskOperation | None = None  # route task
    label: Text
    enabled: StrictBool  # a real JSON boolean
    reason: Text | None = None  # required when ``enabled`` is false

    @model_validator(mode="after")
    def _the_name_of_the_route(self) -> Self:
        named = {
            "intervention": self.type,
            "reserved": self.reserved_operation,
            "task": self.task_operation,
        }
        others = (v for k, v in named.items() if k != self.route)
        if named[self.route] is None or any(v is not None for v in others):
            msg = "exactly the one name that belongs to the stated route"
            raise ValueError(msg)
        return self


def _offered(primary: Action | None, secondary: list[Action]) -> list[Action]:
    return [a for a in [primary, *secondary] if a is not None]


def _check_disabled(actions: list[Action]) -> None:
    if any(not a.enabled and a.reason is None for a in actions):
        msg = "a disabled action states its reason"
        raise ValueError(msg)


class _Card(_Opt):
    subject_ref: SubjectRef
    subject_revision: Int31  # the service's counter; the only stale-check value
    work_ref: WorkRef | None = None  # absent when the card is not about a work item
    intervention_ref: WorkId | None = None  # the SAME id as ``work_ref.work_id``
    evidence: Annotated[list[Evidence], Field(max_length=8)]
    freshness: Annotated[list[Freshness], Field(max_length=8)]
    primary_action: Action | None = None  # at most one primary action per card
    secondary_actions: Annotated[list[Action], Field(max_length=3)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _one_id(self) -> Self:
        if self.intervention_ref is None:
            return self
        if self.work_ref is None:
            msg = "intervention_ref needs work_ref"
            raise ValueError(msg)
        if self.intervention_ref != self.work_ref.work_id:
            msg = "intervention_ref is the same id as work_ref.work_id"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _disabled_actions_say_why(self) -> Self:
        _check_disabled(_offered(self.primary_action, self.secondary_actions))
        return self


# -- the six situations ------------------------------------------------------------


class ArtifactRef(_Closed):
    kind: Literal["repo", "branch", "commit", "pr", "deployment", "local_only"]
    ref: Ref


class WorkLocation(_Card):
    situation: Literal["work_location"]
    claimed_work: Text
    artifact_refs: Annotated[list[ArtifactRef], Field(max_length=8)]
    location_state: Literal["published", "local_only", "unknown"]


class Checkpoint(_Card):
    situation: Literal["checkpoint"]
    requested_at: DateTime
    due_at: DateTime | None = None  # absent when there is no explicit deadline
    delivery: Literal["pending", "delivered", "uncertain", "refused"]
    response: Literal["awaiting", "acknowledged", "evidence_returned"]


class Blocker(_Card):
    situation: Literal["blocker"]
    reported_blocker: Text
    since: DateTime
    depends_on: Ident | None = None
    release_condition: Text | None = None


class Option(_Closed):
    """Read data: no action executes a choice until an owner operation is agreed."""

    id: Ident
    label: Text


class ChoiceAnswer(_Closed):
    kind: Literal["choice"]
    options: Annotated[list[Option], Field(min_length=2, max_length=4)]


class IntegerAnswer(_Opt):
    """A bounded whole number with presets and, optionally, another number.

    ``recommended`` is display data: never a default, never pre-filled. The chosen
    value goes to the backend through the typed answer action, not through the card.
    """

    kind: Literal["bounded_integer"]
    unit: Literal["minutes", "hours", "days", "count"]
    minimum: Int31
    maximum: Annotated[int, Field(ge=1, le=2**31 - 1)]
    presets: Annotated[list[Int31], Field(max_length=6)]
    allow_other: StrictBool
    recommended: Int31 | None = None

    @model_validator(mode="after")
    def _within_bounds(self) -> Self:
        inside = range(self.minimum, self.maximum + 1)
        if self.minimum > self.maximum:
            msg = "minimum is above maximum"
            raise ValueError(msg)
        if any(v not in inside for v in self.presets):
            msg = "a preset is outside the bounds"
            raise ValueError(msg)
        if len(set(self.presets)) != len(self.presets):
            msg = "duplicate preset"
            raise ValueError(msg)
        if self.recommended is not None and self.recommended not in inside:
            msg = "recommended is outside the bounds"
            raise ValueError(msg)
        if not self.presets and not self.allow_other:
            msg = "an answer needs presets or another number"
            raise ValueError(msg)
        return self


class ArtifactToReview(_Closed):
    """What an approval would bind to; the binding itself is the service's."""

    ref: Ref
    revision: Digest


class ReviewImage(_Opt):
    """Metadata of one image the App serves behind sign-in; never bytes, never a URL."""

    media_ref: MediaRef
    label: Text
    alt: Text  # what the image shows, for the phone reader
    sha256: Digest  # of the exact served original bytes
    byte_length: Annotated[int, Field(ge=1, le=IMAGE_MAX_BYTES)]
    content_type: Literal["image/png", "image/jpeg", "image/webp"]  # static only
    width: Annotated[int, Field(ge=1, le=IMAGE_MAX_DIMENSION)]
    height: Annotated[int, Field(ge=1, le=IMAGE_MAX_DIMENSION)]

    @model_validator(mode="after")
    def _pixels(self) -> Self:
        if self.width * self.height > IMAGE_MAX_PIXELS:
            msg = "more pixels than the profile allows"
            raise ValueError(msg)
        return self


class ChecklistStep(_Closed):
    """Read data: the step text and whether it is done; nothing executes from here."""

    text: Text
    done: StrictBool


class Session(_Closed):
    start: DateTime
    duration_minutes: Annotated[int, Field(ge=1, le=1440)]


class PinnedText(_Opt):
    """Display-only text the person is told to use, pinned by the sha256 of its bytes.

    Never run, never an instruction: the card has no execute action. The text is
    card.py's class L (at most 4096 UTF-8 BYTES, LF allowed).
    """

    label: Text
    language: Literal["sql", "text"]
    text: LongText
    sha256: Digest

    @model_validator(mode="after")
    def _pinned(self) -> Self:
        digest = "sha256:" + hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.sha256 != digest:
            msg = "the digest is not the sha256 of the text"
            raise ValueError(msg)
        return self


class ResultSlot(_Opt):
    """One typed, bounded field the person fills in; its VALUE is never on the card.

    The value goes to the backend through the typed answer action, which re-reads
    the bounds from the owning task policy (a client-supplied bound is never
    trusted). Over a bound the backend refuses; the card only states it.
    """

    slot_id: Ident
    label: Text
    value_type: Literal["text", "timestamp", "confirm"]
    max_utf8_bytes: Annotated[int, Field(ge=1, le=262144)] | None = None  # text only
    max_data_rows: Annotated[int, Field(ge=1, le=4096)] | None = None  # text only
    required: StrictBool = True

    @model_validator(mode="after")
    def _bounds_belong_to_text(self) -> Self:
        text = self.value_type == "text"
        if text != (self.max_utf8_bytes is not None):
            msg = "a text slot has max_utf8_bytes; the other slots have none"
            raise ValueError(msg)
        if self.max_data_rows is not None and not text:
            msg = "max_data_rows belongs to a text slot"
            raise ValueError(msg)
        return self


def _check_do_only(card: Decision) -> None:
    extras = (
        card.result_slots,
        card.pinned_texts,
        card.result_max_total_bytes,
        card.target_label,
    )
    if card.ask_type != "do" and any(v is not None for v in extras):
        msg = "result slots, aggregate bound, target and pinned texts belong to do"
        raise ValueError(msg)
    if card.result_max_total_bytes is None:
        return
    if card.result_slots is None:
        msg = "an aggregate bound needs slots"
        raise ValueError(msg)
    if card.result_max_total_bytes > sum(
        s.max_utf8_bytes or 0 for s in card.result_slots
    ):
        msg = "the aggregate bound is above the sum of the slot bounds"
        raise ValueError(msg)


def _check_images(card: Decision) -> None:
    if card.review_images is None:
        return
    if card.ask_type != "review":
        msg = "review_images belong to a review ask"
        raise ValueError(msg)
    if sum(i.byte_length for i in card.review_images) > PAIR_MAX_BYTES:
        msg = "the pair is over the pair byte bound"
        raise ValueError(msg)


class Decision(_Card):
    """The ask card: ``ask_type`` picks ONE typed shape; other shapes are refused."""

    situation: Literal["decision"]
    ask_type: Literal["decide", "review", "do", "session"]
    question: Text
    why_operator: Literal[
        "judgment", "credentials", "physical_presence", "reserved_authority"
    ]
    answer: (
        Annotated[ChoiceAnswer | IntegerAnswer, Field(discriminator="kind")] | None
    ) = None  # decide
    artifact: ArtifactToReview | None = None  # review
    review_images: (
        Annotated[list[ReviewImage], Field(min_length=2, max_length=2)] | None
    ) = None  # review only: exactly the pair, in review order
    checklist: (
        Annotated[list[ChecklistStep], Field(min_length=1, max_length=8)] | None
    ) = None  # do
    session: Session | None = None  # session
    result_slots: (
        Annotated[list[ResultSlot], Field(min_length=1, max_length=6)] | None
    ) = None  # do only
    result_max_total_bytes: Annotated[int, Field(ge=1, le=1048576)] | None = (
        None  # do only; aggregate bound over the text slots
    )
    target_label: Text | None = None  # do only; from the task, never guessed
    pinned_texts: (
        Annotated[list[PinnedText], Field(min_length=1, max_length=3)] | None
    ) = None  # do only

    @model_validator(mode="after")
    def _the_fields_of_the_ask(self) -> Self:
        _check_images(self)
        _check_do_only(self)
        present = {
            "decide": self.answer,
            "review": self.artifact,
            "do": self.checklist,
            "session": self.session,
        }
        others = (v for k, v in present.items() if k != self.ask_type)
        if present[self.ask_type] is None or any(v is not None for v in others):
            msg = "exactly the fields of the stated ask_type"
            raise ValueError(msg)
        return self


class RequestStatus(_Card):
    situation: Literal["request_status"]
    delivery: Literal["pending", "delivered", "uncertain", "refused"]
    response: Literal["awaiting", "acknowledged", "evidence_returned"]
    outcome: Literal["open", "verifying", "verified", "failed", "cancelled"]
    deadline: DateTime | None = None  # absent when there is no explicit deadline
    overdue: StrictBool | None = None  # absent (unknown) without a deadline
    summary: Text  # derived by the service; a late ack never downgrades verified

    @model_validator(mode="after")
    def _overdue_needs_a_deadline(self) -> Self:
        if self.overdue is not None and self.deadline is None:
            msg = "overdue is stated only against an explicit deadline"
            raise ValueError(msg)
        return self


class Outcome(_Card):
    situation: Literal["outcome"]
    outcome: Literal["verified", "failed", "cancelled"]
    # verified: completion evidence AND the verification ref. failed and cancelled
    # carry neither: nothing there may imply a successful delivery.
    completion_evidence: Annotated[list[Evidence], Field(max_length=8)] = Field(
        default_factory=list
    )
    verification_ref: Ref | None = None  # the service's predicate record
    reason: Text | None = None
    closed_at: DateTime

    @model_validator(mode="after")
    def _verified_is_proved(self) -> Self:
        proved = bool(self.completion_evidence) and self.verification_ref is not None
        if self.outcome == "verified":
            if not proved:
                msg = "verified needs completion evidence and the verification ref"
                raise ValueError(msg)
        elif self.completion_evidence or self.verification_ref is not None:
            msg = "only a verified outcome carries completion evidence"
            raise ValueError(msg)
        return self


# -- the project checkpoint (read-only rows; the owner's field names) ----------------


class LatestCheckpoint(_Opt):
    """A seat's latest KNOWN checkpoint, kept apart from the current run's slot.

    A seat that missed the current slot keeps the real time and reference of its last
    checkpoint, and that earlier evidence NEVER makes the current slot answered.
    """

    ref: MailId
    answered_at: DateTime  # the authored time of THAT checkpoint
    age_h: Hours | None = None  # as of the run; a viewer derives a fresh age
    run_ref: RunId | None = None


class SeatAnswer(_Opt):
    seat: SeatName
    # about the CURRENT run slot only
    answer_state: Literal["answered", "not_answered", "unknown"]
    answer_at: DateTime | None = None  # authored time, never a read time
    answer_age_h: Hours | None = None  # as of the run
    answer_ref: MailId | None = None
    latest_checkpoint: LatestCheckpoint | None = None  # absent: the age is UNKNOWN

    @model_validator(mode="after")
    def _slot_and_checkpoint(self) -> Self:
        proofs = (self.answer_at, self.answer_age_h, self.answer_ref)
        if self.answer_state == "answered" and (
            self.answer_at is None or self.answer_ref is None
        ):
            msg = "an answered seat has its time and its message id for this slot"
            raise ValueError(msg)
        if self.answer_state == "not_answered" and any(p is not None for p in proofs):
            msg = "a seat that did not answer carries no slot time, age or message id"
            raise ValueError(msg)
        return self

    def known_age(self) -> bool:
        answered = self.answer_state == "answered" and self.answer_age_h is not None
        earlier = (
            self.latest_checkpoint is not None
            and self.latest_checkpoint.age_h is not None
        )
        return answered or earlier


class Milestone(_Opt):
    """``last_product`` and ``last_administrative`` share this shape, kept apart."""

    what: Text | None = None
    when: DateTime | None = None
    evidence_ref: EvidenceLink | None = None
    stage: Literal[
        "implemented",
        "qualified",
        "reviewed",
        "merged",
        "delivered",
        "installed",
        "consumer_accepted",
        "unknown",
    ]

    @model_validator(mode="after")
    def _a_known_stage_has_its_provenance(self) -> Self:
        proved = None not in (self.what, self.when, self.evidence_ref)
        if self.stage != "unknown" and not proved:
            msg = "a known stage needs its description, time and evidence link"
            raise ValueError(msg)
        return self


class Wait(_Opt):
    owner: SeatName | None = None
    since: DateTime | None = None
    text: Text300 | None = None
    source: Literal["seat_answer", "sow_your_next", "unknown"]


class LocalOnly(_Opt):
    flag: YesNoUnknown  # declared by the seat
    note: Text | None = None
    git_unpushed_count: Annotated[int, Field(ge=0)] | None = None  # observed in git


class SourceStatus(_Opt):
    source: Literal[
        "seat_answers",
        "git_org_main",
        "git_branches",
        "github_prs",
        "seat_state_local",
        "org_chart",
        "operator_tasks",
    ]
    observed_at: DateTime | None = None
    collected_at: DateTime | None = None
    state: Literal["ok", "partial", "unavailable", "stale", "unknown"]
    reason: Text120 | None = None


class Flags(_Opt):
    stale: YesNoUnknown  # supplied by the owner; thresholds are the owner's
    blocked: YesNoUnknown
    reason: Text120 | None = None


class NextAct(_Opt):
    action: Text
    owner: SeatName
    time: DateTime | None = None
    time_quality: Literal["due", "target", "unknown"]

    @model_validator(mode="after")
    def _time_matches_quality(self) -> Self:
        if (self.time_quality == "unknown") != (self.time is None):
            msg = "an unknown time is absent; a due or target time is present"
            raise ValueError(msg)
        return self


class ProjectCheckpoint(_Opt):
    """One card per canonical project. Its identity is NOT a work id."""

    situation: Literal["project_checkpoint"]
    run_ref: RunId
    index_revision: Digest | None = None  # sha256 of the exact index bytes, if given
    project_ref: ProjectRef
    subject_revision: Int31
    seats: Annotated[list[SeatAnswer], Field(min_length=1, max_length=32)]
    last_product: Milestone | None = None  # never filled from administrative progress
    last_administrative: Milestone | None = None
    wait: Wait | None = None
    local_only: LocalOnly | None = None
    project_checkpoint_age_h: Hours | None = None  # the OLDEST known age
    sources: Annotated[list[SourceStatus], Field(min_length=1, max_length=12)]
    flags: Flags
    next_act: NextAct | None = None
    primary_action: Action | None = None
    secondary_actions: Annotated[list[Action], Field(max_length=3)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _the_age_hides_no_silent_seat(self) -> Self:
        if self.project_checkpoint_age_h is not None and not all(
            s.known_age() for s in self.seats
        ):
            msg = "the project age is absent while any seat has no known age"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _product_and_administrative_are_apart(self) -> Self:
        if (
            self.last_product is not None
            and self.last_product == self.last_administrative
        ):
            msg = "product and administrative progress are separate rows"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _only_the_three_interventions(self) -> Self:
        actions = _offered(self.primary_action, self.secondary_actions)
        if any(
            a.route != "intervention" or a.type not in CHECKPOINT_ACTIONS
            for a in actions
        ):
            msg = "a project card offers only checkpoint, unblock and explain"
            raise ValueError(msg)
        _check_disabled(actions)
        return self


class SourceItem(_Opt):
    """A read-only item shown as its source states it. Its identity is the source's id.

    It has no work id, no revision counter and no action: a front end shows it and
    offers to copy it. The digest names the exact source bytes it was read from.
    """

    situation: Literal["source_item"]
    source_id: SourceRef
    source_digest: Digest  # sha256 of the source bytes observed, not a revision
    observed_at: DateTime
    project: Text
    label: Text
    title: Text
    state: Text  # the source's own word; the contract does not list its values
    next_action: LongText | None = None
    copy_line: LongText | None = None
    owner: LongText | None = None
    deadline: DateTime | None = None
    source_url: Annotated[str, Field(pattern=_HTTPS_URL)] | None = None
    freshness: Annotated[list[Freshness], Field(max_length=8)] = Field(
        default_factory=list
    )


# -- messages and approvals ------------------------------------------------------

Priority = Literal["p0", "p1", "p2"]
#: The id of an approval request: ``apr_`` and a canonical lowercase uuid (8-4-4-4-12).
ApprovalId = Annotated[
    str,
    Field(
        pattern=r"^apr_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
]
ApprovalStatus = Literal["pending", "approved", "consumed", "refused"]


def _not_a_work_id(value: str) -> str:
    if value.startswith(("wi_", "todo_")):
        msg = "a message id is the sender's own id, never a work id"
        raise ValueError(msg)
    return value


MessageId = Annotated[SourceRef, AfterValidator(_not_a_work_id)]


class Link(_Closed):
    """A link travels as data, never inside the plain-text body."""

    label: Text
    url: Annotated[str, Field(pattern=_HTTPS_URL)]


class _ReplyBase(_Opt):
    """HOW an answer renders. The card never sends it and names no destination."""

    reply_ref: SourceRef  # the open reply slot, owned by the sender's service
    answers_digest: Digest  # the source_digest of the message being answered
    closes_at: DateTime | None = None


class TextReply(_ReplyBase):
    mode: Literal["text"]
    max_length: Annotated[int, Field(ge=1, le=4096)]


class ChoiceReply(_ReplyBase):
    mode: Literal["choice"]
    options: Annotated[list[Option], Field(min_length=2, max_length=4)]

    @model_validator(mode="after")
    def _options_are_distinct(self) -> Self:
        if len({o.id for o in self.options}) != len(self.options):
            msg = "the options of a reply have distinct ids"
            raise ValueError(msg)
        return self


Reply = Annotated[TextReply | ChoiceReply, Field(discriminator="mode")]


class Message(_Opt):
    """A message to the operator, read-only. Its identity is the sender's id.

    ``source_digest`` names the exact message bytes, so a reply can say what it
    answers. The card computes no age and no urgency.
    """

    situation: Literal["message"]
    message_id: MessageId
    source_digest: Digest
    sent_at: DateTime
    from_: Annotated[SeatName, Field(alias="from")]  # the sending seat
    subject: Text
    body: PlainLong
    project: Text | None = None
    in_reply_to: MessageId | None = None  # a link to another message, not a thread
    priority: Priority | None = None  # the sender's word
    expires_at: DateTime | None = None
    links: Annotated[list[Link], Field(max_length=5)] = Field(default_factory=list)
    freshness: Annotated[list[Freshness], Field(max_length=8)] = Field(
        default_factory=list
    )
    reply: Reply | None = None

    @model_validator(mode="after")
    def _reply_answers_this_message(self) -> Self:
        if self.reply is not None and self.reply.answers_digest != self.source_digest:
            msg = "a reply answers exactly this message: answers_digest is its digest"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _not_a_reply_to_itself(self) -> Self:
        if self.in_reply_to == self.message_id:
            msg = "a message does not reply to itself"
            raise ValueError(msg)
        return self


class Approval(_Opt):
    """An approval request, read-only and display only. The owning service decides.

    The card carries what the person is shown and the digest of exactly what is
    approved. It holds no identity of the person, no audit and no action: a button
    would be a request that the owning service checks and records, never the decision.
    """

    situation: Literal["approval"]
    approval_id: ApprovalId
    request_digest: Digest
    operation_id: Text
    preview: Annotated[PlainLong, Field(max_length=2000)]
    status: ApprovalStatus
    issued_at: DateTime
    expires_at: DateTime
    decided_at: DateTime | None = None
    freshness: Annotated[list[Freshness], Field(max_length=8)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _decided_only_when_decided(self) -> Self:
        if (self.status == "pending") != (self.decided_at is None):
            msg = "decided_at is present exactly when the request is no longer pending"
            raise ValueError(msg)
        return self


InterventionCard = Annotated[
    WorkLocation
    | Checkpoint
    | Blocker
    | Decision
    | RequestStatus
    | Outcome
    | ProjectCheckpoint
    | SourceItem
    | Message
    | Approval,
    Field(discriminator="situation"),
]


class InterventionCardPayload(RootModel[InterventionCard]):
    """What the widget registry validates: one card, chosen by ``situation``."""

    model_config = ConfigDict(strict=True, frozen=True)
