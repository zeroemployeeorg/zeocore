"""
Newsletter conversion, stage 2 of the newsletter pipeline (ZEOCORE-SOW-33).

Three functions, all taking text and returning typed data, with no network, no
credential and no file read:

* ``decode_raw_message`` (``_mime``): raw RFC 822 bytes, as ZEOconnect's fetch hands
  them over, to a ``DecodedMessage``: the HTML and plain-text bodies, a few headers
  and Gmail's verdict on the sender. Standard-library ``email`` only.
* ``convert_newsletter``: one message's HTML to clean Markdown through pandoc,
  keeping the links, source, sender and date, and stripping scripts, styles, elements
  the HTML itself marks not rendered, tracking pixels, tracking parameters and
  unsubscribe noise.
* ``convert_decoded_message``: the two joined, for a decoded message.

A fetched newsletter is data, never instructions. Nothing here follows, runs or acts
on what a message says: its text comes out as Markdown inside a typed object, and
every failure is a typed refusal whose message is fixed (see ``refusals``). The
converter decides hidden text from the element alone (the ``hidden`` attribute and its
own inline ``display:none`` or ``visibility:hidden``) and does NOT evaluate CSS: class
and id rules, selectors, ``@media``, inheritance and clipping are reported in
``diagnostics``, not applied (see ``_hidden``). The guarantee is that the result is
inert data.
Time is bounded in-process (one deadline, checked per element and between pieces of
the message), but BeautifulSoup and the email parser cannot be interrupted inside one
step: call these under your own watchdog for mail you do not trust.
The reviewed list of tracker hosts and footer patterns is package data
(``data/newsletter_noise.json``), never read from the caller's directory.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

from zeo_core.integrations.pandoc import _cleaning, _hidden, _noise
from zeo_core.integrations.pandoc._cleaning import ConvertedLink
from zeo_core.integrations.pandoc._mime import (
    MAX_MIME_DEPTH,
    MAX_MIME_PARTS,
    RAW_MAX_BYTES,
    DecodedMessage,
    decode_raw_message,
)
from zeo_core.integrations.pandoc._runner import Deadline, find_pandoc, run_pandoc
from zeo_core.integrations.pandoc.refusals import refusal

__all__ = [
    "MAX_MIME_DEPTH",
    "MAX_MIME_PARTS",
    "RAW_MAX_BYTES",
    "ConvertedItem",
    "ConvertedTextItem",
    "ConvertedLink",
    "DecodedMessage",
    "convert_decoded_message",
    "convert_newsletter",
    "convert_plain_text",
    "decode_raw_message",
    "noise_data_version",
]

DEFAULT_MAX_BYTES: Final = 2_000_000
DEFAULT_TIMEOUT_S: Final = 30.0

#: The cleaner's own version; a change to what it strips is a new version.
CLEANER_VERSION: Final = "newsletter-clean-2"

#: The fixed pandoc flags for the conversion. Raw HTML is off in the reader and the
#: writer, so no markup of the message survives into the Markdown.
PANDOC_FLAGS: Final[tuple[str, ...]] = (
    "--from=html-native_divs-native_spans-raw_html",
    "--to=gfm-raw_html",
    "--wrap=none",
    "--eol=lf",
)

_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class ConvertedItem:
    """
    One converted message. Every field is data; none is ever an instruction.

    ``diagnostics`` is REQUIRED and always holds every key of
    ``_hidden.DIAGNOSTIC_KEYS``: what CSS could hide that was present and NOT
    evaluated (stylesheets, links, ``@import``, ``@media``, techniques named in a kept
    element's own style or in the ``<style>`` text). Zero means none of THOSE was seen:
    techniques not listed are not detected (``clip-path``, ``content-visibility``, a
    closed ``<details>`` or a ``<dialog>``, colour on colour, other spellings of zero
    such as ``opacity:0.0`` or ``font-size:0.0px``, ``transform:scale(0)``,
    ``mso-hide:all``, a small size with ``overflow:hidden``).
    ``converter`` is the convert step's own tuple (pandoc version, binary digest,
    flags, cleaner version, data-file version and digest), so a stored item says
    exactly what produced it. ``sender_verified`` and ``authentication`` come only
    from ``convert_decoded_message``.
    """

    markdown: str
    links: tuple[ConvertedLink, ...]
    title: str | None
    source: str
    sender: str
    date: str
    content_sha256: str
    stripped: dict[str, int]
    diagnostics: dict[str, int]
    warnings: tuple[str, ...]
    pandoc_version: str
    noise_data_version: str
    converter: dict[str, object] = field(default_factory=dict)
    sender_verified: bool = False
    authentication: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, repr=False)
class ConvertedTextItem:
    """Non-email conversion data; attribution/time are caller claims, not proof."""

    markdown: str
    links: tuple[ConvertedLink, ...]
    title: str | None
    source: str
    source_kind: Literal["operator-browser-post"]
    attribution: str
    source_time: str
    content_sha256: str
    render_input_sha256: str
    stripped: dict[str, int]
    diagnostics: dict[str, int]
    warnings: tuple[str, ...]
    pandoc_version: str
    noise_data_version: str
    converter: dict[str, object] = field(default_factory=dict)


def noise_data_version() -> str:
    """The version label of the tracker and footer data this release ships."""
    return _noise.load().version


def _metadata(value: str | datetime, limit: int) -> str:
    text = value.isoformat() if isinstance(value, datetime) else value
    if not isinstance(text, str):
        raise refusal("metadata_invalid")
    text = text.strip()
    if not text or len(text) > limit or _CONTROL.search(text):
        raise refusal("metadata_invalid")
    return text


def _as_text(html_in: str | bytes, max_bytes: int) -> tuple[str, bytes, list[str]]:
    warnings: list[str] = []
    if isinstance(html_in, bytes):
        data = html_in
        text = data.decode("utf-8", "replace")
        if "�" in text:
            warnings.append("bytes that are not UTF-8 were replaced")
    else:
        text = html_in
        data = text.encode("utf-8", "replace")
    if len(data) > max_bytes:
        raise refusal("input_too_large")
    if not text.strip():
        raise refusal("html_empty")
    return text, data, warnings


def _clean(
    text: str, noise: _noise.Noise, deadline: Deadline
) -> tuple[Any, dict[str, int], dict[str, int], list[ConvertedLink], int, str | None]:  # noqa: ANN401
    """Parse and clean: soup, counts, diagnostics, links, opaque count and title."""
    bs4 = _cleaning.bs4_module()
    counts = dict.fromkeys(_cleaning.COUNT_KEYS, 0)
    diagnostics = _hidden.new_diagnostics()
    try:
        _cleaning.precheck(text)
        soup = bs4.BeautifulSoup(
            text,
            "html.parser",
            # a browser keeps the FIRST of two equal attributes (bs4 keeps the last),
            # and splits a class on ASCII whitespace only
            on_duplicate_attribute="ignore",
            multi_valued_attributes=None,
        )
        deadline.check()
        _cleaning.check_bounds(text, soup, deadline)
        title = _cleaning.title_of(soup)
        _hidden.note_sheets(soup, noise, diagnostics, deadline)  # before styles go
        _cleaning.drop_comments(soup, bs4, counts, deadline)
        _cleaning.drop_tags(soup, counts, noise, deadline)
        _cleaning.drop_hidden(soup, noise, counts, diagnostics, deadline)
        _cleaning.drop_images(soup, noise, counts, deadline)
        _cleaning.flatten_tables(soup, counts, deadline)
        _cleaning.drop_footers(soup, noise, counts, deadline)
        links, opaque = _cleaning.clean_links(soup, noise, counts, deadline)
        deadline.check()
    except RecursionError as error:
        raise refusal("html_too_deep") from error
    return soup, counts, diagnostics, links, opaque, title


def _tidy(markdown: str) -> str:
    lines = [line.rstrip() for line in markdown.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def convert_newsletter(
    html_in: str | bytes,
    *,
    source: str,
    sender: str,
    date: datetime | str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    pandoc_path: str | None = None,
    expected_pandoc_sha256: str | None = None,
) -> ConvertedItem:
    """
    Convert one message's already-decoded HTML into clean Markdown.

    ``pandoc_path`` (an absolute path) and ``expected_pandoc_sha256`` pin the binary:
    with a path, no ``PATH`` lookup happens; with a digest, a binary with another
    digest is refused as ``renderer_mismatch`` before it is executed.

    ``source``, ``sender`` and ``date`` are explicit and kept on the result; the
    sender is whatever the caller says it is (see ``DecodedMessage.sender_verified``).
    The HTML is bounded (bytes, elements, nesting) and cleaned first (scripts,
    styles, frames, forms, comments, hidden elements, tracking pixels, tracking
    parameters, redirector wrappers whose target is in the address itself, unsafe
    link schemes, and the footer lines the reviewed data file names), then converted
    by pandoc through stdin and stdout with ``--sandbox``. ``timeout_s`` is ONE
    budget for the cleaning and pandoc together. Nothing is fetched or run; the
    message's text is returned as data. A failure is a typed ``NewsletterRefusalError``.
    """
    source_text = _metadata(source, 2000)
    sender_text = _metadata(sender, 500)
    date_text = _metadata(date, 100)
    deadline = Deadline(timeout_s)
    text, data, warnings = _as_text(html_in, max_bytes)
    noise = _noise.load()
    soup, counts, diagnostics, links, opaque, title = _clean(text, noise, deadline)
    binary = find_pandoc(expected_pandoc_sha256, path=pandoc_path)
    out = run_pandoc(
        binary,
        list(PANDOC_FLAGS),
        str(soup).encode("utf-8"),
        timeout_s=deadline.remaining(),
    )
    if opaque:
        warnings.append(
            f"{opaque} tracked link(s) could not be unwrapped without fetching them"
        )
    return ConvertedItem(
        markdown=_tidy(out.decode("utf-8", "replace")),
        links=tuple(links),
        title=title,
        source=source_text,
        sender=sender_text,
        date=date_text,
        content_sha256=hashlib.sha256(data).hexdigest(),
        stripped=counts,
        diagnostics=diagnostics,
        warnings=tuple(warnings),
        pandoc_version=binary.version,
        noise_data_version=noise.version,
        converter={
            "cleaner": CLEANER_VERSION,
            "pandoc_version": binary.version,
            "pandoc_binary_sha256": binary.sha256,
            "flags": [*PANDOC_FLAGS, "--sandbox", "--data-dir=<empty>"],
            "noise_data_version": noise.version,
            "noise_data_sha256": noise.sha256,
        },
    )


def _paragraphs(text: str) -> str:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    body = "".join(f"<p>{html.escape(p)}</p>" for p in parts)
    return f"<html><body>{body}</body></html>"


def convert_decoded_message(
    decoded: DecodedMessage,
    *,
    source: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    pandoc_path: str | None = None,
    expected_pandoc_sha256: str | None = None,
) -> ConvertedItem:
    """
    Convert a decoded message: its HTML, or its plain text wrapped as paragraphs.

    The sender and date come from the decoded headers; a missing one is replaced by
    ``"unknown"`` and noted in the warnings. The authentication verdict and
    ``sender_verified`` are passed through. A message with neither body is refused.
    """
    if decoded.html is not None and decoded.html.strip():
        body = decoded.html
    elif decoded.text is not None and decoded.text.strip():
        body = _paragraphs(decoded.text)
    else:
        raise refusal("html_empty")
    extra: list[str] = []
    if not decoded.sender:
        extra.append("the message has no From header")
    if not decoded.date:
        extra.append("the message has no Date header")
    item = convert_newsletter(
        body,
        source=source,
        sender=decoded.sender or "unknown",
        date=decoded.date or "unknown",
        max_bytes=max_bytes,
        timeout_s=timeout_s,
        pandoc_path=pandoc_path,
        expected_pandoc_sha256=expected_pandoc_sha256,
    )
    return ConvertedItem(
        markdown=item.markdown,
        links=item.links,
        title=item.title or (decoded.subject[:300] or None),
        source=item.source,
        sender=item.sender,
        date=item.date,
        content_sha256=item.content_sha256,
        stripped=item.stripped,
        diagnostics=item.diagnostics,
        warnings=(*decoded.warnings, *extra, *item.warnings),
        pandoc_version=item.pandoc_version,
        noise_data_version=item.noise_data_version,
        converter=item.converter,
        sender_verified=decoded.sender_verified,
        authentication=dict(decoded.authentication),
    )


def _plain_source_time(value: datetime | str) -> str:
    text = _metadata(value, 100)
    if (
        re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
            r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",
            text,
        )
        is None
    ):
        raise refusal("metadata_invalid")
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        pass
    raise refusal("metadata_invalid")


def convert_plain_text(
    text: str,
    *,
    source: str,
    source_kind: Literal["operator-browser-post"],
    attribution: str,
    source_time: datetime | str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    pandoc_path: str | None = None,
    expected_pandoc_sha256: str | None = None,
) -> ConvertedTextItem:
    """Convert explicit non-email text using the existing bounded Pandoc engine.

    No email headers or authentication are invented. Metadata is an untrusted
    caller claim. Only operator-browser-post is supported; another source kind is
    an additive minor API change. An aware datetime or RFC3339 time with offset
    is normalized to UTC; it does not establish a publication date.

    Literal text is escaped into paragraphs internally, as for the existing
    plaintext path. That is renderer preparation, never an HTML original.
    content_sha256 hashes the exact original UTF8, while render_input_sha256
    identifies the internally prepared document. Both original and prepared
    inputs must fit max_bytes. The same cleaner, warnings, sandbox, binary pin
    and ONE deadline remain in effect. Caller watchdogs remain necessary.
    """
    deadline = Deadline(timeout_s)
    if type(source) is not str or type(attribution) is not str:
        raise refusal("metadata_invalid")
    source_text = _metadata(source, 2000)
    author = _metadata(attribution, 500)
    stamp = _plain_source_time(source_time)
    if type(source_kind) is not str or source_kind != "operator-browser-post":
        raise refusal("metadata_invalid")
    if type(text) is not str:
        raise refusal("text_invalid")
    try:
        data = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        data = None
    if data is None or any(ord(c) < 32 and ord(c) not in (9, 10, 13) for c in text):
        raise refusal("text_invalid")
    if len(data) > max_bytes:
        raise refusal("input_too_large")
    if not text.strip():
        raise refusal("text_empty")
    item = convert_newsletter(
        _paragraphs(text),
        source=source_text,
        sender=author,
        date=stamp,
        max_bytes=max_bytes,
        timeout_s=deadline.remaining(),
        pandoc_path=pandoc_path,
        expected_pandoc_sha256=expected_pandoc_sha256,
    )
    return ConvertedTextItem(
        markdown=item.markdown,
        links=item.links,
        title=item.title,
        source=source_text,
        source_kind="operator-browser-post",
        attribution=author,
        source_time=stamp,
        content_sha256=hashlib.sha256(data).hexdigest(),
        render_input_sha256=item.content_sha256,
        stripped=item.stripped,
        diagnostics=item.diagnostics,
        warnings=item.warnings,
        pandoc_version=item.pandoc_version,
        noise_data_version=item.noise_data_version,
        converter=item.converter,
    )
