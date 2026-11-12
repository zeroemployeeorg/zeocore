"""
Decoding one raw RFC 822 message (ZEOCORE-SOW-33, section 2A).

Standard-library ``email`` only: charset and transfer decoding. No attachment is
extracted, no link is followed, nothing is fetched. The message is bounded before
and while it is read (bytes, header bytes, number of headers, parts and nesting),
and every exception the email package can raise on a hostile message becomes a
typed refusal, so a caller sees ``NewsletterRefusalError`` or a result, never a
traceback.

The sender is a CLAIM. ``From`` can say anything, so ``DecodedMessage`` also carries
what Gmail's own mail server concluded: the TOPMOST ``Authentication-Results`` header
only, and only when it names ``mx.google.com`` as its authserv-id. Gmail prepends its
own header, so every header below it was written by the sender or a hop on the way,
and is ignored. ``sender_verified`` is True only for ``dmarc=pass`` where the domain
DMARC checked equals the domain in ``From``.

Gmail echoes sender-controlled text (the MAIL FROM address) inside that header, in a
comment and in ``smtp.mailfrom=``, where a quote or a backslash changes what is
structure. So the RAW header is read (never the decoded string, which would turn an
RFC 2047 word into characters Gmail never wrote), and a value that contains a quote, a
backslash or ``=?``, or is longer than ``AUTH_MAX_CHARS``, is not trusted at all:
``authentication`` is empty and ``sender_verified`` is False. The one ``dmarc``
clause may carry ``header.from`` and nothing else.
"""

from __future__ import annotations

import email
import email.parser
import email.policy
import email.utils
import re
from dataclasses import dataclass, field
from typing import Any, Final

from zeo_core.integrations.pandoc._runner import Deadline
from zeo_core.integrations.pandoc.refusals import refusal

RAW_MAX_BYTES: Final = 10_485_760
MAX_HEADER_BYTES: Final = 262_144
MAX_HEADERS: Final = 1_000
MAX_MIME_PARTS: Final = 64
MAX_MIME_DEPTH: Final = 8
DEFAULT_TIMEOUT_S: Final = 30.0

#: The message is handed to the parser in pieces of this size, so the deadline is
#: checked between them; the parser itself cannot be interrupted inside one piece.
_FEED_CHUNK: Final = 65_536
#: A cheap pre-scan bound on the number of parts: no message with at most
#: ``MAX_MIME_PARTS`` parts has more than twice that many ``Content-Type`` headers.
_PART_HEADER: Final = re.compile(rb"^content-type:", re.IGNORECASE | re.MULTILINE)
#: A declared multipart boundary. Parts with no headers at all have no Content-Type,
#: but each still costs a boundary line, so the lines are counted too.
_BOUNDARY: Final = re.compile(
    rb"boundary\s*=\s*(?:\"((?:[^\"\\\r\n]|\\.)*)\"|([^\s;\"]+))",
    re.IGNORECASE,
)
_ESCAPE: Final = re.compile(rb"\\(.)")
#: An RFC 2231 parameter continuation or encoding of the boundary (``boundary*0=``,
#: ``boundary*=utf-8''...``). The ``email`` package honours it, but the pattern above
#: cannot see through it, so the boundary lines could not be counted: refused.
#: It is looked for anywhere in a ``Content-Type`` field of a real header section (the
#: line and its folded lines), so a comment before it cannot hide it; body prose is
#: never a header section (see ``_in_header_section``).
_BOUNDARY_EXTENDED: Final = re.compile(
    rb"boundary\s*\*\s*(?:\d+\s*\*?\s*)?=", re.IGNORECASE
)
#: A boundary the scan could read differently from the parser (parentheses, angle
#: brackets, a backslash, an encoded word) is refused as well, and so is an empty one.
_UNSAFE_BOUNDARY: Final = re.compile(rb"[()<>\\]|=\?")
_LINE_END: Final = re.compile(rb"\r\n|\r")
_CONTENT_TYPE_FIELD: Final = re.compile(
    rb"^content-type[ \t]*:[^\r\n]*(?:\r?\n[ \t][^\r\n]*)*",
    re.IGNORECASE | re.MULTILINE,
)
#: A ``Content-Type`` field of a multipart type: only such a field declares a boundary
#: that delimits parts (a ``boundary`` parameter on ``text/plain`` delimits nothing).
_MULTIPART_FIELD: Final = re.compile(
    rb"content-type[ \t]*:\s*multipart/", re.IGNORECASE
)
#: A ``Content-Type`` field of an embedded message: the part's body is a message whose
#: own header block follows the part's blank line.
_EMBEDDED_FIELD: Final = re.compile(rb"content-type[ \t]*:\s*message/", re.IGNORECASE)
#: What may follow ``--<boundary>`` on a boundary line: an optional closing ``--``, then
#: spaces or tabs, then the line end. Anything else is a longer word, not a delimiter.
_DELIMITER_END: Final = re.compile(rb"(--)?[ \t]*(?:\n|\Z)")
#: How many lines that only look like a boundary line are examined before refusing.
_LOOKALIKES: Final = 64
_MAX_BOUNDARIES: Final = 2 * MAX_MIME_DEPTH + 8
_MAX_BOUNDARY_MENTIONS: Final = 4 * MAX_MIME_PARTS

#: The longest Authentication-Results value that is believed (not cut: refused).
AUTH_MAX_CHARS: Final = 4000

#: The only authserv-id whose Authentication-Results is believed.
TRUSTED_AUTHSERV_ID: Final = "mx.google.com"

_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
#: Text that can carry a forged structure: Gmail copies sender-controlled characters
#: into this header, and these are the ones that change how it parses.
_UNTRUSTED_MARKS: Final = ('"', "\\", "=?")
_METHODS: Final = ("dkim", "spf", "dmarc")
_AUTH_STOP: Final = frozenset(' \t\r\n;="()/')


@dataclass(frozen=True, slots=True)
class DecodedMessage:
    """
    The bodies and a few headers of one decoded RFC 822 message.

    ``list_unsubscribe`` is data and never an instruction: nothing may act on it.
    ``authentication`` and ``sender_verified`` say what Gmail concluded about the
    sender; ``sender`` itself is only what the message claims.
    """

    html: str | None
    text: str | None
    sender: str
    date: str
    subject: str
    message_id: str
    list_unsubscribe: str
    warnings: tuple[str, ...] = field(default=())
    authentication: dict[str, str] = field(default_factory=dict)
    sender_verified: bool = False


def _header(message: Any, name: str) -> str:  # noqa: ANN401 -- an email Message
    try:
        value = str(message.get(name, "") or "")
    except (ValueError, LookupError, TypeError, AttributeError):
        return ""
    return _CONTROL.sub(" ", value).strip()[:1000]


def _walk_limits(message: Any, depth: int, seen: list[int]) -> None:  # noqa: ANN401
    seen[0] += 1
    if seen[0] > MAX_MIME_PARTS or depth > MAX_MIME_DEPTH:
        raise refusal("message_too_complex")
    if message.is_multipart():
        for part in message.get_payload():
            _walk_limits(part, depth + 1, seen)


def _body(message: Any, kind: str, warnings: list[str]) -> str | None:  # noqa: ANN401
    try:
        part = message.get_body(preferencelist=(kind,))
        if part is None:
            return None
        content = part.get_content()
    except (LookupError, ValueError, UnicodeError, KeyError, AttributeError):
        warnings.append(f"the {kind} body could not be decoded and was skipped")
        return None
    return content if isinstance(content, str) else None


def _date_text(raw: str, warnings: list[str]) -> str:
    if not raw:
        return ""
    try:
        return email.utils.parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError, IndexError):
        warnings.append("the Date header is not a valid date and was kept as written")
        return raw


def _skip_comment(value: str, start: int) -> int | None:
    """The index after the comment opening at ``start``; ``None`` if unfinished."""
    depth, i, n = 1, start + 1, len(value)
    while i < n and depth:
        if value[i] == "\\":
            i += 1
        elif value[i] == "(":
            depth += 1
        elif value[i] == ")":
            depth -= 1
        i += 1
    return None if depth else i


def _read_quoted(value: str, start: int) -> tuple[str, int] | None:
    """The text of the quoted string at ``start`` and the index after it."""
    j, n, text = start + 1, len(value), []
    while j < n and value[j] != '"':
        if value[j] == "\\" and j + 1 < n:
            j += 1
        text.append(value[j])
        j += 1
    return None if j >= n else ("".join(text), j + 1)


def _auth_tokens(value: str) -> list[tuple[str, str]] | None:
    """
    Tokenise an Authentication-Results value per RFC 8601, or ``None`` if malformed.

    Comments (nestable, with backslash escapes) are dropped. A quoted string is ONE
    token of kind ``quoted``, so a ``;`` or ``=`` inside it is text and can never
    start a result. ``;``, ``=`` and ``/`` are tokens of their own kind; anything
    else up to a separator is an ``atom``.
    """
    out: list[tuple[str, str]] = []
    i, n = 0, len(value)
    while i < n:
        c = value[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(":
            skipped = _skip_comment(value, i)
            if skipped is None:
                return None  # a comment that never ends
            i = skipped
        elif c == '"':
            quoted = _read_quoted(value, i)
            if quoted is None:
                return None  # a quoted string that never ends
            out.append(("quoted", quoted[0]))
            i = quoted[1]
        elif c in ";=/":
            out.append((c, c))
            i += 1
        else:
            j = i
            while j < n and value[j] not in _AUTH_STOP:
                j += 1
            if j == i:
                return None  # a stray ")"
            out.append(("atom", value[i:j]))
            i = j
    return out


def _auth_clause(
    clause: list[tuple[str, str]],
) -> tuple[str, str, list[str], bool] | None:
    """
    One ``method=result`` clause: method, result, ``header.from`` values, and whether
    it carries nothing but ``header.from`` properties (the only kind ``dmarc`` may).
    """
    if not clause or clause[0][0] != "atom":
        return None
    method, i = clause[0][1].lower(), 1
    if i + 1 < len(clause) and clause[i][0] == "/":
        i += 2  # the method's version
    if i + 1 >= len(clause) or clause[i][0] != "=" or clause[i + 1][0] != "atom":
        return None
    result, i = clause[i + 1][1].lower(), i + 2
    header_from: list[str] = []
    only_header_from = True
    while i + 3 <= len(clause):
        key, eq, value = clause[i], clause[i + 1], clause[i + 2]
        if key[0] != "atom" or eq[0] != "=" or value[0] not in ("atom", "quoted"):
            break
        if key[1].lower() == "header.from":
            header_from.append(value[1].lower().rstrip("."))
        else:
            only_header_from = False
        i += 3
    return method, result, header_from, only_header_from and i == len(clause)


def _clauses(tokens: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """The tokens split at each ``;``."""
    clauses: list[list[tuple[str, str]]] = [[]]
    for token in tokens:
        if token[0] == ";":
            clauses.append([])
        else:
            clauses[-1].append(token)
    return clauses


def _is_gmail_head(head: list[tuple[str, str]]) -> bool:
    """True when the first clause is exactly ``mx.google.com`` and a version, if any."""
    if not head or head[0][0] != "atom" or head[0][1].lower() != TRUSTED_AUTHSERV_ID:
        return False
    if len(head) == 1:
        return True
    return len(head) == 2 and head[1][0] == "atom" and head[1][1].isdigit()


def _combine(values: list[str], method: str) -> str:
    """The one result a method reports when it appears ``len(values)`` times."""
    if len(values) == 1:
        return values[0]
    if method == "dkim":  # a message carries one signature per signing domain
        return "pass" if "pass" in values else values[0]
    return "ambiguous"  # a repeated dmarc or spf is never believed


def parse_authentication(value: str) -> dict[str, str]:
    """
    The dkim, spf and dmarc results in ONE Authentication-Results value, or ``{}``.

    Empty unless the value's authserv-id is exactly ``mx.google.com``. The value is
    tokenised per RFC 8601 (quoted strings and comments are never structure). A
    method that appears more than once is ``ambiguous`` (``dkim`` is the exception: a
    message has one signature per signing domain), a ``dmarc`` clause with any
    property but ``header.from`` is ``ambiguous``, and ``dmarc_header_from`` is set
    only for a single ``dmarc`` result that names exactly one ``header.from``.
    """
    tokens = _auth_tokens(value)
    if tokens is None:
        return {}
    clauses = _clauses(tokens)
    if not _is_gmail_head(clauses[0]):
        return {}
    results: dict[str, list[str]] = {}
    dmarc_from: list[str] = []
    dmarc_clean = True
    for clause in clauses[1:]:
        parsed = _auth_clause(clause)
        if parsed is None or parsed[0] not in _METHODS:
            continue
        results.setdefault(parsed[0], []).append(parsed[1])
        if parsed[0] == "dmarc":
            dmarc_from.extend(parsed[2])
            dmarc_clean = dmarc_clean and parsed[3]
    found = {"authserv_id": TRUSTED_AUTHSERV_ID}
    found.update({m: _combine(v, m) for m, v in results.items()})
    if "dmarc" in found and not dmarc_clean:
        found["dmarc"] = "ambiguous"  # a dmarc clause carries header.from and no more
    if found.get("dmarc") != "ambiguous" and len(dmarc_from) == 1:
        found["dmarc_header_from"] = dmarc_from[0]
    return found


def _top_authentication(message: Any) -> str:  # noqa: ANN401 -- an email Message
    """The RAW value of the first (topmost) Authentication-Results header, or ``""``."""
    try:
        for name, value in message.raw_items():
            if str(name).lower() == "authentication-results":
                return _CONTROL.sub(" ", str(value))
    except (ValueError, LookupError, TypeError, AttributeError):
        return ""
    return ""


def _authentication(
    message: Any,  # noqa: ANN401
    sender: str,
    warnings: list[str],
) -> tuple[dict[str, str], bool]:
    """Gmail's verdict on the sender, from the topmost Authentication-Results only."""
    top = _top_authentication(message)
    if not top:
        return {}, False
    if len(top) > AUTH_MAX_CHARS or any(mark in top for mark in _UNTRUSTED_MARKS):
        warnings.append(
            "the Authentication-Results header has quoting, an encoded word or is "
            "too long, so the sender was not verified"
        )
        return {}, False
    result = parse_authentication(top)
    if not result:
        return {}, False
    domain = email.utils.parseaddr(sender)[1].rpartition("@")[2].lower().rstrip(".")
    verified = (
        result.get("dmarc") == "pass"
        and bool(domain)
        and result.get("dmarc_header_from") == domain
    )
    return result, verified


def _bound(raw: bytes, max_bytes: int) -> None:
    """
    Refuse an oversized message before the email package sees a byte of it.

    The ``email`` package ends a line at CR LF, LF or a bare CR, so every count below
    reads the message with every line ending turned into LF: a bare CR cannot hide a
    header line, a blank line or a boundary line from it.
    """
    if not raw:
        raise refusal("message_empty")
    if len(raw) > max_bytes:
        raise refusal("message_too_large")
    raw = _LINE_END.sub(b"\n", raw)
    split = raw.find(b"\n\n")
    if split < 0:
        split = len(raw)
    if split > MAX_HEADER_BYTES or raw.count(b"\n", 0, split) > 4 * MAX_HEADERS:
        raise refusal("message_too_complex")
    for count, _ in enumerate(_PART_HEADER.finditer(raw), 1):
        if count > 2 * MAX_MIME_PARTS:
            raise refusal("message_too_complex")
    _bound_boundary_lines(raw)


def _boundary_line_before(
    raw: bytes, low: int, position: int, boundary: bytes
) -> tuple[int, bool]:
    """
    Where the last line before ``position`` that is exactly a boundary line starts.

    A boundary line is ``--<boundary>`` or ``--<boundary>--``, then only spaces or tabs
    up to the line end. A line that merely starts with the same characters (a longer
    boundary, prose) is not one. Returns the index of the LF before the line (0 at
    the top of the message) and whether it is the closing line (``--<boundary>--``),
    or ``(-1, False)`` when there is none from ``low`` on. More than ``_LOOKALIKES``
    lookalike lines are refused as too complex.
    """
    needle = b"\n--" + boundary
    end = position
    for _ in range(_LOOKALIKES):
        found = raw.rfind(needle, low, end)
        if found < 0:
            break
        delimiter = _DELIMITER_END.match(raw, found + len(needle))
        if delimiter:
            return found, delimiter.group(1) is not None
        end = found
    else:
        raise refusal("message_too_complex")
    top = b"--" + boundary
    if low == 0 and raw.startswith(top):
        delimiter = _DELIMITER_END.match(raw, len(top))
        if delimiter:
            return 0, delimiter.group(1) is not None
    return -1, False


def _delimiter_lines(raw: bytes, boundary: bytes, limit: int) -> int:
    """
    How many lines are exactly a delimiter of ``boundary``, counting up to ``limit``.

    A longer word that starts like the boundary and prose are not delimiters, so they
    cost nothing. The count stops one past ``limit``: the caller refuses either way.
    """
    needle = b"\n--" + boundary
    count = 0
    candidates = 0
    at = raw.find(needle)
    while at >= 0:
        candidates += 1
        if candidates > 2 * limit + _LOOKALIKES:
            raise refusal("message_too_complex")
        if _DELIMITER_END.match(raw, at + len(needle)):
            count += 1
            if count > limit:
                break
        at = raw.find(needle, at + 1)
    return count


def _in_header_section(
    raw: bytes, position: int, declared: set[bytes], embedded: list[int]
) -> bool:
    """
    Whether ``position`` lies in a header section, not in a part or message body.

    A header section starts at the top of the message, on the line after an opening
    boundary line of a boundary ``declared`` in an earlier header section, or where an
    embedded message's own header block starts (``embedded``), and ends at the first
    blank line. So the field is in a header section when no blank line lies between the
    last such start and the field. What follows a closing boundary line is an epilogue,
    not a header section. A body line that merely looks like a header, a ``--``
    signature line and a boundary only mentioned in prose are not one either. A field
    farther than ``MAX_HEADER_BYTES`` from both is taken to be in one: refused.
    """
    low = max(0, position - MAX_HEADER_BYTES)
    blank = raw.rfind(b"\n\n", low, position)
    delimiter, closing = -1, False
    for boundary in declared:
        found, is_closing = _boundary_line_before(raw, low, position, boundary)
        if found > delimiter or (found == delimiter and not is_closing):
            delimiter, closing = found, is_closing
    if embedded and embedded[-1] <= position:
        start = embedded[-1]
        in_block = start >= low and raw.rfind(b"\n\n", start, position) < 0
        if in_block and start > delimiter:
            return True
    if delimiter < 0 and blank < 0:
        return True
    if delimiter < 0 or closing:
        return False
    return blank < delimiter


def _boundary_value(found: re.Match[bytes]) -> bytes:
    """
    The boundary as the ``email`` package reads it: unquoted, unescaped, right-stripped.

    ``get_boundary`` right-strips the value, so ``boundary="b "`` delimits on ``--b``.
    """
    quoted = found.group(1)
    value = _ESCAPE.sub(rb"\1", quoted) if quoted is not None else found.group(2)
    return value.rstrip()


def _declared_boundaries(field: bytes) -> set[bytes]:
    """
    The boundaries a multipart ``Content-Type`` field declares, or a refusal.

    The parser unquotes twice, strips angle brackets, reads comments and decodes encoded
    words, so a value with any of them (or an empty one) could delimit on something the
    line scan does not count: refused rather than guessed.
    """
    result: set[bytes] = set()
    for found in _BOUNDARY.finditer(field):
        value = _boundary_value(found)
        if not value or _UNSAFE_BOUNDARY.search(value):
            raise refusal("message_too_complex")
        result.add(value)
    return result


def _mentioned_boundaries(raw: bytes) -> set[bytes]:
    """Every boundary the message mentions anywhere, refusing a flood of them."""
    boundaries: set[bytes] = set()
    for mentions, found in enumerate(_BOUNDARY.finditer(raw), 1):
        if mentions > _MAX_BOUNDARY_MENTIONS:
            raise refusal("message_too_complex")
        boundary = _boundary_value(found)
        if boundary:
            boundaries.add(boundary)
    if len(boundaries) > _MAX_BOUNDARIES:
        raise refusal("message_too_complex")
    return boundaries


def _refuse_extended_boundaries(raw: bytes) -> None:
    """Refuse an RFC 2231 boundary written in a real header section."""
    declared: set[bytes] = set()
    embedded: list[int] = []
    for fields, found in enumerate(_CONTENT_TYPE_FIELD.finditer(raw), 1):
        if fields > 2 * MAX_MIME_PARTS or len(declared) > _MAX_BOUNDARIES:
            raise refusal("message_too_complex")
        if not _in_header_section(raw, found.start(), declared, embedded):
            continue
        if _BOUNDARY_EXTENDED.search(found.group()):
            raise refusal("message_too_complex")
        if _MULTIPART_FIELD.match(found.group()):
            declared.update(_declared_boundaries(found.group()))
        elif _EMBEDDED_FIELD.match(found.group()):
            end = raw.find(b"\n\n", found.end())
            if end >= 0:
                embedded.append(end + 2)


def _bound_boundary_lines(raw: bytes) -> None:
    """
    Refuse a message whose declared boundaries are delimited too many times.

    A part with no headers has no ``Content-Type`` to count, but it still costs one
    boundary line, so the lines that are exactly a boundary line are counted before
    parsing. A boundary written as an RFC 2231 continuation (``boundary*0=``) cannot be
    seen by that count, so such a message is refused. ``raw`` has every line ending
    turned into LF already (see ``_bound``).
    """
    boundaries = _mentioned_boundaries(raw)
    _refuse_extended_boundaries(raw)
    limit = 2 * MAX_MIME_PARTS + len(boundaries)
    lines = 0
    for boundary in boundaries:
        lines += _delimiter_lines(raw, boundary, limit)
        if lines > limit:
            raise refusal("message_too_complex")


def decode_raw_message(
    raw: bytes,
    *,
    max_bytes: int = RAW_MAX_BYTES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> DecodedMessage:
    """
    Decode one raw RFC 822 message into its bodies and headers.

    Uses the standard-library ``email`` package with ``policy.default`` and
    ``get_body``: charset and transfer decoding only. No attachment is extracted, no
    link is followed, nothing is fetched. A message over ``max_bytes``, with over
    256 KiB of headers or 1000 headers, with more than 64 parts, or nested deeper
    than 8 levels, is refused, and so is anything the email package cannot read.

    ``timeout_s`` is checked between the 64 KiB pieces handed to the parser and
    between the steps after it. The parser cannot be interrupted inside one piece, so
    a caller reading hostile mail should still run this under its own watchdog (a
    process or thread timeout); a child process with a resource limit is planned.
    """
    deadline = Deadline(timeout_s)
    _bound(raw, max_bytes)
    try:
        return _decode(raw, deadline)
    except Exception as error:  # noqa: BLE001 -- every email-package failure refuses
        if getattr(error, "code", None) is not None:
            raise
        raise refusal("message_too_complex") from error


def _parse(raw: bytes, deadline: Deadline) -> Any:  # noqa: ANN401 -- an email Message
    parser = email.parser.BytesFeedParser(policy=email.policy.default)
    for start in range(0, len(raw), _FEED_CHUNK):
        parser.feed(raw[start : start + _FEED_CHUNK])
        deadline.check()
    return parser.close()


def _decode(raw: bytes, deadline: Deadline) -> DecodedMessage:
    message = _parse(raw, deadline)
    if len(message.keys()) > MAX_HEADERS:
        raise refusal("message_too_complex")
    _walk_limits(message, 1, [0])
    deadline.check()
    warnings: list[str] = []
    html_body = _body(message, "html", warnings)
    deadline.check()
    text_body = _body(message, "plain", warnings)
    deadline.check()
    if html_body is None and text_body is None:
        warnings.append("the message has no text or HTML body")
    sender = _header(message, "From")
    authentication, verified = _authentication(message, sender, warnings)
    return DecodedMessage(
        html=html_body,
        text=text_body,
        sender=sender,
        date=_date_text(_header(message, "Date"), warnings),
        subject=_header(message, "Subject"),
        message_id=_header(message, "Message-ID"),
        list_unsubscribe=_header(message, "List-Unsubscribe"),
        warnings=tuple(warnings),
        authentication=authentication,
        sender_verified=verified,
    )
