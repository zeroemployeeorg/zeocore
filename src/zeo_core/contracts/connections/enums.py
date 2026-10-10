"""
Controlled vocabularies for the connections domain.

Consumed by: connector, connection, execution, error and receipt contracts
in this package.
Must NOT contain: transition logic (see transitions.py), adapter code.

ExecutionState is grounded in the Principal's direct ruling on
ZC0-KERNEL-SEAM-01's state machine (msg_ebff3939, refined by msg_770124cc),
which supersedes disposition 12's SUCCEEDED/FAILED/RECONCILED naming and
reinstates section 5.5's REFUSED/FAILED_SAFE terminal vocabulary. The
ruling is explicit and binding: ADD REFUSED and FAILED_SAFE; REMOVE the
generic FAILED and RECONCILED entirely; AMBIGUOUS encodes forward to
SUCCEEDED or FAILED_SAFE only, and only with reconciliation evidence and a
reference to the prior ambiguous receipt/transition event -- an unresolved
reconciliation attempt leaves the state AMBIGUOUS and appends an attempt
record rather than self-transitioning. Recorded here, superseding the
disposition-12 note this docstring previously carried, so a reader who
remembers the old SUCCEEDED/FAILED/RECONCILED shape is not left wondering
which one shipped (append-don't-revert: this replaces the prior grounding
note rather than leaving it to contradict the code beside it).

REFUSED and FAILED_SAFE also appear as members of NormalizedErrorCode
below (REQUEST_REFUSED, FAILED_SAFE) -- that is a DIFFERENT, pre-existing
enum describing a normalized provider-error taxonomy, not this state
machine. A receipt in ExecutionState.FAILED_SAFE MAY carry a
NormalizedError with code REQUEST_REFUSED or FAILED_SAFE, but the two
enums are never interchangeable and neither reuses the other's members.
"""

from __future__ import annotations

from enum import StrEnum


class ExecutionState(StrEnum):
    """
    Durable state of one execution, per the Principal's state-machine
    ruling (msg_ebff3939, refined by msg_770124cc).

    Minimum path:
        CREATED -> AUTHORIZATION_VERIFIED -> PREPARED -> DISPATCH_STARTED
        -> SUCCEEDED | FAILED_SAFE | AMBIGUOUS

    Pre-dispatch refusal path:
        CREATED | AUTHORIZATION_VERIFIED | PREPARED -> REFUSED

    REFUSED is a terminal reached only BEFORE DISPATCH_STARTED -- it is how
    the machine records that admission or authorization declined to dispatch
    at all. It is never reachable from DISPATCH_STARTED or from AMBIGUOUS:
    once a provider dispatch has actually started, refusal is no longer a
    possible outcome, only success, safe failure, or ambiguity are.

    AMBIGUOUS means the provider may have applied the effect but the system
    lacks trustworthy confirmation. Reconciliation may resolve AMBIGUOUS
    forward to SUCCEEDED or FAILED_SAFE ONLY, and only when the resolving
    transition carries reconciliation evidence and a reference to the prior
    ambiguous receipt/transition event -- the transition table in
    transitions.py enforces this by refusing any transition that treats
    AMBIGUOUS as if it had never happened. An unresolved reconciliation
    attempt does not self-transition AMBIGUOUS back to AMBIGUOUS; it leaves
    the state AMBIGUOUS and the attempt is appended as history instead
    (receipt.py's reconciliation_attempts), so a failed reconciliation is
    never mistaken for a state change and is never lost.

    There is no RECONCILED state: FAILED (generic) and RECONCILED (a
    disposition-12 state naming successful reconciliation as its own
    terminal) are both removed by this ruling. A resolved ambiguity is
    recorded as an ordinary SUCCEEDED or FAILED_SAFE receipt that carries
    reconciliation evidence, not as a fourth terminal shape.
    """

    CREATED = "CREATED"
    AUTHORIZATION_VERIFIED = "AUTHORIZATION_VERIFIED"
    PREPARED = "PREPARED"
    DISPATCH_STARTED = "DISPATCH_STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED_SAFE = "FAILED_SAFE"
    REFUSED = "REFUSED"
    AMBIGUOUS = "AMBIGUOUS"


class NormalizedErrorCode(StrEnum):
    """
    Closed taxonomy for provider-specific failures, per packet section 5.6.

    Provider detail remains available for diagnosis (see NormalizedError's
    `provider_detail` field) but must never become the only product
    explanation, and must never carry raw credential or cross-tenant
    material -- that constraint is enforced at the NormalizedError model,
    not here.
    """

    CONNECTION_REVOKED = "CONNECTION_REVOKED"
    CONNECTION_REPAIR_REQUIRED = "CONNECTION_REPAIR_REQUIRED"
    PROVIDER_SCOPE_MISSING = "PROVIDER_SCOPE_MISSING"
    EXTERNAL_IDENTITY_CHANGED = "EXTERNAL_IDENTITY_CHANGED"
    BUSINESS_RESOURCE_UNAVAILABLE = "BUSINESS_RESOURCE_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    REQUEST_REFUSED = "REQUEST_REFUSED"
    FAILED_SAFE = "FAILED_SAFE"
    RESULT_AMBIGUOUS = "RESULT_AMBIGUOUS"
    CONNECTOR_REVISION_UNAVAILABLE = "CONNECTOR_REVISION_UNAVAILABLE"
    #: An operational stop reported by ZEOconnect. The Broker sends it only to
    #: clients that declare ``ZEOconnect-Capabilities: stopped-code``; others
    #: get REQUEST_REFUSED with the message ``stopped:<control>:<scope>``.
    STOPPED = "STOPPED"


class ConnectionStatus(StrEnum):
    """
    Lifecycle status of a connection, per packet section 5.4.

    Deliberately small and closed. A connection's fine-grained health
    signal (reachable, degraded, unknown) is ConnectionHealth, not this
    enum -- status is about lifecycle, health is about reachability, and
    packet section 5.4 lists both as separate fields on the connection
    object.
    """

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REPAIR_REQUIRED = "REPAIR_REQUIRED"
    REVOKED = "REVOKED"


class ConnectionHealth(StrEnum):
    """Reachability signal for a connection, distinct from lifecycle status."""

    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNREACHABLE = "UNREACHABLE"


class RiskClass(StrEnum):
    """
    Declared risk class of a connector revision's business operations, per
    packet section 5.1's "risk class" manifest field.
    """

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class IdempotencyMode(StrEnum):
    """
    Declared idempotency mode of one admitted business operation, per
    disposition 10.
    """

    PROVIDER_NATIVE = "PROVIDER_NATIVE"
    KERNEL_MANAGED = "KERNEL_MANAGED"
    NOT_IDEMPOTENT = "NOT_IDEMPOTENT"


class AuthorizationRefusalReason(StrEnum):
    """
    Closed taxonomy for why an EffectAuthorizationVerifier refused to
    verify one attempted effect, step 2 (SecretStore/ConnectionStore/
    EffectAuthorizationVerifier protocols, no permissive default
    implementation).

    AUTHORIZATION_VERIFIED's own binding definition (Principal decision
    msg_ebff3939) names exactly what a verifier checks: "the exact ZEO Go
    authorization matches organization, connection, connector revision,
    business operation, normalized request digest, expiry and replay
    identity." Each member below is the refusal for one of those checks
    failing, plus ABSENT for the zero-authorization case -- there is
    deliberately no generic/catch-all member, because a verifier that could
    refuse without saying which check failed would be exactly the kind of
    unfalsifiable "trust me" surface disposition 2 forbids ("Zeocore
    verifies mechanical execution conditions... it does not reconstruct
    organizational policy").

    This is a REFUSAL taxonomy, not NormalizedErrorCode: NormalizedErrorCode
    (above) classifies a provider-side effect outcome recorded on a
    receipt; this enum classifies a pre-dispatch verification refusal,
    which never reaches a provider and never produces a receipt with an
    effect outcome at all (transitions.py: REFUSED is reachable only
    before DISPATCH_STARTED). The two enums are never interchangeable and
    neither reuses the other's members, mirroring the existing
    REFUSED/FAILED_SAFE note above.
    """

    ABSENT = "ABSENT"
    ORGANIZATION_MISMATCH = "ORGANIZATION_MISMATCH"
    CONNECTION_MISMATCH = "CONNECTION_MISMATCH"
    CONNECTOR_REVISION_MISMATCH = "CONNECTOR_REVISION_MISMATCH"
    OPERATION_MISMATCH = "OPERATION_MISMATCH"
    REQUEST_DIGEST_MISMATCH = "REQUEST_DIGEST_MISMATCH"
    AUDIENCE_MISMATCH = "AUDIENCE_MISMATCH"
    ISSUER_UNTRUSTED = "ISSUER_UNTRUSTED"
    SIGNATURE_UNVERIFIABLE = "SIGNATURE_UNVERIFIABLE"
    EXPIRED = "EXPIRED"
    REPLAYED = "REPLAYED"
