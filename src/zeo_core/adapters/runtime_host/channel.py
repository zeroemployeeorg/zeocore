"""Bounded request/reply on a trusted-launcher inherited Unix socket.

No URL, bearer token, provider credential, connection fallback or retry loop.
The parent Runtime authenticates the launch and consumes bootstrap/effect IDs.
"""

from __future__ import annotations

import os
import secrets
import socket
import struct
import threading
import time
from typing import Any

from zeo_core.adapters.runtime_host.canonical import (
    ProtocolError,
    canonical_bytes,
    digest,
    manifest_inventory,
    parse_json,
)
from zeo_core.contracts.runtime import LaunchContext, RuntimeReply


class RuntimeChannel:
    def __init__(self, connection: socket.socket, context: LaunchContext) -> None:
        if connection.family != socket.AF_UNIX or connection.type != socket.SOCK_STREAM:
            raise ProtocolError(
                "Runtime channel must be an inherited Unix stream socket"
            )
        connection.getpeername()  # refuses unconnected/listening sockets
        self._socket = connection
        self.context = context
        self._nonce = secrets.token_hex(32)
        self._sequence = 0
        self._lock = threading.Lock()
        self._bootstrapped = False
        self._broken = False
        self._exchange_deadline = 0.0

    def _read(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            remaining = self._exchange_deadline - time.monotonic()
            if remaining <= 0:
                raise ProtocolError("Runtime exchange deadline elapsed")
            self._socket.settimeout(remaining)
            chunk = self._socket.recv(size - len(chunks))
            if not chunk:
                raise ProtocolError("Runtime disconnected")
            chunks.extend(chunk)
        return bytes(chunks)

    def call(self, method: str, payload: dict[str, Any]) -> RuntimeReply:
        with self._lock:
            if self._broken or (method != "bootstrap" and not self._bootstrapped):
                raise ProtocolError("Runtime session unavailable")
            if method == "bootstrap" and self._sequence:
                raise ProtocolError("bootstrap already attempted")
            remaining = self.context.deadline_unix_ms / 1000 - time.time()
            if remaining <= 0:
                raise ProtocolError("attempt deadline elapsed")
            timeout = min(remaining, self.context.rpc_timeout_ms / 1000)
            self._exchange_deadline = time.monotonic() + timeout
            self._socket.settimeout(timeout)
            self._sequence += 1
            message = {
                "protocol_version": 1,
                "sequence": self._sequence,
                "host_nonce": self._nonce,
                "binding": self.context.attempt.model_dump(mode="json"),
                "method": method,
                "payload": payload,
            }
            raw = canonical_bytes(message)
            if len(raw) > self.context.max_message_bytes:
                self._broken = True
                raise ProtocolError("Runtime request too large")
            try:
                self._socket.sendall(struct.pack("!I", len(raw)) + raw)
                length = struct.unpack("!I", self._read(4))[0]
                if not 0 < length <= self.context.max_message_bytes:
                    raise ProtocolError("Runtime reply too large")
                reply_raw = self._read(length)
                if time.time() * 1000 >= self.context.deadline_unix_ms:
                    raise ProtocolError("late Runtime reply")
                parse_json(reply_raw, limit=self.context.max_message_bytes)
                reply = RuntimeReply.model_validate_json(reply_raw)
                if (
                    reply.sequence != self._sequence
                    or reply.host_nonce != self._nonce
                    or reply.binding != self.context.attempt
                    or reply.request_digest != digest(message)
                ):
                    raise ProtocolError("Runtime reply binding mismatch")
                return reply
            except (OSError, ValueError) as exc:
                self._broken = True
                raise ProtocolError(
                    "Runtime exchange failed; no automatic retry"
                ) from exc

    def bootstrap(self) -> RuntimeReply:
        launch = self.context.model_dump(mode="json")
        launch["provider"]["manifests"] = manifest_inventory(
            self.context.provider.manifests
        )
        reply = self.call(
            "bootstrap",
            {
                "bootstrap_id": self.context.bootstrap_id,
                "pid": os.getpid(),
                "launch_digest": digest(launch),
            },
        )
        self._bootstrapped = reply.state == "allowed"
        return reply

    def close(self) -> None:
        self._broken = True
        self._socket.close()


__all__ = [
    "RuntimeChannel",
]
