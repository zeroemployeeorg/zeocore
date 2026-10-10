"""GitHub App installation tokens, for a principal that holds its own App key.

For the merger (the operator's ruling, 2026-10-10: the merger is a zeocore
app whose key custody is separate from ZEOconnect) and any other isolated
principal. The App's private key is read only through an ``AppKeySource``
the host supplies, for example the merger OS user's own Keychain item. It
is never read from the environment, a file in a repository or ZEOconnect.

Each token is scoped to exactly one repository and exactly the permissions
asked for; GitHub's answer is checked to be no broader. The request is sent
once and never retried. Tokens are ``SecretStr`` and never logged.

Needs the ``github-app`` extra (``cryptography``).
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, Protocol

import httpx
from pydantic import SecretStr

GITHUB_API: Final = "https://api.github.com"
_REPOSITORY = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
_PERMISSION = re.compile(r"[a-z_]{1,64}")
Access = Literal["read", "write"]


class AppTokenError(RuntimeError):
    """No token: ``outcome`` is ``refused``, ``unavailable`` or ``ambiguous``.

    ``ambiguous`` means GitHub may have issued a token we never received; it
    expires on its own within the hour, and nothing is retried.
    """

    def __init__(self, outcome: str, message: str) -> None:
        self.outcome = outcome
        super().__init__(message)


class AppKeySource(Protocol):
    """Where an App's PEM private key lives. Implementations own the custody."""

    def load(self) -> SecretStr: ...


@dataclass(frozen=True)
class KeychainAppKeySource:
    """The running macOS user's own Keychain item, read with ``security``.

    Run as the merger's own OS user, this is a key no other seat can read.
    """

    service: str
    account: str = "private-key"

    def load(self) -> SecretStr:
        result = subprocess.run(  # noqa: S603 -- fixed binary, no shell
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                self.service,
                "-a",
                self.account,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise AppTokenError("refused", "the App key is not in this user's Keychain")
        return SecretStr(result.stdout.strip())


@dataclass(frozen=True)
class InstallationToken:
    token: SecretStr
    expires_at: datetime
    repository: str
    permissions: Mapping[str, Access]


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def app_jwt(app_id: int, key: SecretStr, now: datetime) -> str:
    """The App's own RS256 JWT: issued 60 s in the past, valid 9 minutes."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    try:
        private = serialization.load_pem_private_key(
            key.get_secret_value().encode(), password=None
        )
    except ValueError:
        raise AppTokenError("refused", "the App key is not a PEM private key") from None
    if not isinstance(private, rsa.RSAPrivateKey):
        raise AppTokenError("refused", "a GitHub App key is an RSA key")
    issued = int(now.timestamp())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(
        json.dumps(
            {"iat": issued - 60, "exp": issued + 540, "iss": str(app_id)}
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode()
    signature = private.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64url(signature)}"


class InstallationTokens:
    """Mints one-repository installation tokens for one App installation."""

    def __init__(
        self,
        *,
        app_id: int,
        installation_id: int,
        key_source: AppKeySource,
        http_client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if app_id <= 0 or installation_id <= 0:
            raise ValueError("App and installation ids are positive integers")
        self._app_id = app_id
        self._installation_id = installation_id
        self._key_source = key_source
        self._clock = clock or (lambda: datetime.now(UTC))
        self._http = http_client or httpx.Client(
            base_url=GITHUB_API,
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
        )

    def token(
        self, repository: str, permissions: Mapping[str, Access]
    ) -> InstallationToken:
        """Exactly one repository and exactly these permissions, or nothing."""
        if not _REPOSITORY.fullmatch(repository):
            raise ValueError("repository is owner/name")
        if not permissions or any(
            not _PERMISSION.fullmatch(name) or level not in ("read", "write")
            for name, level in permissions.items()
        ):
            raise ValueError("permissions are an explicit {name: read|write} map")
        jwt = app_jwt(self._app_id, self._key_source.load(), self._clock())
        try:
            response = self._http.post(
                f"/app/installations/{self._installation_id}/access_tokens",
                json={
                    "repositories": [repository.split("/", 1)[1]],
                    "permissions": dict(permissions),
                },
                headers=_headers(jwt),
            )
        except httpx.ConnectError, httpx.ConnectTimeout:
            raise AppTokenError("unavailable", "GitHub could not be reached") from None
        except httpx.TransportError:
            raise AppTokenError(
                "ambiguous", "GitHub did not answer; any token issued expires unused"
            ) from None
        if response.is_redirect:
            raise AppTokenError("refused", "GitHub redirected the token request")
        if response.status_code >= 500:
            raise AppTokenError(
                "ambiguous", f"GitHub failed with HTTP {response.status_code}"
            )
        if response.status_code != 201:
            raise AppTokenError(
                "refused", f"GitHub refused the token with HTTP {response.status_code}"
            )
        return self._checked(response, repository, permissions)

    def _checked(
        self,
        response: httpx.Response,
        repository: str,
        permissions: Mapping[str, Access],
    ) -> InstallationToken:
        try:
            payload = response.json()
            token = SecretStr(str(payload["token"]))
            expires_at = datetime.fromisoformat(
                str(payload["expires_at"]).replace("Z", "+00:00")
            )
            granted = dict(payload.get("permissions") or {})
            repositories = [item["full_name"] for item in payload["repositories"]]
        except ValueError, KeyError, TypeError:
            raise AppTokenError(
                "refused", "GitHub's token answer is malformed"
            ) from None
        # GitHub always adds metadata: read; anything else beyond the request
        # is a broader scope than asked for.
        if "metadata" not in permissions and granted.get("metadata") == "read":
            granted.pop("metadata")
        if repositories != [repository] or granted != dict(permissions):
            # Broader than asked for: never use it, and revoke it at once.
            self.revoke(token)
            raise AppTokenError("refused", "GitHub granted a different scope")
        if expires_at <= self._clock():
            raise AppTokenError("refused", "the token is already expired")
        return InstallationToken(token, expires_at, repository, dict(permissions))

    def revoke(self, token: SecretStr) -> None:
        """Best effort: a token expires on its own within the hour anyway."""
        try:
            self._http.delete(
                "/installation/token",
                headers=_headers(token.get_secret_value(), scheme="token"),
            )
        except httpx.TransportError:
            pass


def _headers(credential: str, *, scheme: str = "Bearer") -> dict[str, str]:
    return {
        "Authorization": f"{scheme} {credential}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zeocore-github-app",
    }


__all__ = [
    "GITHUB_API",
    "AppKeySource",
    "AppTokenError",
    "InstallationToken",
    "InstallationTokens",
    "KeychainAppKeySource",
    "app_jwt",
]
