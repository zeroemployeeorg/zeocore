"""Nano Banana and Recraft through a faked ZEOconnect (proposed contract 1.3.0)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from tests.test_integrations.imaging.images import SVG, png
from zeo_core.contracts.connections import NormalizedError, NormalizedErrorCode
from zeo_core.integrations.hosted.client import (
    HostedArtifactDescriptor,
    HostedClientError,
    HostedConnectionClient,
    HostedOperationRequest,
    HostedOperationResponse,
    HostedSessionError,
    HostedStoppedError,
    HostedUnavailableError,
    HostedUnreachableError,
)
from zeo_core.integrations.hosted.services import HostedServiceBinding
from zeo_core.integrations.imaging import (
    GeminiGenerate,
    HostedGeminiImages,
    HostedRecraftImages,
    ImageInput,
    ImagingError,
    RecraftRemoveBackground,
    RecraftVectorize,
)

GEMINI = HostedServiceBinding(connection_id="con_gemini_12345678")
RECRAFT = HostedServiceBinding(connection_id="con_recraft_12345678")


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _artifact(content: bytes, media_type: str, name: str) -> HostedArtifactDescriptor:
    return HostedArtifactDescriptor(
        artifact_id=f"art_{name}_12345678",
        content_sha256=_digest(content),
        size_bytes=len(content),
        media_type=media_type,
        filename=name,
    )


class FakeBroker:
    """Records every upload and invocation; answers from a script."""

    def __init__(
        self, output: bytes = png(8, 8), media_type: str = "image/png"
    ) -> None:
        self.output = output
        self.media_type = media_type
        self.uploads: list[tuple[str, bytes, str]] = []
        self.invocations: list[HostedOperationRequest] = []
        self.stored: dict[str, bytes] = {}
        self.answer: dict[str, Any] | Exception | None = None
        self.upload_digest: str | None = None

    def upload_artifact(
        self, *, connection_id: str, content: bytes, media_type: str
    ) -> HostedArtifactDescriptor:
        self.uploads.append((connection_id, content, media_type))
        artifact = _artifact(content, media_type, f"in{len(self.uploads)}")
        if self.upload_digest is not None:
            artifact = artifact.model_copy(
                update={"content_sha256": self.upload_digest}
            )
        return artifact

    def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse:
        self.invocations.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is not None:
            return HostedOperationResponse.model_validate(self.answer)
        artifact = _artifact(
            self.output, self.media_type, f"out{len(self.invocations)}"
        )
        self.stored[artifact.artifact_id] = self.output
        return HostedOperationResponse(
            status="confirmed",
            execution_id=f"exe_{len(self.invocations)}",
            artifact=artifact,
            receipt={
                "model": "recraftv3",
                "provider_image_id": "img-1",
                "cost": {"unit": "recraft_credits", "reserved": 40, "settled": 20},
                "replayed": False,
            },
        )

    def fetch_artifact(self, *, artifact_id: str, max_bytes: int) -> bytes:
        return self.stored[artifact_id]


def _gemini(broker: FakeBroker) -> HostedGeminiImages:
    return HostedGeminiImages(
        client=HostedConnectionClient(transport=broker), binding=GEMINI
    )


def _recraft(broker: FakeBroker) -> HostedRecraftImages:
    return HostedRecraftImages(
        client=HostedConnectionClient(transport=broker), binding=RECRAFT
    )


def _input(width: int = 4) -> ImageInput:
    return ImageInput(content=png(width, 4), media_type="image/png")


def test_gemini_uploads_each_reference_then_runs_once_under_the_request_key() -> None:
    broker = FakeBroker()
    request = GeminiGenerate(prompt="a duck", inputs=(_input(4), _input(5)))
    image = _gemini(broker).run(request)
    assert [(c, m) for c, _, m in broker.uploads] == [
        ("con_gemini_12345678", "image/png"),
        ("con_gemini_12345678", "image/png"),
    ]
    (call,) = broker.invocations
    assert call.operation_id == "gemini.image.generate"
    assert call.connection_id == "con_gemini_12345678"
    assert call.idempotency_key == request.idempotency_key()
    assert call.arguments["input_artifact_ids"] == [
        "art_in1_12345678",
        "art_in2_12345678",
    ]
    assert call.arguments["prompt"] == "a duck"
    assert image.content == broker.output
    assert image.sha256 == hashlib.sha256(broker.output).hexdigest()
    assert (image.provider, image.profile, image.operation) == (
        "gemini",
        "hosted",
        "gemini.image.generate",
    )
    assert image.cost is not None and image.cost.settled == 20
    assert image.provider_image_id == "img-1"
    assert image.execution_id == "exe_1"
    assert image.replayed is False


def test_asking_for_the_same_image_again_sends_the_same_key() -> None:
    broker = FakeBroker()
    service = _gemini(broker)
    service.run(GeminiGenerate(prompt="a duck"))
    service.run(GeminiGenerate(prompt="a duck"))
    service.run(GeminiGenerate(prompt="a duck", occurrence="2"))
    keys = [call.idempotency_key for call in broker.invocations]
    assert keys[0] == keys[1] != keys[2]


def test_a_replayed_answer_is_reported_as_one() -> None:
    broker = FakeBroker()
    artifact = _artifact(broker.output, "image/png", "out")
    broker.stored[artifact.artifact_id] = broker.output
    broker.answer = {
        "status": "confirmed",
        "execution_id": "exe_first",
        "artifact": artifact.model_dump(),
        "receipt": {"replayed": True},
    }
    image = _gemini(broker).run(GeminiGenerate(prompt="a duck"))
    assert image.replayed is True
    assert image.execution_id == "exe_first"
    assert image.cost is None


def test_a_chain_on_one_connection_feeds_the_produced_image_without_upload() -> None:
    broker = FakeBroker(output=png(16, 16))
    service = _recraft(broker)
    cut = service.run(RecraftRemoveBackground(input=_input()))
    broker.output, broker.media_type = SVG, "image/svg+xml"
    vector = service.run(
        RecraftVectorize(input=ImageInput(content=cut.content, media_type="image/png"))
    )
    assert len(broker.uploads) == 1
    assert broker.invocations[1].arguments == {"input_artifact_id": "art_out1_12345678"}
    assert vector.media_type == "image/svg+xml"


def test_ids_never_cross_connections() -> None:
    broker = FakeBroker(output=png(16, 16))
    cut = _recraft(broker).run(RecraftRemoveBackground(input=_input()))
    _gemini(broker).run(
        GeminiGenerate(
            prompt="x",
            inputs=(ImageInput(content=cut.content, media_type="image/png"),),
        )
    )
    assert broker.uploads[-1][0] == "con_gemini_12345678"
    assert len(broker.uploads) == 2


def test_an_upload_receipt_for_other_bytes_is_refused_and_nothing_runs() -> None:
    broker = FakeBroker()
    broker.upload_digest = _digest(b"other")
    with pytest.raises(ImagingError) as caught:
        _gemini(broker).run(GeminiGenerate(prompt="x", inputs=(_input(),)))
    assert caught.value.outcome == "refused"
    assert broker.invocations == []


def test_a_transport_without_upload_refuses_inputs_but_serves_text_to_image() -> None:
    class NoUpload:
        def __init__(self) -> None:
            self.broker = FakeBroker()

        def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse:
            return self.broker.invoke(request)

        def fetch_artifact(self, *, artifact_id: str, max_bytes: int) -> bytes:
            return self.broker.fetch_artifact(
                artifact_id=artifact_id, max_bytes=max_bytes
            )

    service = HostedGeminiImages(
        client=HostedConnectionClient(transport=NoUpload()), binding=GEMINI
    )
    service.run(GeminiGenerate(prompt="x"))
    with pytest.raises(ImagingError, match="cannot upload"):
        service.run(GeminiGenerate(prompt="x", inputs=(_input(),)))


def _refused(code: NormalizedErrorCode, message: str = "refused") -> dict[str, Any]:
    return {
        "status": "failed_safe",
        "execution_id": "exe_x",
        "normalized_error": NormalizedError(code=code, message=message).model_dump(
            mode="json"
        ),
    }


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (_refused(NormalizedErrorCode.BUDGET_EXHAUSTED), "budget_exhausted"),
        (_refused(NormalizedErrorCode.INPUT_ARTIFACT_UNAVAILABLE), "input_unavailable"),
        (_refused(NormalizedErrorCode.STOPPED, "stopped:dispatch:recraft"), "stopped"),
        (
            _refused(NormalizedErrorCode.REQUEST_REFUSED, "stopped:dispatch:recraft"),
            "stopped",
        ),
        (_refused(NormalizedErrorCode.PROVIDER_UNAVAILABLE), "unavailable"),
        (_refused(NormalizedErrorCode.REQUEST_REFUSED), "refused"),
        ({"status": "refused", "execution_id": "exe_x"}, "refused"),
        ({"status": "ambiguous", "execution_id": "exe_x"}, "ambiguous"),
    ],
)
def test_each_answer_has_its_own_outcome_and_is_never_resent(
    answer: dict[str, Any], outcome: str
) -> None:
    broker = FakeBroker()
    broker.answer = answer
    with pytest.raises(ImagingError) as caught:
        _recraft(broker).run(RecraftVectorize(input=_input()))
    assert caught.value.outcome == outcome
    assert len(broker.invocations) == 1


def test_approval_required_says_where() -> None:
    broker = FakeBroker()
    broker.answer = {
        "status": "approval_required",
        "execution_id": "exe_x",
        "approval_url": "https://connect.zeo.ac/approve/exe_x",
    }
    with pytest.raises(ImagingError) as caught:
        _gemini(broker).run(GeminiGenerate(prompt="x"))
    assert caught.value.outcome == "approval_required"
    assert caught.value.approval_url == "https://connect.zeo.ac/approve/exe_x"


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (HostedUnreachableError(), "ambiguous"),
        (HostedUnreachableError(may_have_arrived=False), "unavailable"),
        (HostedStoppedError(control="dispatch", scope="global"), "stopped"),
        (HostedUnavailableError(), "unavailable"),
        (HostedClientError("hosted request was refused"), "refused"),
    ],
)
def test_transport_failures_map_to_outcomes(error: Exception, outcome: str) -> None:
    broker = FakeBroker()
    broker.answer = error
    with pytest.raises(ImagingError) as caught:
        _gemini(broker).run(GeminiGenerate(prompt="x"))
    assert caught.value.outcome == outcome
    assert len(broker.invocations) == 1


def test_an_svg_with_active_content_is_never_handed_on() -> None:
    broker = FakeBroker(
        output=SVG.replace(b"</svg>", b"<script>x()</script></svg>"),
        media_type="image/svg+xml",
    )
    with pytest.raises(ImagingError) as caught:
        _recraft(broker).run(RecraftVectorize(input=_input()))
    assert caught.value.outcome == "invalid_response"


def test_an_output_that_is_not_its_media_type_is_refused() -> None:
    broker = FakeBroker(output=png(), media_type="image/jpeg")
    with pytest.raises(ImagingError, match="do not match"):
        _gemini(broker).run(GeminiGenerate(prompt="x"))


def test_a_provider_mismatch_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="not a gemini"):
        _gemini(FakeBroker()).run(RecraftVectorize(input=_input()))


def test_credits_are_a_read_with_a_fresh_key_each_time() -> None:
    broker = FakeBroker()
    broker.answer = {
        "status": "confirmed",
        "execution_id": "exe_read",
        "result": {"credits": 1250},
    }
    service = _recraft(broker)
    assert service.credits().credits == 1250
    service.credits()
    first, second = broker.invocations
    assert first.operation_id == "recraft.account.read"
    assert first.arguments == {}
    assert first.idempotency_key != second.idempotency_key
    broker.answer = {"status": "confirmed", "execution_id": "e", "result": {"x": 1}}
    with pytest.raises(ImagingError, match="no balance"):
        service.credits()


@pytest.mark.parametrize(
    ("answer", "outcome", "retry"),
    [
        ({"status": "ambiguous", "execution_id": "e"}, "ambiguous", "same_request"),
        (
            _refused(NormalizedErrorCode.BUDGET_EXHAUSTED),
            "budget_exhausted",
            "same_request",
        ),
        (_refused(NormalizedErrorCode.RATE_LIMITED), "unavailable", "new_occurrence"),
        (
            _refused(NormalizedErrorCode.PROVIDER_UNAVAILABLE),
            "unavailable",
            "new_occurrence",
        ),
        (_refused(NormalizedErrorCode.REQUEST_REFUSED), "refused", "none"),
    ],
)
def test_every_error_says_what_a_retry_needs_and_names_the_request(
    answer: dict[str, Any], outcome: str, retry: str
) -> None:
    broker = FakeBroker()
    broker.answer = answer
    request = GeminiGenerate(prompt="a duck")
    with pytest.raises(ImagingError) as caught:
        _gemini(broker).run(request)
    assert (caught.value.outcome, caught.value.retry) == (outcome, retry)
    assert caught.value.request_key == request.idempotency_key()


def test_a_produced_image_carries_its_request_key() -> None:
    request = GeminiGenerate(prompt="a duck")
    assert _gemini(FakeBroker()).run(request).request_key == request.idempotency_key()


def test_an_expired_held_id_is_uploaded_again_next_time() -> None:
    broker = FakeBroker(output=png(16, 16))
    service = _recraft(broker)
    cut = service.run(RecraftRemoveBackground(input=_input()))
    again = ImageInput(content=cut.content, media_type="image/png")
    broker.answer = _refused(NormalizedErrorCode.INPUT_ARTIFACT_UNAVAILABLE)
    with pytest.raises(ImagingError) as caught:
        service.run(RecraftVectorize(input=again))
    assert caught.value.retry == "same_request"
    broker.answer = None
    broker.output, broker.media_type = SVG, "image/svg+xml"
    service.run(RecraftVectorize(input=again))
    assert len(broker.uploads) == 2
    assert (
        broker.invocations[-1].idempotency_key == broker.invocations[-2].idempotency_key
    )


def test_a_replay_after_the_bytes_expired_never_regenerates() -> None:
    broker = FakeBroker()
    digest = "sha256:" + "a" * 64
    broker.answer = {
        "status": "confirmed",
        "execution_id": "exe_old",
        "result": {"artifact_expired": True, "content_sha256": digest},
        "receipt": {"replayed": True},
    }
    with pytest.raises(ImagingError) as caught:
        _gemini(broker).run(GeminiGenerate(prompt="a duck"))
    assert (caught.value.outcome, caught.value.retry) == (
        "artifact_expired",
        "new_occurrence",
    )
    assert caught.value.content_sha256 == digest
    assert len(broker.invocations) == 1


def test_a_replay_while_the_first_call_runs_says_so() -> None:
    broker = FakeBroker()
    broker.answer = {
        "status": "ambiguous",
        "execution_id": "exe_x",
        "receipt": {"replayed": True, "in_flight": True},
    }
    with pytest.raises(ImagingError, match="still running") as caught:
        _gemini(broker).run(GeminiGenerate(prompt="a duck"))
    assert (caught.value.outcome, caught.value.retry) == ("in_flight", "same_request")


def test_storage_capacity_is_a_passing_outage_not_a_refusal() -> None:
    broker = FakeBroker()
    broker.answer = {
        "status": "refused",
        "execution_id": "exe_x",
        "normalized_error": {
            "code": "REQUEST_REFUSED",
            "message": "storage_capacity_exceeded",
        },
    }
    with pytest.raises(ImagingError) as caught:
        _gemini(broker).run(GeminiGenerate(prompt="a duck"))
    assert (caught.value.outcome, caught.value.retry) == ("unavailable", "same_request")


def test_inputs_keep_their_order_roles_and_digests() -> None:
    identity = ImageInput(content=png(4, 4), media_type="image/png", role="identity")
    guide = ImageInput(content=png(5, 5), media_type="image/png", role="camera_guide")
    broker = FakeBroker()
    image = _gemini(broker).run(
        GeminiGenerate(
            model="gemini-3-pro-image", prompt="profile", inputs=(identity, guide)
        )
    )
    assert [(item.role, item.sha256) for item in image.inputs] == [
        ("identity", identity.sha256),
        ("camera_guide", guide.sha256),
    ]
    assert [content for _, content, _ in broker.uploads] == [png(4, 4), png(5, 5)]
    assert broker.invocations[0].arguments["model"] == "gemini-3-pro-image"


def test_a_role_is_provenance_only_and_never_changes_the_request() -> None:
    plain = GeminiGenerate(prompt="x", inputs=(_input(),))
    labelled = GeminiGenerate(
        prompt="x",
        inputs=(
            ImageInput(content=png(4, 4), media_type="image/png", role="identity"),
        ),
    )
    assert plain.idempotency_key() == labelled.idempotency_key()
    assert "role" not in json.dumps(labelled.arguments())
    swapped = GeminiGenerate(prompt="x", inputs=(_input(5), _input(4)))
    assert (
        swapped.idempotency_key()
        != GeminiGenerate(prompt="x", inputs=(_input(4), _input(5))).idempotency_key()
    )


@pytest.mark.parametrize(
    "message",
    [
        "paired device session is unavailable",
        "paired device session is expired",
        "paired device session was refused; pair this device again",
    ],
)
def test_an_unpaired_device_is_told_to_log_in(message: str) -> None:
    broker = FakeBroker()
    broker.answer = HostedSessionError(message)
    with pytest.raises(ImagingError, match="zeocore login") as caught:
        _gemini(broker).run(GeminiGenerate(prompt="x"))
    assert caught.value.outcome == "not_paired"


def test_only_the_session_type_means_not_paired() -> None:
    broker = FakeBroker()
    broker.answer = HostedClientError("paired device session is unavailable")
    with pytest.raises(ImagingError) as caught:
        _gemini(broker).run(GeminiGenerate(prompt="x"))
    assert caught.value.outcome == "refused"
