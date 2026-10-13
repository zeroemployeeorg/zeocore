"""decode_raw_message: raw RFC 822 bytes to bodies and headers (ZEOCORE-SOW-33, 2A).

Every message here is built in the test from example.com and example.org addresses.
"""

from __future__ import annotations

import email
from email.message import EmailMessage

import pytest

from zeo_core.integrations.pandoc import _mime
from zeo_core.integrations.pandoc.newsletter import (
    MAX_MIME_DEPTH,
    MAX_MIME_PARTS,
    decode_raw_message,
)
from zeo_core.integrations.pandoc.refusals import NewsletterRefusalError

_HTML = "<html><body><p>Café 日本</p></body></html>"
_TEXT = "Café 日本 plain"


def _alternative() -> EmailMessage:
    message = EmailMessage()
    message["From"] = "News <news@example.org>"
    message["To"] = "reader@example.com"
    message["Subject"] = "A weekly note"
    message["Date"] = "Tue, 13 Oct 2026 08:00:00 +0000"
    message["Message-ID"] = "<abc123@example.org>"
    message["List-Unsubscribe"] = "<https://example.org/unsub?u=1>"
    message.set_content(_TEXT)
    message.add_alternative(_HTML, subtype="html")
    return message


def test_multipart_alternative_gives_both_bodies_and_the_headers() -> None:
    decoded = decode_raw_message(bytes(_alternative()))
    assert decoded.html is not None
    assert "Café 日本" in decoded.html
    assert decoded.text is not None
    assert "plain" in decoded.text
    assert decoded.sender == "News <news@example.org>"
    assert decoded.subject == "A weekly note"
    assert decoded.message_id == "<abc123@example.org>"
    assert decoded.list_unsubscribe == "<https://example.org/unsub?u=1>"
    assert decoded.date == "2026-10-13T08:00:00+00:00"
    assert decoded.warnings == ()


@pytest.mark.parametrize("cte", ["quoted-printable", "base64", "8bit"])
def test_transfer_encodings_are_decoded(cte: str) -> None:
    message = EmailMessage()
    message["From"] = "n@example.org"
    message.set_content(_HTML, subtype="html", cte=cte)
    decoded = decode_raw_message(bytes(message))
    assert decoded.html is not None
    assert "Café 日本" in decoded.html


def test_a_latin1_charset_is_decoded() -> None:
    message = EmailMessage()
    message["From"] = "n@example.org"
    message.set_content("<p>Café</p>", subtype="html", charset="iso-8859-1")
    assert b"iso-8859-1" in bytes(message)
    decoded = decode_raw_message(bytes(message))
    assert decoded.html is not None
    assert "Café" in decoded.html


def test_a_message_with_no_html_part_gives_only_text() -> None:
    message = EmailMessage()
    message["From"] = "n@example.org"
    message.set_content("just text")
    decoded = decode_raw_message(bytes(message))
    assert decoded.html is None
    assert decoded.text is not None
    assert "just text" in decoded.text


def test_a_message_with_no_body_warns() -> None:
    raw = b"From: n@example.org\r\nSubject: empty\r\n\r\n"
    decoded = decode_raw_message(raw)
    assert decoded.html is None
    assert decoded.text in (None, "")
    assert decoded.sender == "n@example.org"


def test_an_attachment_is_ignored_and_never_extracted() -> None:
    message = _alternative()
    message.add_attachment(
        b"ATTACHMENT-SENTINEL-BYTES",
        maintype="application",
        subtype="pdf",
        filename="report.pdf",
    )
    decoded = decode_raw_message(bytes(message))
    assert decoded.html is not None
    fields = (decoded.html, decoded.text, decoded.sender, decoded.subject)
    assert not any("ATTACHMENT-SENTINEL" in str(f) for f in fields)
    assert "report.pdf" not in str(decoded)


def test_an_unparseable_date_is_kept_as_written_with_a_warning() -> None:
    raw = b"From: n@example.org\r\nDate: sometime last week\r\n\r\nbody\r\n"
    decoded = decode_raw_message(raw)
    assert decoded.date == "sometime last week"
    assert any("Date" in w for w in decoded.warnings)


def test_a_missing_header_is_an_empty_string() -> None:
    message = EmailMessage()
    message.set_content("x")
    decoded = decode_raw_message(bytes(message))
    assert decoded.sender == ""
    assert decoded.message_id == ""


def test_headers_are_single_line_and_bounded() -> None:
    message = _alternative()
    del message["Subject"]
    message["Subject"] = "S" * 5000
    decoded = decode_raw_message(bytes(message))
    assert len(decoded.subject) <= 1000
    assert "\n" not in decoded.subject


def test_an_empty_message_is_refused() -> None:
    with pytest.raises(NewsletterRefusalError) as caught:
        decode_raw_message(b"")
    assert caught.value.code == "message_empty"


def test_an_oversized_message_is_refused() -> None:
    with pytest.raises(NewsletterRefusalError) as caught:
        decode_raw_message(b"x" * 1000, max_bytes=999)
    assert caught.value.code == "message_too_large"


def test_too_many_parts_are_refused() -> None:
    message = EmailMessage()
    message["From"] = "n@example.org"
    message.set_content("first")
    for index in range(MAX_MIME_PARTS + 1):
        message.add_attachment(
            f"part {index}".encode(), maintype="text", subtype="plain"
        )
    with pytest.raises(NewsletterRefusalError) as caught:
        decode_raw_message(bytes(message))
    assert caught.value.code == "message_too_complex"


def test_deep_nesting_is_refused() -> None:
    inner = "Content-Type: text/plain\r\n\r\nbottom\r\n"
    for level in range(MAX_MIME_DEPTH + 2):
        boundary = f"b{level}"
        inner = (
            f'Content-Type: multipart/mixed; boundary="{boundary}"\r\n\r\n'
            f"--{boundary}\r\n{inner}\r\n--{boundary}--\r\n"
        )
    raw = ("From: n@example.org\r\n" + inner).encode()
    with pytest.raises(NewsletterRefusalError) as caught:
        decode_raw_message(raw)
    assert caught.value.code == "message_too_complex"


def test_an_unknown_charset_does_not_crash() -> None:
    raw = (
        b"From: n@example.org\r\n"
        b"Content-Type: text/html; charset=x-no-such-charset\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n\r\n<p>body</p>\r\n"
    )
    decoded = decode_raw_message(raw)
    assert decoded.sender == "n@example.org"


# --- the sender is a claim (council M2) --------------------------------------

_GMAIL_OK = (
    "Authentication-Results: mx.google.com;\r\n"
    "       dkim=pass header.i=@example.org header.s=s1 header.b=abc;\r\n"
    "       spf=pass (google.com: domain of n@example.org designates 1.2.3.4 as "
    "permitted sender) smtp.mailfrom=n@example.org;\r\n"
    "       dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=example.org\r\n"
)
_GMAIL_FAIL = (
    "Authentication-Results: mx.google.com;\r\n"
    "       dkim=fail header.i=@example.org;\r\n"
    "       spf=softfail smtp.mailfrom=ceo@example.org;\r\n"
    "       dmarc=fail (p=REJECT sp=REJECT dis=NONE) header.from=example.org\r\n"
)
_PLANTED = (
    "Authentication-Results: mx.google.com; dmarc=pass header.from=example.org\r\n"
)


def _raw(top: str, sender: str, *, lower: str = "") -> bytes:
    return (
        f"{top}{lower}From: {sender}\r\nSubject: s\r\n"
        "Content-Type: text/plain\r\n\r\nbody\r\n"
    ).encode()


def test_a_dmarc_pass_aligned_with_from_is_a_verified_sender() -> None:
    decoded = decode_raw_message(_raw(_GMAIL_OK, "News <n@example.org>"))
    assert decoded.authentication["dmarc"] == "pass"
    assert decoded.authentication["dkim"] == "pass"
    assert decoded.authentication["spf"] == "pass"
    assert decoded.authentication["dmarc_header_from"] == "example.org"
    assert decoded.sender_verified is True


def test_a_forged_from_is_not_verified() -> None:
    decoded = decode_raw_message(_raw(_GMAIL_FAIL, "CEO <ceo@example.org>"))
    assert decoded.authentication["dmarc"] == "fail"
    assert decoded.sender_verified is False


def test_a_dmarc_pass_for_another_domain_is_not_verified() -> None:
    decoded = decode_raw_message(_raw(_GMAIL_OK, "Bank <alerts@example.com>"))
    assert decoded.authentication["dmarc"] == "pass"
    assert decoded.sender_verified is False  # the pass was for example.org


def test_a_planted_second_header_cannot_override_gmails() -> None:
    raw = _raw(_GMAIL_FAIL, "CEO <ceo@example.org>", lower=_PLANTED)
    decoded = decode_raw_message(raw)
    assert decoded.authentication["dmarc"] == "fail"
    assert decoded.sender_verified is False


def test_a_planted_header_with_another_authserv_id_is_ignored() -> None:
    top = "Authentication-Results: evil.example; dmarc=pass header.from=example.org\r\n"
    decoded = decode_raw_message(_raw(top, "CEO <ceo@example.org>"))
    assert decoded.authentication == {}
    assert decoded.sender_verified is False


def test_a_planted_header_alone_is_not_gmails_and_no_header_is_unverified() -> None:
    decoded = decode_raw_message(_raw("", "CEO <ceo@example.org>"))
    assert decoded.authentication == {}
    assert decoded.sender_verified is False


# --- every email-module failure is a typed refusal (council L2) ---------------


@pytest.mark.parametrize(
    "error",
    [ValueError("x"), TypeError("x"), RecursionError(), LookupError("x"),
     email.errors.MessageError("x"), AttributeError("x")],
)  # fmt: skip
def test_any_email_module_exception_is_a_typed_refusal(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def broken(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(_mime.email.parser, "BytesFeedParser", broken)
    with pytest.raises(NewsletterRefusalError) as caught:
        decode_raw_message(b"From: n@example.org\r\n\r\nbody")
    assert caught.value.code == "message_too_complex"


def test_oversized_headers_and_too_many_headers_are_refused() -> None:
    big = b"From: n@example.org\r\nX-Pad: " + b"a" * (_mime.MAX_HEADER_BYTES + 10)
    with pytest.raises(NewsletterRefusalError) as huge:
        decode_raw_message(big + b"\r\n\r\nbody")
    assert huge.value.code == "message_too_complex"
    many = b"".join(b"X-%d: v\r\n" % i for i in range(_mime.MAX_HEADERS + 5))
    with pytest.raises(NewsletterRefusalError) as lots:
        decode_raw_message(b"From: n@example.org\r\n" + many + b"\r\nbody")
    assert lots.value.code == "message_too_complex"


def test_list_unsubscribe_is_data_not_an_action() -> None:
    decoded = decode_raw_message(bytes(_alternative()))
    assert isinstance(decoded.list_unsubscribe, str)
    assert "unsub" in decoded.list_unsubscribe  # kept as text for the record only


# --- Authentication-Results is tokenised, never split on text (council M2) ----


def _gmail(results: str) -> str:
    return f"Authentication-Results: mx.google.com; {results}\r\n"


@pytest.mark.parametrize(
    ("results", "sender", "expected_dmarc"),
    [
        # a comment that looks like a result
        (
            "dmarc=fail (dmarc=pass header.from=bank.com) header.from=evil.com",
            "n@bank.com",
            "fail",
        ),
        # the same method twice is ambiguous, whatever the two say
        (
            "dmarc=pass header.from=bank.com; dmarc=pass header.from=bank.com",
            "n@bank.com",
            "ambiguous",
        ),
        (
            "dmarc=pass header.from=bank.com; dmarc=fail header.from=bank.com",
            "n@bank.com",
            "ambiguous",
        ),
        # two header.from values name two domains: none is trusted
        (
            "dmarc=pass header.from=bank.com header.from=evil.com",
            "n@bank.com",
            "pass",
        ),
    ],
)
def test_a_forged_or_ambiguous_dmarc_result_is_never_a_verified_sender(
    results: str, sender: str, expected_dmarc: str
) -> None:
    decoded = decode_raw_message(_raw(_gmail(results), sender))
    assert decoded.authentication.get("dmarc") == expected_dmarc
    assert decoded.sender_verified is False


def test_a_comment_is_not_a_result_and_a_clean_single_pass_still_verifies() -> None:
    commented = decode_raw_message(
        _raw(_gmail("spf=pass (dmarc=pass header.from=example.org)"), "n@example.org")
    )
    assert "dmarc" not in commented.authentication
    assert commented.sender_verified is False
    clean = decode_raw_message(
        _raw(_gmail("dmarc=pass header.from=example.org"), "n@example.org")
    )
    assert clean.sender_verified is True


@pytest.mark.parametrize(
    "results",
    ['dmarc=pass header.from="example.org', "dmarc=pass (never closed header.from=x"],
)
def test_an_unterminated_quote_or_comment_trusts_nothing(results: str) -> None:
    decoded = decode_raw_message(_raw(_gmail(results), "n@example.org"))
    assert decoded.authentication == {}
    assert decoded.sender_verified is False


# --- the sender's own text inside the header is never structure (council N1, N2) --

_FORGED_SPF_COMMENT = (
    # Gmail copies the MAIL FROM into the spf comment; here a local part whose quote
    # and backslash close the comment and open a forged dmarc result before the
    # real dmarc=fail (the failure is written last, where Gmail puts it)
    'spf=pass (google.com: domain of "\\");dmarc=pass header.from=bank.com(\\"" '
    'designates 1.2.3.4 as permitted sender) smtp.mailfrom="\\");dmarc=pass '
    'header.from=bank.com(\\""@evil.com; dmarc=fail header.from=bank.com'
)


@pytest.mark.parametrize(
    "results",
    [
        pytest.param(_FORGED_SPF_COMMENT, id="spf-comment-shape"),
        pytest.param(
            'spf=pass smtp.mailfrom="x;dmarc=pass header.from=bank.com"@evil.com; '
            "dmarc=fail header.from=bank.com",
            id="smtp-mailfrom-quoted-string",
        ),
        pytest.param(
            "spf=pass smtp.mailfrom==?utf-8?q?x=3Bdmarc=3Dpass?=@evil.com; "
            "dmarc=pass header.from=bank.com",
            id="rfc2047-encoded-word",
        ),
        pytest.param(
            "dmarc=pass header.from=bank.com (a backslash \\) in a comment)",
            id="backslash-in-a-comment",
        ),
        pytest.param('dmarc=pass header.from="bank.com"', id="a-quote-at-all"),
    ],
)
def test_quoting_escapes_and_encoded_words_trust_nothing(results: str) -> None:
    decoded = decode_raw_message(_raw(_gmail(results), "News <n@bank.com>"))
    assert decoded.authentication == {}
    assert decoded.sender_verified is False
    assert any("not verified" in w for w in decoded.warnings)


def test_a_header_over_the_length_bound_is_unverified_not_cut() -> None:
    pad = "x" * (_mime.AUTH_MAX_CHARS + 10)
    results = f"spf=pass smtp.mailfrom={pad}@e.com; dmarc=pass header.from=bank.com"
    decoded = decode_raw_message(_raw(_gmail(results), "n@bank.com"))
    assert decoded.authentication == {}
    assert decoded.sender_verified is False


def test_the_raw_value_is_read_not_the_decoded_string() -> None:
    # the decoded string of this header would hold characters Gmail never wrote
    raw = _raw(_gmail("dmarc=pass header.from=bank.com =?utf-8?q?=22?="), "n@bank.com")
    assert decode_raw_message(raw).sender_verified is False


def test_nested_comments_and_escapes_are_dropped_not_structure() -> None:
    nested = _gmail("dmarc=pass (a (b dmarc=fail) c) header.from=example.org")
    decoded = decode_raw_message(_raw(nested, "n@example.org"))
    assert decoded.authentication["dmarc"] == "pass"
    assert decoded.sender_verified is True
    assert _mime._auth_tokens(r"a (b \( c) d") == [("atom", "a"), ("atom", "d")]
    assert _mime._auth_tokens(r"a (b \) c") is None  # the comment never ends


def test_a_dmarc_clause_carries_header_from_and_nothing_else() -> None:
    results = "dmarc=pass header.from=example.org header.d=evil.com"
    decoded = decode_raw_message(_raw(_gmail(results), "n@example.org"))
    assert decoded.authentication["dmarc"] == "ambiguous"
    assert decoded.sender_verified is False
    extra = "dmarc=pass header.from=example.org smtp.mailfrom=x@evil.com"
    other = decode_raw_message(_raw(_gmail(extra), "n@example.org"))
    assert other.sender_verified is False


def test_several_dkim_signatures_are_not_ambiguous_only_a_repeated_dmarc_is() -> None:
    results = (
        "dkim=pass header.i=@a.org; dkim=fail header.i=@b.org; "
        "dmarc=pass header.from=example.org"
    )
    decoded = decode_raw_message(_raw(_gmail(results), "n@example.org"))
    assert decoded.authentication["dkim"] == "pass"
    assert decoded.sender_verified is True
    repeated = _gmail("dmarc=pass header.from=a.org; dmarc=pass header.from=a.org")
    assert decode_raw_message(_raw(repeated, "n@a.org")).sender_verified is False


def test_the_tokeniser_keeps_quoted_text_as_one_token() -> None:
    tokens = _mime._auth_tokens('a="x;y=z" (c;d) b')
    assert tokens == [
        ("atom", "a"),
        ("=", "="),
        ("quoted", "x;y=z"),
        ("atom", "b"),
    ]


# --- the decode is bounded in time before the email package runs (council M3) -


def test_the_message_is_fed_in_pieces_under_a_deadline() -> None:
    body = b"x" * 3_000_000
    raw = b"From: n@example.org\r\nContent-Type: text/plain\r\n\r\n" + body
    with pytest.raises(NewsletterRefusalError) as caught:
        decode_raw_message(raw, timeout_s=0.000001)
    assert caught.value.code == "convert_timeout"
    assert decode_raw_message(raw).text is not None


def test_header_lines_and_parts_are_counted_before_any_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("the email parser must not run")

    monkeypatch.setattr(_mime.email.parser, "BytesFeedParser", never)
    folded = b"From: n@example.org\r\nX-Fold: a\r\n" + b" b\r\n" * (
        4 * _mime.MAX_HEADERS + 1
    )
    with pytest.raises(NewsletterRefusalError) as lines:
        decode_raw_message(folded + b"\r\nbody")
    assert lines.value.code == "message_too_complex"
    parts = b"Content-Type: text/plain\r\n\r\nx\r\n" * (2 * MAX_MIME_PARTS + 1)
    with pytest.raises(NewsletterRefusalError) as many:
        decode_raw_message(b"From: n@example.org\r\n\r\n" + parts)
    assert many.value.code == "message_too_complex"


def test_headerless_parts_are_counted_by_their_boundary_lines() -> None:
    # a part with no headers has no Content-Type, but each still costs a boundary line
    parts = b"--b\r\n\r\nx\r\n" * (2 * MAX_MIME_PARTS + 5)
    raw = (
        b"From: n@example.org\r\n"
        b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n' + parts + b"--b--\r\n"
    )
    with pytest.raises(NewsletterRefusalError) as caught:
        decode_raw_message(raw)
    assert caught.value.code == "message_too_complex"


@pytest.mark.parametrize(
    "parameter",
    [
        b'boundary*0="ab"; boundary*1="cd"',
        b"boundary*=utf-8''abcd",
        b"boundary*0*=utf-8''ab; boundary*1=\"cd\"",
        b'BOUNDARY *0="abcd"',
    ],
    ids=["continuation", "encoded", "encoded-continuation", "case-and-space"],
)
def test_a_boundary_written_as_an_rfc_2231_continuation_is_refused_before_parsing(
    parameter: bytes,
) -> None:
    """email honours ``boundary*0=``; the pre-scan could not count such a boundary."""
    import time

    # ~1.7M header-less parts in under the 10 MiB limit: 6 bytes each
    parts = b"--abcd\n\n" * 1_300_000
    raw = (
        b"From: n@example.org\n"
        b"Content-Type: multipart/mixed; " + parameter + b"\n\n" + parts
    )
    assert len(raw) < _mime.RAW_MAX_BYTES
    started = time.monotonic()
    with pytest.raises(NewsletterRefusalError) as caught:
        decode_raw_message(raw)
    assert caught.value.code == "message_too_complex"
    assert time.monotonic() - started < 2.0  # refused by the pre-scan, not parsed


def test_a_plain_boundary_with_no_star_is_still_counted_not_refused_for_a_star() -> (
    None
):
    parts = b"--b\r\nContent-Type: text/plain\r\n\r\nx\r\n" * 3
    raw = (
        b"From: n@example.org\r\n"
        b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n' + parts + b"--b--\r\n"
    )
    _mime._bound(raw, _mime.RAW_MAX_BYTES)  # no refusal


def test_a_message_with_the_largest_allowed_parts_is_not_refused_by_the_prescan() -> (
    None
):
    parts = b"--b\r\nContent-Type: text/plain\r\n\r\nx\r\n" * MAX_MIME_PARTS
    raw = (
        b"From: n@example.org\r\n"
        b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n' + parts + b"--b--\r\n"
    )
    _mime._bound(
        raw, _mime.RAW_MAX_BYTES
    )  # the pre-scan alone; the walk is tested above


def test_a_flood_of_boundary_declarations_is_refused_before_parsing() -> None:
    raw = b"From: n@example.org\r\n\r\n" + b"boundary=a\n" * (4 * MAX_MIME_PARTS + 2)
    with pytest.raises(NewsletterRefusalError) as caught:
        _mime._bound(raw, _mime.RAW_MAX_BYTES)
    assert caught.value.code == "message_too_complex"
