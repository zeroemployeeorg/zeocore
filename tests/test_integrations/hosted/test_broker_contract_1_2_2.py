"""zeocore against ZEOconnect Broker contract 1.2.2's re-enrolment code.

Pinned at zeoconnect 243409bc (#70's merge), contract/broker-contract-v1.md,
sha256 358bcb0f. §9: to a client declaring ``expected-binding``, the
re-enrolment 400 also carries ``"code": "connection_binding_changed"``. The
detail string is byte-identical, so zeocore reads the code first and keeps the
1.2.1 detail match as the fallback.
"""

from __future__ import annotations

import pytest

from tests.test_integrations.hosted.test_broker_contract_1_2_1 import (
    CONFLICT,
    EFFECT,
    READ,
    _answer,
)
from tests.test_integrations.hosted.test_transport import request
from zeo_core.integrations.hosted import (
    ZEOCONNECT_CAPABILITIES_HEADER,
    HostedClientError,
    HostedConnectionChangedError,
    HostedRequestChangedError,
)

CHANGED = "kernel connection binding changed"
CODE = "connection_binding_changed"


@pytest.mark.parametrize("operation", [EFFECT, READ])
@pytest.mark.parametrize(
    "body",
    [
        # The 1.2.2 bytes, which a declared client receives.
        {"detail": CHANGED, "code": CODE},
        # The 1.2.1 bytes: the detail alone is still enough.
        {"detail": CHANGED},
        # The code alone is enough too: it is read first.
        {"detail": "refused", "code": CODE},
        {"code": CODE},
    ],
)
def test_a_changed_connection_is_read_from_the_code_or_the_detail(
    operation: str, body: object
) -> None:
    sent, hosted = _answer(400, body)
    with pytest.raises(HostedConnectionChangedError, match="repair it"):
        hosted.invoke(request(operation))
    assert len(sent) == 1
    assert "expected-binding" in sent[0].headers[ZEOCONNECT_CAPABILITIES_HEADER]


@pytest.mark.parametrize(
    "body",
    [
        {"detail": "refused", "code": CODE + "."},
        {"detail": "refused", "code": CODE.upper()},
        {"detail": "refused", "code": [CODE]},
        {"detail": [CHANGED], "code": [CODE]},
        [CODE],
    ],
)
def test_only_the_exact_code_names_a_changed_connection(body: object) -> None:
    sent, hosted = _answer(400, body)
    with pytest.raises(HostedClientError, match="refused") as caught:
        hosted.invoke(request(EFFECT))
    assert not isinstance(caught.value, HostedConnectionChangedError)
    assert len(sent) == 1


@pytest.mark.parametrize("status", [403, 409, 422])
def test_the_code_counts_only_on_a_400(status: int) -> None:
    _, hosted = _answer(status, {"detail": CHANGED, "code": CODE})
    with pytest.raises(HostedClientError) as caught:
        hosted.invoke(request(EFFECT))
    assert not isinstance(caught.value, HostedConnectionChangedError)


def test_a_changed_request_under_a_used_key_is_still_its_own_reason() -> None:
    # §6a.2.6: under a used key, the changed-request answer comes first.
    sent, hosted = _answer(
        400, {"detail": CONFLICT, "code": "request_changed_under_key"}
    )
    with pytest.raises(HostedRequestChangedError):
        hosted.invoke(request(EFFECT))
    assert len(sent) == 1
