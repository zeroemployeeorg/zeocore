"""In-process fakes for YouTube's resumable upload server and ZEOconnect's broker.

The fake server keeps real byte offsets, so tests can drop a connection mid-chunk
(with part of the chunk stored), lose an answer after the bytes landed, expire a
session, or refuse an unauthenticated link, and then check that the device resumes
at the right byte and never creates a second video.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from zeo_core.integrations.hosted.client import (
    HostedOperationResponse,
    HostedOperationStatus,
)

PREFIX = "https://www.googleapis.com/upload/youtube/v3/"


@dataclass
class Response:
    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    text: str = ""


@dataclass
class Session:
    kind: str
    size: int
    meta: dict[str, Any]
    data: bytearray = field(default_factory=bytearray)
    expired: bool = False
    resource: dict[str, Any] | None = None


@dataclass
class Video:
    video_id: str
    title: str
    size: int
    privacy: str
    publish_at: str | None
    published_at: str
    content_sha256: str
    processing: str = "succeeded"
    upload: str = "processed"


class FakeYouTube:
    """Upload sessions plus the channel's videos, captions and playlists."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.videos: dict[str, Video] = {}
        self.thumbnails: dict[str, str] = {}
        self.captions: dict[str, list[dict[str, str]]] = {}
        self.playlists: dict[str, list[str]] = {}
        self.puts: list[tuple[str, str]] = []
        #: Hooks a test sets to break the next request(s).
        self.drop_after: int | None = None  # store this many bytes, then drop
        self.lose_final_answer = False
        self.fail_statuses: list[int] = []
        self.require_token = False
        self.force_privacy: str | None = None

    # The device side -------------------------------------------------

    def open(self, kind: str, size: int, meta: dict[str, Any]) -> str:
        url = f"{PREFIX}{kind}?upload_id=u{len(self.sessions) + 1}"
        self.sessions[url] = Session(kind, size, meta)
        return url

    def put(
        self, url: str, *, headers: Mapping[str, str], content: bytes, timeout: float
    ) -> Response:
        del timeout
        content_range = headers["Content-Range"]
        self.puts.append((url, content_range))
        if self.require_token and "Authorization" not in headers:
            return Response(401, text=_error("authError"))
        if self.fail_statuses:
            return Response(self.fail_statuses.pop(0))
        session = self.sessions.get(url)
        if session is None or session.expired:
            return Response(404, text=_error("notFound"))
        if session.resource is not None:
            return Response(200, text=json.dumps(session.resource))
        spec = content_range.removeprefix("bytes ")
        if spec.startswith("*/"):
            return self._incomplete(session)
        return self._accept(session, spec, content)

    def _accept(self, session: Session, spec: str, content: bytes) -> Response:
        span, _, total = spec.partition("/")
        start, _, end = span.partition("-")
        start_i, end_i = int(start), int(end)
        assert int(total) == session.size
        assert end_i - start_i + 1 == len(content)
        if start_i > len(session.data):
            return Response(400, text=_error("badRange"))
        # Bytes YouTube already has are accepted again harmlessly.
        fresh = content[len(session.data) - start_i :]
        if self.drop_after is not None:
            session.data.extend(fresh[: self.drop_after])
            self.drop_after = None
            raise httpx.ReadError("connection reset by peer")
        session.data.extend(fresh)
        if len(session.data) < session.size:
            return self._incomplete(session)
        session.resource = self._complete(session)
        if self.lose_final_answer:
            self.lose_final_answer = False
            raise httpx.ReadTimeout("answer lost")
        return Response(200, text=json.dumps(session.resource))

    def _incomplete(self, session: Session) -> Response:
        if not session.data:
            return Response(308)
        return Response(308, {"Range": f"bytes=0-{len(session.data) - 1}"})

    def _complete(self, session: Session) -> dict[str, Any]:
        digest = hashlib.sha256(bytes(session.data)).hexdigest()
        if session.kind == "videos":
            video_id = f"vid{len(self.videos) + 1:08d}"
            status = session.meta["status"]
            self.videos[video_id] = Video(
                video_id=video_id,
                title=session.meta["metadata"]["title"],
                size=session.size,
                privacy=self.force_privacy or status["privacy"],
                publish_at=status.get("publish_at"),
                published_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                content_sha256=digest,
            )
            return {"id": video_id, "status": {"privacyStatus": status["privacy"]}}
        if session.kind == "thumbnails/set":
            self.thumbnails[session.meta["video_id"]] = digest
            return {"items": [{"default": {"url": "https://i.ytimg.com/x.jpg"}}]}
        track = {
            "caption_id": f"cap{sum(len(v) for v in self.captions.values()) + 1}",
            "language": session.meta["language"],
            "name": session.meta["name"],
        }
        self.captions.setdefault(session.meta["video_id"], []).append(track)
        return {"id": track["caption_id"]}

    def expire_all(self) -> None:
        for session in self.sessions.values():
            session.expired = True


class FakeBroker:
    """ZEOconnect's YouTube operations with its approval behaviour.

    An effect returns APPROVAL_REQUIRED until the test approves it (``auto_approve``
    or ``approve``). Like the real broker, approving a key and invoking it again runs
    the effect once; invoking the same key again returns the recorded result without
    the upload link (the broker never stores links).
    """

    EFFECTS = frozenset(
        {
            "youtube.video.upload_session.create",
            "youtube.thumbnail.upload_session.create",
            "youtube.caption.upload_session.create",
            "youtube.video.status.update",
            "youtube.playlist.item.add",
        }
    )

    def __init__(self, youtube: FakeYouTube, *, auto_approve: bool = True) -> None:
        self.youtube = youtube
        self.auto_approve = auto_approve
        self.approved: set[str] = set()
        self.executed: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.refuse: set[str] = set()
        self.on_invoke: Callable[[str], None] | None = None

    def approve(self, key: str) -> None:
        self.approved.add(key)

    def invoke(
        self, operation_id: str, arguments: dict[str, Any], idempotency_key: str
    ) -> HostedOperationResponse:
        self.calls.append((operation_id, idempotency_key))
        if self.on_invoke is not None:
            self.on_invoke(operation_id)
        if operation_id in self.refuse:
            return HostedOperationResponse(
                status=HostedOperationStatus.REFUSED, execution_id="exe-refused"
            )
        if operation_id not in self.EFFECTS:
            return _confirmed(self._read(operation_id, arguments))
        if idempotency_key in self.executed:
            replay = dict(self.executed[idempotency_key])
            if "upload_url" in replay:
                replay = {"upload_url": None, "relay_seal": None, "replayed": True}
            return _confirmed(replay)
        if self.auto_approve:
            self.approved.add(idempotency_key)
        if idempotency_key not in self.approved:
            return HostedOperationResponse(
                status=HostedOperationStatus.APPROVAL_REQUIRED,
                execution_id="apr_" + idempotency_key[-8:],
                approval_url=f"https://connect.example/approvals/{idempotency_key[-12:]}",
            )
        result = self._effect(operation_id, arguments)
        self.executed[idempotency_key] = result
        return _confirmed(result)

    def _effect(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        yt = self.youtube
        kinds = {
            "youtube.video.upload_session.create": "videos",
            "youtube.thumbnail.upload_session.create": "thumbnails/set",
            "youtube.caption.upload_session.create": "captions",
        }
        if operation in kinds:
            url = yt.open(kinds[operation], args["size_bytes"], args)
            return {"upload_url": url, "relay_seal": seal_for(url)}
        if operation == "youtube.playlist.item.add":
            items = yt.playlists.setdefault(args["playlist_id"], [])
            present = args["video_id"] in items
            if not present:
                items.append(args["video_id"])
            return {
                "playlist_item_id": f"pli-{args['video_id']}",
                "already_present": present,
            }
        video = yt.videos[args["video_id"]]
        video.privacy = args["privacy"]
        video.publish_at = args["publish_at"]
        return {
            "video_id": video.video_id,
            "privacy": video.privacy,
            "publish_at": video.publish_at,
        }

    def _read(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        yt = self.youtube
        if operation == "youtube.video.get":
            video = yt.videos[args["video_id"]]
            return {
                "video_id": video.video_id,
                "title": video.title,
                "privacy": video.privacy,
                "publish_at": video.publish_at,
                "upload_status": video.upload,
                "processing_status": video.processing,
                "failure_reason": None,
                "rejection_reason": None,
            }
        if operation == "youtube.video.find_upload":
            return {
                "matches": [
                    {
                        "video_id": v.video_id,
                        "privacy": v.privacy,
                        "upload_status": v.upload,
                    }
                    for v in yt.videos.values()
                    if v.title == args["title"] and v.size == args["size_bytes"]
                ]
            }
        if operation == "youtube.captions.list":
            return {"tracks": list(yt.captions.get(args["video_id"], []))}
        if operation == "youtube.channel.get":
            return {"channel_id": "UC1", "title": "Rasa", "handle": "@rasahq"}
        raise AssertionError(f"unexpected read {operation}")


def seal_for(url: str) -> str:
    return hashlib.sha256(b"fake-relay|" + url.encode()).hexdigest()


class FakeRelayTransport:
    """ZEOconnect's relay endpoint: checks the seal, adds the token, forwards."""

    def __init__(self, youtube: FakeYouTube) -> None:
        self.youtube = youtube
        self.chunks: list[int] = []

    def relay_youtube_chunk(
        self,
        *,
        connection_id: str,
        link: str,
        seal: str,
        content_range: str,
        content_type: str,
        body: bytes,
    ) -> dict[str, Any]:
        if seal != seal_for(link) or not connection_id.startswith("con_"):
            from zeo_core.integrations.hosted.client import HostedClientError

            raise HostedClientError("hosted request was refused")
        self.chunks.append(len(body))
        answer = self.youtube.put(
            link,
            headers={
                "Authorization": "Bearer custody-token",
                "Content-Range": content_range,
                "Content-Type": content_type,
            },
            content=body,
            timeout=280,
        )
        resource = json.loads(answer.text) if answer.status_code in (200, 201) else None
        return {
            "status": answer.status_code,
            "range": dict(answer.headers).get("Range"),
            "resource": resource,
            "reason": "",
        }


def _confirmed(result: dict[str, Any]) -> HostedOperationResponse:
    return HostedOperationResponse(
        status=HostedOperationStatus.CONFIRMED, execution_id="exe-1", result=result
    )


def _error(reason: str) -> str:
    return json.dumps({"error": {"errors": [{"reason": reason}]}})
