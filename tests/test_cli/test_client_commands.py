"""zeocore login, logout, whoami, connections, invoke and artifact get."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.test_integrations.hosted.test_pairing import (
    CANARY_ACCESS,
    CANARY_REFRESH,
    NOW,
    KeychainRunner,
    PairingTransportFake,
)
from zeo_core.cli import client
from zeo_core.cli.__main__ import run
from zeo_core.contracts.connections import NormalizedError, NormalizedErrorCode
from zeo_core.integrations.hosted import (
    HostedOperationRequest,
    HostedOperationResponse,
    InMemorySecureSessionStore,
    KeychainSecureSessionStore,
    PairingChallenge,
    PairingPendingError,
    SecureStoreError,
)
from zeo_core.integrations.hosted.client import (
    HostedClientError,
    HostedSessionError,
    HostedStoppedError,
    HostedUnavailableError,
    HostedUnreachableError,
)

CONNECTION = "con_google_12345678"


class Broker(PairingTransportFake):
    """The pairing fake, plus scripted invoke answers and a pending count."""

    def __init__(self) -> None:
        super().__init__()
        self.pending = 0
        self.requests: list[HostedOperationRequest] = []
        self.answers: list[dict[str, Any] | Exception] = []
        self.closed = False
        self.revoked = False
        self.profiles: list[str | None] = []
        self.stores: dict[str | None, InMemorySecureSessionStore] = {}

    def poll_pairing(self, challenge: PairingChallenge) -> Any:  # noqa: ANN401
        if self.pending:
            self.pending -= 1
            raise PairingPendingError()
        return self.session

    def list_connections(self, session: Any) -> Any:  # noqa: ANN401
        return (self.connection,)

    def revoke_device(self, session: Any) -> None:  # noqa: ANN401
        self.revoked = True

    def refresh_session(self, session: Any) -> Any:  # noqa: ANN401
        return session

    def invoke(self, request: HostedOperationRequest) -> HostedOperationResponse:
        self.requests.append(request)
        if not self.answers:
            return super().invoke(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return HostedOperationResponse.model_validate(answer)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> Broker:
    fake = Broker()

    def make_store(profile: str | None) -> InMemorySecureSessionStore:
        fake.profiles.append(profile)
        return fake.stores.setdefault(profile, InMemorySecureSessionStore())

    monkeypatch.setattr(client, "make_store", make_store)
    monkeypatch.setattr(client, "make_transport", lambda store: fake)
    monkeypatch.setattr(client, "sleep", lambda seconds: None)
    monkeypatch.setattr(client, "now", lambda: NOW)
    monkeypatch.delenv(client.PROFILE_ENV, raising=False)
    return fake


def _paired(broker: Broker, profile: str | None = None) -> None:
    broker.stores.setdefault(profile, InMemorySecureSessionStore()).save(broker.session)


def _events() -> tuple[list[dict[str, Any]], Any]:
    seen: list[dict[str, Any]] = []
    return seen, seen.append


# -- login, logout, whoami ---------------------------------------------------------


def test_login_shows_the_code_waits_for_the_person_and_stores_the_grant(
    broker: Broker,
) -> None:
    broker.pending = 2
    seen, out = _events()
    status, answer = client.login(["--profile", "studio"], b"", out=out)
    assert status == 0
    assert seen == [
        {
            "event": "pair",
            "verification_url": "https://connect.zeroemployee.org/device",
            "user_code": "ABCD-EFGH",
            "expires_at": broker.challenge.expires_at.isoformat(),
        }
    ]
    assert answer["state"] == "paired"
    assert answer["profile"] == "studio"
    assert answer["connections"][0]["connection_id"] == CONNECTION
    assert broker.stores["studio"].load() == broker.session
    assert broker.calls[0].startswith("begin:zeocore on ")
    assert broker.calls[0].endswith(" (studio)")
    assert CANARY_ACCESS not in json.dumps(answer)
    assert broker.closed


def test_login_that_nobody_approves_expires(
    broker: Broker, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker.pending = 10**6
    times = iter([NOW, NOW + timedelta(minutes=11)])
    monkeypatch.setattr(client, "now", lambda: next(times))
    status, answer = client.login([], b"", out=lambda line: None)
    assert (status, answer["outcome"]) == (20, "pairing_expired")


def test_the_profile_comes_from_the_flag_or_the_environment(
    broker: Broker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(client.PROFILE_ENV, "duck")
    client.whoami([], b"")
    client.whoami(["--profile", "studio"], b"")
    assert broker.profiles == ["duck", "studio"]


def test_whoami_reads_locally_and_never_prints_a_token(broker: Broker) -> None:
    assert client.whoami([], b"")[0] == 12
    _paired(broker)
    status, answer = client.whoami([], b"")
    assert status == 0
    assert answer["device_id"] == broker.session.device_id
    assert CANARY_ACCESS not in json.dumps(answer)
    assert CANARY_REFRESH not in json.dumps(answer)
    assert broker.requests == []


def test_logout_revokes_the_grant_or_says_there_was_none(broker: Broker) -> None:
    assert client.logout([], b"") == (
        0,
        {"ok": True, "state": "not_paired", "profile": None},
    )
    _paired(broker)
    status, answer = client.logout([], b"")
    assert (status, answer["state"]) == (0, "revoked")
    assert broker.revoked
    assert broker.stores[None].load() is None


def test_no_usable_session_store_fails_closed_with_12(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(profile: str | None) -> Any:  # noqa: ANN401
        raise SecureStoreError("secure session storage is unavailable")

    monkeypatch.setattr(client, "make_store", unavailable)
    for command in (client.whoami, client.connections):
        status, answer = command([], b"")
        assert (status, answer["outcome"]) == (12, "no_session_store")
    status, answer = client.invoke(["op.x", "--connection", CONNECTION], b"{}")
    assert (status, answer["outcome"]) == (12, "no_session_store")


def test_a_bad_profile_name_is_invalid_input() -> None:
    status, answer = client.whoami(["--profile", "Not A Name"], b"")
    assert (status, answer["outcome"]) == (2, "invalid_request")


def test_the_keychain_keeps_each_profile_apart_and_the_default_unchanged() -> None:
    default = KeychainSecureSessionStore(runner=KeychainRunner())
    studio = KeychainSecureSessionStore(runner=KeychainRunner(), profile="studio")
    assert default._service == "org.zeroemployee.zeocore.zeoconnect-session"
    assert studio._service == default._service + ".profile.studio"
    for bad in ("", "Studio", "-x", "a" * 33, "a/b"):
        with pytest.raises(SecureStoreError, match="profile"):
            KeychainSecureSessionStore(runner=KeychainRunner(), profile=bad)


# -- connections -------------------------------------------------------------------


def test_connections_lists_and_filters_by_service(broker: Broker) -> None:
    _paired(broker)
    status, answer = client.connections([], b"")
    assert status == 0
    (listed,) = answer["connections"]
    assert listed["connection_id"] == CONNECTION
    assert listed["service"] == "google.drive"
    assert "handle" not in listed
    assert client.connections(["--service", "youtube"], b"")[1]["connections"] == []


def test_connections_without_a_grant_is_12(broker: Broker) -> None:
    assert client.connections([], b"")[0] == 12


# -- invoke ------------------------------------------------------------------------


def _invoke(*extra: str, arguments: object | None = None) -> tuple[int, dict[str, Any]]:
    return client.invoke(
        ["google.drive.file.download", "--connection", CONNECTION, *extra],
        json.dumps({"file_id": "f"} if arguments is None else arguments).encode(),
        out=lambda line: None,
    )


def test_the_key_is_the_request_so_asking_again_replays(broker: Broker) -> None:
    _paired(broker)
    first = _invoke()[1]["request_key"]
    assert _invoke()[1]["request_key"] == first
    assert _invoke("--occurrence", "2")[1]["request_key"] != first
    assert _invoke(arguments={"file_id": "g"})[1]["request_key"] != first
    reordered = client.invoke(
        ["google.drive.file.download", "--connection", CONNECTION],
        b'{"file_id": "f"}',
    )[1]["request_key"]
    assert reordered == first
    assert [request.idempotency_key for request in broker.requests[:2]] == [first] * 2


def test_a_confirmed_call_answers_its_artifact_descriptor(broker: Broker) -> None:
    _paired(broker)
    status, answer = _invoke()
    assert status == 0
    assert answer["ok"] is True
    assert answer["status"] == "confirmed"
    assert answer["artifact"]["artifact_id"] == "art_selected_file"


def _refused(code: NormalizedErrorCode, message: str = "no") -> dict[str, Any]:
    return {
        "status": "failed_safe",
        "execution_id": "exe_x",
        "normalized_error": NormalizedError(code=code, message=message).model_dump(
            mode="json"
        ),
    }


APPROVAL = {
    "status": "approval_required",
    "execution_id": "exe_x",
    "approval_url": "https://connect.zeo.ac/approve/exe_x",
}


@pytest.mark.parametrize(
    ("answer", "status", "retry"),
    [
        (APPROVAL, 10, "same_request"),
        ({"status": "ambiguous", "execution_id": "e"}, 13, "same_request"),
        (
            {
                "status": "ambiguous",
                "execution_id": "e",
                "receipt": {"in_flight": True},
            },
            11,
            "same_request",
        ),
        (_refused(NormalizedErrorCode.PROVIDER_UNAVAILABLE), 20, "new_occurrence"),
        (
            _refused(NormalizedErrorCode.STOPPED, "stopped:dispatch:global"),
            20,
            "same_request",
        ),
        (_refused(NormalizedErrorCode.REQUEST_REFUSED), 20, "new_occurrence"),
        ({"status": "refused", "execution_id": "e"}, 20, "new_occurrence"),
    ],
)
def test_each_broker_answer_has_its_exit_and_retry(
    broker: Broker, answer: dict[str, Any], status: int, retry: str
) -> None:
    _paired(broker)
    broker.answers = [answer]
    got, result = _invoke()
    assert (got, result["retry"], result["ok"]) == (status, retry, False)
    assert len(broker.requests) == 1


@pytest.mark.parametrize(
    ("error", "status", "outcome"),
    [
        (HostedUnreachableError(), 13, "ambiguous"),
        (HostedUnavailableError(), 11, "unavailable"),
        (HostedStoppedError(control="dispatch", scope="global"), 20, "stopped"),
        (HostedSessionError("paired device session is expired"), 12, "not_paired"),
        # A reworded session message can't change the exit: the type decides.
        (HostedSessionError("reworded"), 12, "not_paired"),
        (HostedClientError("paired device session is expired"), 20, "refused"),
        (HostedClientError("hosted request was refused"), 20, "refused"),
    ],
)
def test_transport_failures_have_their_exits(
    broker: Broker, error: Exception, status: int, outcome: str
) -> None:
    _paired(broker)
    broker.answers = [error]
    got, result = _invoke()
    assert (got, result["outcome"]) == (status, outcome)
    assert len(broker.requests) == 1


def test_wait_approval_announces_once_then_replays_the_same_key(broker: Broker) -> None:
    _paired(broker)
    confirmed = broker.invoke(
        HostedOperationRequest(
            connection_id=CONNECTION,
            operation_id="x.y",
            arguments={},
            idempotency_key="k",
        )
    ).model_dump(mode="json")
    broker.requests.clear()
    broker.answers = [APPROVAL, APPROVAL, confirmed]
    seen, out = _events()
    status, answer = client.invoke(
        [
            "google.drive.file.download",
            "--connection",
            CONNECTION,
            "--wait-approval",
            "60",
        ],
        b'{"file_id": "f"}',
        out=out,
    )
    assert status == 0
    assert seen == [
        {
            "event": "approval",
            "approval_url": "https://connect.zeo.ac/approve/exe_x",
            "request_key": answer["request_key"],
        }
    ]
    assert len({request.idempotency_key for request in broker.requests}) == 1
    assert len(broker.requests) == 3


def test_wait_approval_gives_up_at_its_deadline(
    broker: Broker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _paired(broker)
    broker.answers = [APPROVAL]
    clock = iter([0.0, 0.0, 30.0, 61.0])
    monkeypatch.setattr(client.time, "monotonic", lambda: next(clock))
    status, answer = _invoke("--wait-approval", "60")
    assert status == 10
    assert answer["approval_url"] == "https://connect.zeo.ac/approve/exe_x"


@pytest.mark.parametrize(
    ("argv", "stdin"),
    [
        (["x.y"], b"{}"),
        (["x.y", "--connection", CONNECTION], b"[]"),
        (["x.y", "--connection", CONNECTION], b"not json"),
        (["x.y", "--connection", CONNECTION, "--wait-approval", "0"], b"{}"),
        (["x.y", "--connection", CONNECTION, "--nope"], b"{}"),
        (["x.y", "--connection", ""], b"{}"),
    ],
)
def test_an_unusable_invocation_is_2_and_sends_nothing(
    broker: Broker, argv: list[str], stdin: bytes
) -> None:
    _paired(broker)
    status, answer = client.invoke(argv, stdin)
    assert (status, answer["outcome"]) == (2, "invalid_request")
    assert broker.requests == []


# -- artifact get ------------------------------------------------------------------


def _descriptor(broker: Broker) -> bytes:
    return json.dumps(
        {
            "artifact_id": "art_selected_file",
            "content_sha256": "sha256:" + hashlib.sha256(broker.content).hexdigest(),
            "size_bytes": len(broker.content),
            "media_type": "text/csv",
            "filename": "selected.csv",
        }
    ).encode()


def test_artifact_get_writes_a_new_file_and_checks_its_digest(
    broker: Broker, tmp_path: Path
) -> None:
    _paired(broker)
    out = tmp_path / "selected.csv"
    status, answer = client.artifact(["get", "--out", str(out)], _descriptor(broker))
    assert status == 0
    assert out.read_bytes() == broker.content
    assert answer["sha256"] == "sha256:" + hashlib.sha256(broker.content).hexdigest()
    assert list(tmp_path.iterdir()) == [out]
    status, answer = client.artifact(["get", "--out", str(out)], _descriptor(broker))
    assert (status, answer["outcome"]) == (2, "invalid_request")


def test_artifact_bytes_that_do_not_match_are_refused_and_not_written(
    broker: Broker, tmp_path: Path
) -> None:
    _paired(broker)
    broker.content = b"other"
    descriptor = json.loads(_descriptor(broker))
    descriptor["content_sha256"] = "sha256:" + "0" * 64
    out = tmp_path / "x.csv"
    status, answer = client.artifact(
        ["get", "--out", str(out)], json.dumps(descriptor).encode()
    )
    assert (status, answer["outcome"]) == (20, "refused")
    assert not out.exists()


def test_artifact_get_needs_a_descriptor(broker: Broker, tmp_path: Path) -> None:
    status, _ = client.artifact(["get", "--out", str(tmp_path / "x")], b"{}")
    assert status == 2


# -- through the dispatcher --------------------------------------------------------


def test_the_client_commands_are_zeocore_commands(broker: Broker) -> None:
    _paired(broker)
    assert run(["whoami"], lambda: b"")[0] == 0
    status, answer = run(
        ["invoke", "google.drive.file.download", "--connection", CONNECTION],
        lambda: b'{"file_id": "f"}',
    )
    assert (status, answer["status"]) == (0, "confirmed")
