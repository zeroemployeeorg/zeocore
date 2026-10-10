"""The ZEOconnect Broker client as commands: one client, for every language.

``zeocore login | logout | whoami | connections | invoke | artifact get``.
Apps never hold provider tokens: this machine holds only each app's own
device grant (``--profile`` or ``ZEOCORE_PROFILE``), in the Keychain. With no
usable Keychain the command fails closed with exit 12; there is no plaintext
fallback.

An invocation's idempotency key is derived from the request (connection,
operation, arguments and ``--occurrence``), so asking again with the same
request is an exact replay of the stored outcome, never a second act.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from zeo_core.adapters.runtime_host.canonical import ProtocolError, digest, parse_json
from zeo_core.integrations.hosted.client import (
    HostedArtifactDescriptor,
    HostedClientError,
    HostedConnectionClient,
    HostedOperationRequest,
    HostedOperationResponse,
    HostedOperationStatus,
    HostedSessionError,
    HostedStoppedError,
    HostedUnavailableError,
    HostedUnreachableError,
    stop_of,
)
from zeo_core.integrations.hosted.pairing import (
    HostedConnectionManager,
    KeychainSecureSessionStore,
    PairingPendingError,
    SecureSessionStore,
    SecureStoreError,
)
from zeo_core.integrations.hosted.profile import ServiceRequirement
from zeo_core.integrations.hosted.transport import (
    ZEOCONNECT_PRODUCTION_ORIGIN,
    ZEOconnectHTTPTransport,
)

from .protocol import (
    EXIT_AMBIGUOUS,
    EXIT_APPROVAL,
    EXIT_DONE,
    EXIT_HELD,
    EXIT_NOT_PAIRED,
    EXIT_WAIT,
    Answer,
    Arguments,
    ArgumentsError,
    Emit,
    emit,
    invalid,
)

PROFILE_ENV: Final = "ZEOCORE_PROFILE"
_APPROVAL_POLL_SECONDS: Final = 5


# Seams a test replaces; production builds the real Keychain store and the
# fixed-origin transport.
def origin() -> str:
    return os.environ.get("ZEOCONNECT_URL", ZEOCONNECT_PRODUCTION_ORIGIN)


def make_store(profile: str | None) -> SecureSessionStore:
    # A grant is bound to the origin it was paired with: a production session
    # never reaches a development Broker, and the reverse.
    selected = origin()
    return KeychainSecureSessionStore(
        profile=profile,
        origin=None if selected == ZEOCONNECT_PRODUCTION_ORIGIN else selected,
    )


def make_transport(store: SecureSessionStore) -> ZEOconnectHTTPTransport:
    return ZEOconnectHTTPTransport(
        session_store=store,
        base_url=origin(),
        allow_development_origin=os.environ.get("ZEOCONNECT_DEVELOPMENT") == "1",
    )


sleep: Callable[[float], None] = time.sleep
now: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731


class _NotPairedError(Exception):
    def __init__(self, outcome: str, message: str) -> None:
        self.outcome = outcome
        super().__init__(message)


def _not_paired(error: Exception) -> Answer | None:
    """Exit 12: no usable grant on this machine, for this profile."""
    text = str(error)
    if isinstance(error, _NotPairedError):
        return EXIT_NOT_PAIRED, {"ok": False, "outcome": error.outcome, "message": text}
    if isinstance(error, HostedSessionError):
        return EXIT_NOT_PAIRED, {
            "ok": False,
            "outcome": "not_paired",
            "message": "this device is not paired with ZEOconnect; run zeocore login",
        }
    if isinstance(error, SecureStoreError):
        return EXIT_NOT_PAIRED, {
            "ok": False,
            "outcome": "no_session_store",
            "message": "no usable secure session store on this machine",
        }
    return None


def _profile(value: str | None) -> str | None:
    return value or os.environ.get(PROFILE_ENV) or None


def _open(profile: str | None) -> tuple[SecureSessionStore, ZEOconnectHTTPTransport]:
    try:
        store = make_store(profile)
    except SecureStoreError as error:
        if "profile" in str(error):
            raise ArgumentsError(str(error)) from None
        raise _NotPairedError(
            "no_session_store", "no usable secure session store on this machine"
        ) from None
    try:
        transport = make_transport(store)
    except ValueError:
        # The origin itself is unusable: a configuration error, nothing sent.
        raise ArgumentsError("ZEOCONNECT_URL is not an allowed Broker origin") from None
    return store, transport


def _guarded(body: Callable[[], Answer]) -> Answer:
    """Turn the client's known failures into the protocol's answers."""
    try:
        return body()
    except ArgumentsError as error:
        return invalid(str(error))
    except (HostedClientError, SecureStoreError, _NotPairedError) as error:
        answer = _not_paired(error)
        if answer is not None:
            return answer
        if isinstance(error, HostedStoppedError):
            return EXIT_HELD, {"ok": False, "outcome": "stopped", "message": str(error)}
        if isinstance(error, HostedUnavailableError):
            return EXIT_WAIT, {
                "ok": False,
                "outcome": "unavailable",
                "message": str(error),
            }
        return EXIT_HELD, {"ok": False, "outcome": "refused", "message": str(error)}


# -- login, logout, whoami ---------------------------------------------------------


def login(args: Sequence[str], _stdin: bytes, *, out: Emit = emit) -> Answer:
    parser = Arguments("zeocore login")
    parser.add_argument("--profile")
    parser.add_argument("--device-name")

    def body() -> Answer:
        options = parser.parse_args(list(args))
        profile = _profile(options.profile)
        store, transport = _open(profile)
        try:
            manager = HostedConnectionManager(transport=transport, session_store=store)
            name = options.device_name or (
                f"zeocore on {socket.gethostname()}"
                + (f" ({profile})" if profile else "")
            )
            challenge = manager.begin_pairing(
                ServiceRequirement(service="zeocore", operations=("zeocore.login",)),
                device_name=name[:200],
            )
            out(
                {
                    "event": "pair",
                    "verification_url": str(challenge.verification_url),
                    "user_code": challenge.user_code,
                    "expires_at": challenge.expires_at.isoformat(),
                }
            )
            while now() < challenge.expires_at:
                try:
                    connections = manager.complete_pairing(challenge)
                except PairingPendingError:
                    sleep(challenge.polling_interval_seconds)
                    continue
                return EXIT_DONE, {
                    "ok": True,
                    "state": "paired",
                    "profile": profile,
                    "connections": [_summary(item) for item in connections],
                }
            return EXIT_HELD, {"ok": False, "outcome": "pairing_expired"}
        finally:
            transport.close()

    return _guarded(body)


def logout(args: Sequence[str], _stdin: bytes) -> Answer:
    parser = Arguments("zeocore logout")
    parser.add_argument("--profile")

    def body() -> Answer:
        profile = _profile(parser.parse_args(list(args)).profile)
        store, transport = _open(profile)
        try:
            if store.load() is None:
                return EXIT_DONE, {
                    "ok": True,
                    "state": "not_paired",
                    "profile": profile,
                }
            HostedConnectionManager(
                transport=transport, session_store=store
            ).revoke_device()
            return EXIT_DONE, {"ok": True, "state": "revoked", "profile": profile}
        finally:
            transport.close()

    return _guarded(body)


def whoami(args: Sequence[str], _stdin: bytes) -> Answer:
    """This profile's grant, read locally: no network, and never a token."""
    parser = Arguments("zeocore whoami")
    parser.add_argument("--profile")

    def body() -> Answer:
        profile = _profile(parser.parse_args(list(args)).profile)
        store, transport = _open(profile)
        transport.close()
        session = store.load()
        if session is None:
            raise _NotPairedError(
                "not_paired", "this profile is not paired; run zeocore login"
            )
        return EXIT_DONE, {
            "ok": True,
            "paired": True,
            "profile": profile,
            "device_id": session.device_id,
            "access_expires_at": session.access_expires_at.isoformat(),
            "refresh_expires_at": session.refresh_expires_at.isoformat(),
        }

    return _guarded(body)


# -- connections -----------------------------------------------------------------


def _summary(summary: Any) -> dict[str, Any]:  # noqa: ANN401 -- a pydantic model
    data: dict[str, Any] = summary.model_dump(mode="json")
    data["connection_id"] = str(summary.handle)
    data.pop("handle", None)
    return data


def connections(args: Sequence[str], _stdin: bytes) -> Answer:
    parser = Arguments("zeocore connections")
    parser.add_argument("--profile")
    parser.add_argument("--service")

    def body() -> Answer:
        options = parser.parse_args(list(args))
        store, transport = _open(_profile(options.profile))
        try:
            listed = HostedConnectionManager(
                transport=transport, session_store=store
            ).refresh_connections()
        finally:
            transport.close()
        return EXIT_DONE, {
            "ok": True,
            "connections": [
                _summary(item)
                for item in listed
                if options.service is None or item.service == options.service
            ],
        }

    return _guarded(body)


# -- invoke ----------------------------------------------------------------------


def request_key(
    *, connection: str, operation: str, arguments: object, occurrence: str
) -> str:
    """The invocation's idempotency key: the same request, the same key."""
    material = digest(
        {
            "connection_id": connection,
            "operation_id": operation,
            "arguments": arguments,
            "occurrence": occurrence,
        }
    )
    return "zc-" + material.removeprefix("sha256:")


def invoke(args: Sequence[str], stdin: bytes, *, out: Emit = emit) -> Answer:
    parser = Arguments("zeocore invoke")
    parser.add_argument("operation_id")
    parser.add_argument("--connection", required=True)
    parser.add_argument("--occurrence", default="1")
    parser.add_argument("--wait-approval", type=int, metavar="SECONDS")
    parser.add_argument("--profile")

    def body() -> Answer:
        options = parser.parse_args(list(args))
        if options.wait_approval is not None and not 0 < options.wait_approval <= 3600:
            raise ArgumentsError("--wait-approval is 1 to 3600 seconds")
        try:
            arguments = parse_json(stdin)
        except ProtocolError as error:
            raise ArgumentsError(str(error)) from None
        if not isinstance(arguments, dict):
            raise ArgumentsError("the arguments on stdin are a JSON object")
        key = request_key(
            connection=options.connection,
            operation=options.operation_id,
            arguments=arguments,
            occurrence=options.occurrence,
        )
        try:
            request = HostedOperationRequest(
                connection_id=options.connection,
                operation_id=options.operation_id,
                arguments=arguments,
                idempotency_key=key,
            )
        except ValidationError:
            raise ArgumentsError(
                "the invocation is not a valid operation request"
            ) from None
        store, transport = _open(_profile(options.profile))
        try:
            client = HostedConnectionClient(transport=transport)
            deadline = (
                time.monotonic() + options.wait_approval
                if options.wait_approval
                else None
            )
            announced = False
            while True:
                try:
                    response = client.invoke(request)
                except HostedUnreachableError as error:
                    return _unreachable(error, key)
                waiting = response.status is HostedOperationStatus.APPROVAL_REQUIRED
                if not waiting or deadline is None or time.monotonic() >= deadline:
                    return _answer(response, key)
                if not announced:
                    out(
                        {
                            "event": "approval",
                            "approval_url": str(response.approval_url),
                            "request_key": key,
                        }
                    )
                    announced = True
                sleep(_APPROVAL_POLL_SECONDS)
        finally:
            transport.close()

    return _guarded(body)


def _unreachable(error: HostedUnreachableError, key: str) -> Answer:
    if not error.may_have_arrived:
        # Never connected: the request cannot have arrived.
        return EXIT_WAIT, {
            "ok": False,
            "outcome": "unavailable",
            "message": str(error),
            "retry": "same_request",
            "request_key": key,
        }
    return EXIT_AMBIGUOUS, _unknown(key)


def _unknown(key: str) -> dict[str, Any]:
    return {
        "ok": False,
        "outcome": "ambiguous",
        "message": "ZEOconnect did not answer; the same request replays its outcome",
        "retry": "same_request",
        "request_key": key,
    }


def _answer(response: HostedOperationResponse, key: str) -> Answer:
    answer: dict[str, Any] = {
        **response.model_dump(mode="json", exclude_none=True),
        "request_key": key,
    }
    status = response.status
    if status is HostedOperationStatus.CONFIRMED:
        return EXIT_DONE, {"ok": True, **answer}
    if status is HostedOperationStatus.APPROVAL_REQUIRED:
        return EXIT_APPROVAL, {"ok": False, "retry": "same_request", **answer}
    if status is HostedOperationStatus.AMBIGUOUS:
        in_flight = (response.receipt or {}).get("in_flight") is True
        return (EXIT_WAIT if in_flight else EXIT_AMBIGUOUS), {
            "ok": False,
            "retry": "same_request",
            **answer,
        }
    if stop_of(response) is not None:
        retry = "same_request"  # a stop is refused before anything is recorded
    elif status is HostedOperationStatus.FAILED_SAFE:
        retry = "new_occurrence"  # recorded against the key; it replays
    else:
        retry = "none"  # refused: the request itself has to change
    return EXIT_HELD, {"ok": False, "retry": retry, **answer}


# -- artifact get ----------------------------------------------------------------


def artifact(args: Sequence[str], stdin: bytes) -> Answer:
    """``artifact get --out PATH``: the descriptor from an answer, on stdin."""
    parser = Arguments("zeocore artifact")
    parser.add_argument("action", choices=("get",))
    parser.add_argument("--out", required=True)
    parser.add_argument("--profile")

    def body() -> Answer:
        options = parser.parse_args(list(args))
        try:
            descriptor = HostedArtifactDescriptor.model_validate(parse_json(stdin))
        except ProtocolError, ValidationError:
            raise ArgumentsError("stdin is not an artifact descriptor") from None
        destination = Path(options.out)
        if destination.exists() or not destination.parent.is_dir():
            raise ArgumentsError("--out must be a new file in an existing directory")
        store, transport = _open(_profile(options.profile))
        try:
            content = HostedConnectionClient(transport=transport).download_artifact(
                descriptor
            )
        finally:
            transport.close()
        _write_new(destination, content)
        return EXIT_DONE, {
            "ok": True,
            "path": str(destination),
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "media_type": descriptor.media_type,
        }

    return _guarded(body)


def _write_new(destination: Path, content: bytes) -> None:
    """Publish the whole file at once, never over an existing one."""
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".zeocore-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.link(temporary, destination)
    except FileExistsError:
        raise ArgumentsError("--out must be a new file") from None
    finally:
        os.unlink(temporary)


# -- upload (Broker contract 1.3.0) ------------------------------------------------


def upload(args: Sequence[str], _stdin: bytes) -> Answer:
    """``upload <file> --connection C``: one input image for billed calls."""
    parser = Arguments("zeocore upload")
    parser.add_argument("path")
    parser.add_argument("--connection", required=True)
    parser.add_argument("--profile")

    def body() -> Answer:
        from zeo_core.integrations.imaging.models import ImageInput

        options = parser.parse_args(list(args))
        try:
            image = ImageInput.from_path(options.path)
        except OSError, ValueError:
            # A fixed message: a validation error's text can quote the bytes.
            raise ArgumentsError(
                "the file is not a readable png, jpeg or webp within the bounds"
            ) from None
        store, transport = _open(_profile(options.profile))
        try:
            descriptor = HostedConnectionClient(transport=transport).upload_artifact(
                connection_id=options.connection,
                content=image.content,
                media_type=image.media_type,
            )
        finally:
            transport.close()
        return EXIT_DONE, {"ok": True, **descriptor.model_dump(mode="json")}

    return _guarded(body)


# -- llm (billed LLM chat, proposed contract draft 3) ------------------------------


def llm(args: Sequence[str], stdin: bytes) -> Answer:
    """``llm <operation> --connection C``: the provider body on stdin, exactly.

    The bytes are checked as strict JSON but sent as given, never
    re-serialized, so key order and number formatting survive.
    """
    from zeo_core.integrations.hosted.services import HostedServiceBinding
    from zeo_core.integrations.llms.hosted import (
        HostedLLMChat,
        HostedLLMError,
        LLMOperation,
    )

    parser = Arguments("zeocore llm")
    parser.add_argument(
        "operation",
        choices=(
            "openai.responses.create",
            "openai.chat.completions.create",
            "anthropic.messages.create",
            "nebius.chat.completions.create",
        ),
    )
    parser.add_argument("--connection", required=True)
    parser.add_argument("--occurrence", default="1")
    parser.add_argument("--out")
    parser.add_argument("--profile")

    def body() -> Answer:
        options = parser.parse_args(list(args))
        try:
            parse_json(stdin)
        except ProtocolError as error:
            raise ArgumentsError(str(error)) from None
        destination = Path(options.out) if options.out else None
        if destination is not None and (
            destination.exists() or not destination.parent.is_dir()
        ):
            raise ArgumentsError("--out must be a new file in an existing directory")
        store, transport = _open(_profile(options.profile))
        try:
            chat = HostedLLMChat(
                client=HostedConnectionClient(transport=transport),
                binding=HostedServiceBinding(connection_id=options.connection),
            )
            operation: LLMOperation = options.operation
            try:
                response = chat.send(operation, stdin, occurrence=options.occurrence)
            except ValueError as error:
                raise ArgumentsError(str(error)) from None
            except HostedLLMError as error:
                return _llm_exit(error.outcome), {
                    "ok": False,
                    "outcome": error.outcome,
                    "message": str(error),
                    "retry": error.retry,
                    "request_key": error.request_key,
                    "execution_id": error.execution_id,
                }
        finally:
            transport.close()
        answer: dict[str, Any] = {
            "ok": True,
            **response.model_dump(mode="json", exclude={"provider_body"}),
        }
        if destination is not None:
            _write_new(destination, response.provider_body)
            answer["path"] = str(destination)
        else:
            answer["provider_body_base64"] = base64.b64encode(
                response.provider_body
            ).decode()
        return EXIT_DONE, answer

    return _guarded(body)


def _llm_exit(outcome: str) -> int:
    return {
        "in_flight": EXIT_WAIT,
        "unavailable": EXIT_WAIT,
        "not_paired": EXIT_NOT_PAIRED,
        "ambiguous": EXIT_AMBIGUOUS,
    }.get(outcome, EXIT_HELD)
