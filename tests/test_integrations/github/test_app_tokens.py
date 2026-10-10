"""GitHub App installation tokens: one repository, exact scope, sent once."""

from __future__ import annotations

import base64
import json
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import SecretStr

from zeo_core.integrations.github.app_tokens import (
    GITHUB_API,
    Access,
    AppTokenError,
    InstallationTokens,
    KeychainAppKeySource,
    app_jwt,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = SecretStr(
    KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
)
TOKEN = "ghs_installation-token-canary"  # noqa: S105 -- a test canary


class Key:
    def load(self) -> SecretStr:
        return PEM


def _unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_the_app_jwt_is_rs256_signed_and_short_lived() -> None:
    header, claims, signature = app_jwt(42, PEM, NOW).split(".")
    assert json.loads(_unb64(header)) == {"alg": "RS256", "typ": "JWT"}
    payload = json.loads(_unb64(claims))
    stamp = int(NOW.timestamp())
    assert payload == {"iat": stamp - 60, "exp": stamp + 540, "iss": "42"}
    KEY.public_key().verify(
        _unb64(signature),
        f"{header}.{claims}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_a_key_that_is_not_an_rsa_pem_is_refused() -> None:
    with pytest.raises(AppTokenError, match="PEM"):
        app_jwt(1, SecretStr("not a key"), NOW)


def _tokens(
    answer: httpx.Response | Exception, seen: list[httpx.Request]
) -> InstallationTokens:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "DELETE":
            return httpx.Response(204)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return InstallationTokens(
        app_id=42,
        installation_id=7,
        key_source=Key(),
        http_client=httpx.Client(
            base_url=GITHUB_API, transport=httpx.MockTransport(handler)
        ),
        clock=lambda: NOW,
    )


def _granted(**overrides: object) -> httpx.Response:
    body = {
        "token": TOKEN,
        "expires_at": (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "permissions": {
            "contents": "write",
            "pull_requests": "write",
            "metadata": "read",
        },
        "repositories": [{"full_name": "zeroemployeeorg/zeocore"}],
        **overrides,
    }
    return httpx.Response(201, json=body)


SCOPE: dict[str, Access] = {"contents": "write", "pull_requests": "write"}


def test_a_token_is_one_repository_and_exactly_the_scope_asked_for() -> None:
    seen: list[httpx.Request] = []
    token = _tokens(_granted(), seen).token("zeroemployeeorg/zeocore", SCOPE)
    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == "/app/installations/7/access_tokens"
    assert json.loads(request.content) == {
        "repositories": ["zeocore"],
        "permissions": SCOPE,
    }
    assert request.headers["Authorization"].startswith("Bearer ey")
    assert token.token.get_secret_value() == TOKEN
    assert TOKEN not in repr(token)
    assert token.repository == "zeroemployeeorg/zeocore"
    assert dict(token.permissions) == SCOPE


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "permissions": {
                "contents": "write",
                "pull_requests": "write",
                "administration": "write",
            }
        },
        {"permissions": {"contents": "write"}},
        {
            "repositories": [
                {"full_name": "zeroemployeeorg/zeocore"},
                {"full_name": "zeroemployeeorg/org"},
            ]
        },
    ],
)
def test_a_different_scope_is_refused_and_revoked_at_once(
    overrides: dict[str, Any],
) -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(AppTokenError, match="different scope"):
        _tokens(_granted(**overrides), seen).token("zeroemployeeorg/zeocore", SCOPE)
    assert [request.method for request in seen] == ["POST", "DELETE"]
    assert seen[1].url.path == "/installation/token"
    assert seen[1].headers["Authorization"] == f"token {TOKEN}"


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (httpx.Response(502), "ambiguous"),
        (httpx.ReadTimeout("slow"), "ambiguous"),
        (httpx.ConnectError("refused"), "unavailable"),
        (httpx.Response(403, json={"message": "nope"}), "refused"),
        (httpx.Response(302, headers={"Location": "https://evil.example"}), "refused"),
        (httpx.Response(201, json={"token": TOKEN}), "refused"),
    ],
)
def test_every_failure_is_one_attempt(
    answer: httpx.Response | Exception, outcome: str
) -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(AppTokenError) as caught:
        _tokens(answer, seen).token("zeroemployeeorg/zeocore", SCOPE)
    assert caught.value.outcome == outcome
    assert len([request for request in seen if request.method == "POST"]) == 1


@pytest.mark.parametrize(
    ("repository", "permissions"),
    [
        ("zeocore", SCOPE),
        ("a/b/c", SCOPE),
        ("zeroemployeeorg/zeocore", {}),
        ("zeroemployeeorg/zeocore", {"contents": "admin"}),
    ],
)
def test_an_unclear_request_is_refused_before_signing(
    repository: str, permissions: dict[str, str]
) -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(ValueError):
        _tokens(_granted(), seen).token(repository, permissions)  # type: ignore[arg-type]
    assert seen == []


def test_the_keychain_source_reads_only_this_users_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, PEM.get_secret_value() + "\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    key = KeychainAppKeySource(service="mator-merger").load()
    assert key.get_secret_value() == PEM.get_secret_value().strip()
    assert calls == [
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            "mator-merger",
            "-a",
            "private-key",
            "-w",
        ]
    ]
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 44, "", ""),
    )
    with pytest.raises(AppTokenError, match="Keychain"):
        KeychainAppKeySource(service="mator-merger").load()


@pytest.mark.parametrize(
    "printed",
    [
        PEM.get_secret_value(),
        PEM.get_secret_value()
        .encode()
        .hex(),  # how security -w prints a multi-line value
        base64.b64encode(PEM.get_secret_value().encode()).decode(),
    ],
)
def test_the_keychain_value_is_read_however_it_was_printed(
    monkeypatch: pytest.MonkeyPatch, printed: str
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, printed + "\n", ""),
    )
    key = KeychainAppKeySource(service="mator-merger").load()
    assert key.get_secret_value() == PEM.get_secret_value().strip()


def test_a_keychain_value_that_is_no_pem_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "68656c6c6f\n", ""),
    )
    with pytest.raises(AppTokenError, match="not a PEM"):
        KeychainAppKeySource(service="mator-merger").load()


def test_a_keychain_that_does_not_answer_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, 15)

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(AppTokenError) as caught:
        KeychainAppKeySource(service="mator-merger").load()
    assert caught.value.outcome == "unavailable"
