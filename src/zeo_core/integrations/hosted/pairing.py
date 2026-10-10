"""Explicit device pairing and secure local custody for ZEOconnect sessions."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr

from zeo_core.connections.adapters.subprocess_runner import (
    RealSubprocessRunner,
    SubprocessRunner,
)
from zeo_core.integrations.hosted.client import HostedSessionError
from zeo_core.integrations.hosted.profile import (
    HostedConnectionSummary,
    ServiceRequirement,
)

_KEYCHAIN_NOT_FOUND = frozenset({36, 44})
_KEYCHAIN_SERVICE = "org.zeroemployee.zeocore.zeoconnect-session"
_KEYCHAIN_ACCOUNT = "zeoconnect-device-session-v1"
_PROFILE = re.compile(r"[a-z][a-z0-9-]{0,31}")


class SecureStoreError(RuntimeError):
    """Sanitized failure at the reusable-device-authority boundary."""


class SessionUnavailableError(SecureStoreError, HostedSessionError):
    """No paired session is stored: a store failure and a session error both."""


class PairingPendingError(RuntimeError):
    """The browser ceremony has not completed yet."""


class PairingChallenge(BaseModel):
    """Browser-safe instructions plus a locally confined polling credential."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pairing_id: str = Field(..., min_length=1, max_length=200)
    device_code: SecretStr = Field(..., exclude=True, repr=False)
    verification_url: AnyHttpUrl
    user_code: str = Field(..., min_length=1, max_length=32)
    expires_at: datetime
    polling_interval_seconds: int = Field(..., ge=1, le=60)


class DeviceSession(BaseModel):
    """Reusable ZEOconnect authority; token values never serialize or repr."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    device_id: str = Field(..., min_length=1, max_length=200)
    access_token: SecretStr = Field(..., exclude=True, repr=False)
    refresh_token: SecretStr = Field(..., exclude=True, repr=False)
    access_expires_at: datetime
    refresh_expires_at: datetime


@runtime_checkable
class SecureSessionStore(Protocol):
    """Local custody for device authority; plaintext fallback is forbidden."""

    def load(self) -> DeviceSession | None: ...

    def save(self, session: DeviceSession) -> None: ...

    def delete(self) -> None: ...


class InMemorySecureSessionStore:
    """Deterministic test store; never selected as a production fallback."""

    def __init__(self) -> None:
        self._session: DeviceSession | None = None

    def load(self) -> DeviceSession | None:
        return self._session

    def save(self, session: DeviceSession) -> None:
        self._session = session

    def delete(self) -> None:
        self._session = None


class KeychainSecureSessionStore:
    """macOS Keychain storage using stdin for secret-bearing writes.

    ``profile`` names one app's own device grant, so one machine can hold
    several, each paired and revoked on its own. It is attribution, not a
    security boundary: every profile of one macOS user can read the others.
    No profile is the original entry, which existing pairings keep using.

    ``origin`` binds the entry to a non-production Broker: a session paired
    against one origin is never sent to another. None means production, the
    original entry.
    """

    def __init__(
        self,
        *,
        runner: SubprocessRunner | None = None,
        profile: str | None = None,
        origin: str | None = None,
    ) -> None:
        if profile is not None and not _PROFILE.fullmatch(profile):
            raise SecureStoreError("session profile name is invalid")
        if runner is None and sys.platform != "darwin":
            raise SecureStoreError("secure session storage is unavailable")
        self._runner = runner or RealSubprocessRunner()
        from zeo_core.integrations.environment import managed_state_dir

        state = managed_state_dir()
        self._service = _KEYCHAIN_SERVICE
        if state is not None:
            digest = hashlib.sha256(str(state).encode()).hexdigest()[:16]
            self._service = f"{_KEYCHAIN_SERVICE}.{state.name}.{digest}"
        if profile is not None:
            self._service = f"{self._service}.profile.{profile}"
        if origin is not None:
            digest = hashlib.sha256(origin.encode()).hexdigest()[:16]
            self._service = f"{self._service}.origin.{digest}"

    def load(self) -> DeviceSession | None:
        result = self._runner.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                _KEYCHAIN_ACCOUNT,
                "-s",
                self._service,
                "-w",
            ]
        )
        if result.returncode in _KEYCHAIN_NOT_FOUND:
            return None
        if result.returncode != 0:
            raise SecureStoreError("secure session storage is unavailable")
        material = result.stdout.strip()
        try:
            payload = json.loads(material)
            return DeviceSession(
                device_id=payload["device_id"],
                access_token=SecretStr(payload["access_token"]),
                refresh_token=SecretStr(payload["refresh_token"]),
                access_expires_at=payload["access_expires_at"],
                refresh_expires_at=payload["refresh_expires_at"],
            )
        except Exception:
            raise SecureStoreError("secure session storage is invalid") from None
        finally:
            del material

    def save(self, session: DeviceSession) -> None:
        material = json.dumps(
            {
                "device_id": session.device_id,
                "access_token": session.access_token.get_secret_value(),
                "refresh_token": session.refresh_token.get_secret_value(),
                "access_expires_at": session.access_expires_at.isoformat(),
                "refresh_expires_at": session.refresh_expires_at.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            result = self._runner.run_with_secret_stdin(
                [
                    "/usr/bin/security",
                    "add-generic-password",
                    "-a",
                    _KEYCHAIN_ACCOUNT,
                    "-s",
                    self._service,
                    "-U",
                    "-w",
                ],
                secret_lines=[material, material],
            )
        finally:
            del material
        if result.returncode != 0:
            raise SecureStoreError("secure session storage is unavailable")

    def delete(self) -> None:
        result = self._runner.run(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-a",
                _KEYCHAIN_ACCOUNT,
                "-s",
                self._service,
            ]
        )
        if result.returncode not in {0, *_KEYCHAIN_NOT_FOUND}:
            raise SecureStoreError("secure session storage is unavailable")


@runtime_checkable
class PairingTransport(Protocol):
    """Explicit network actions implemented by the native hosted transport."""

    def begin_pairing(self, *, device_name: str) -> PairingChallenge: ...

    def poll_pairing(self, challenge: PairingChallenge) -> DeviceSession: ...

    def refresh_session(self, session: DeviceSession) -> DeviceSession: ...

    def list_connections(
        self, session: DeviceSession
    ) -> tuple[HostedConnectionSummary, ...]: ...

    def revoke_device(self, session: DeviceSession) -> None: ...


class HostedConnectionManager:
    """Consent-bound pairing actions; construction and resolution stay inert."""

    def __init__(
        self,
        *,
        transport: PairingTransport,
        session_store: SecureSessionStore,
        catalog_sink: Callable[[Sequence[HostedConnectionSummary]], None] | None = None,
    ) -> None:
        self._transport = transport
        self._session_store = session_store
        self._catalog_sink = catalog_sink
        self._connections: tuple[HostedConnectionSummary, ...] = ()

    @property
    def connections(self) -> tuple[HostedConnectionSummary, ...]:
        return self._connections

    def begin_pairing(
        self, requirement: ServiceRequirement, *, device_name: str
    ) -> PairingChallenge:
        del requirement  # The server derives tenant and later connection selection.
        return self._transport.begin_pairing(device_name=device_name)

    def complete_pairing(
        self, challenge: PairingChallenge
    ) -> tuple[HostedConnectionSummary, ...]:
        session = self._transport.poll_pairing(challenge)
        self._session_store.save(session)
        self._connections = self._transport.list_connections(session)
        self._publish_catalog()
        return self._connections

    def refresh_connections(self) -> tuple[HostedConnectionSummary, ...]:
        session = self._required_session()
        self._connections = self._transport.list_connections(session)
        self._publish_catalog()
        return self._connections

    def refresh_session(self) -> DeviceSession:
        session = self._transport.refresh_session(self._required_session())
        self._session_store.save(session)
        return session

    def revoke_device(self) -> None:
        session = self._required_session()
        self._transport.revoke_device(session)
        self._session_store.delete()
        self._connections = ()
        self._publish_catalog()

    def _required_session(self) -> DeviceSession:
        session = self._session_store.load()
        if session is None:
            raise SessionUnavailableError("paired device session is unavailable")
        return session

    def _publish_catalog(self) -> None:
        if self._catalog_sink is not None:
            self._catalog_sink(self._connections)


__all__ = [
    "DeviceSession",
    "HostedConnectionManager",
    "InMemorySecureSessionStore",
    "KeychainSecureSessionStore",
    "PairingChallenge",
    "PairingPendingError",
    "PairingTransport",
    "SecureSessionStore",
    "SecureStoreError",
    "SessionUnavailableError",
]
