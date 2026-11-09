"""
Typed refusals for the newsletter components (ZEOCORE-SOW-33).

A refusal names what was refused and ends with what to do next. Every message
here is a fixed string: nothing in a fetched message or an issue's Markdown is
ever copied into it, so an error can never carry an instruction (or a secret)
from the input. ``code`` is the stable, machine-readable name.
"""

from __future__ import annotations

import re
from typing import Final

from zeo_core.core.errors.integration import ZeoIntegrationError

REFUSALS: Final[dict[str, str]] = {
    # input and limits
    "text_empty": "the text is empty; give a non-empty plain-text source",
    "text_invalid": (
        "the text must be valid Unicode without unsupported control characters; "
        "fix the text and call again"
    ),
    "input_too_large": (
        "the message is larger than max_bytes; skip it, or raise max_bytes"
    ),
    "html_empty": (
        "the message has no HTML to convert; convert its plain-text part instead"
    ),
    "metadata_invalid": (
        "source, sender and date must be short single-line text; "
        "fix them and call again"
    ),
    "message_empty": "the raw message is empty; skip it and fetch it again",
    "message_too_large": (
        "the raw message is larger than max_bytes; skip it, or raise max_bytes"
    ),
    "message_too_complex": (
        "the message has too many MIME parts or nests too deeply; skip it"
    ),
    "html_too_deep": "the HTML nests too deeply to convert; skip this message",
    "convert_timeout": (
        "the conversion did not finish in time; skip this message, or raise timeout_s"
    ),
    "renderer_mismatch": (
        "the renderer is not the one expected; "
        "pin the new renderer tuple, or install the expected pandoc"
    ),
    "markdown_empty": "the Markdown is empty; write the issue and render again",
    "markdown_too_large": (
        "the Markdown is larger than max_bytes; shorten the issue, or raise max_bytes"
    ),
    "markdown_invalid": (
        "the Markdown contains a control character; remove it and render again"
    ),
    # the pandoc binary
    "pandoc_missing": (
        "pandoc is not installed or not on PATH; install pandoc 2.15 or later "
        "(tools/install-pandoc.sh pins the version CI uses)"
    ),
    "pandoc_too_old": "pandoc is older than 2.15; upgrade pandoc and call again",
    "pandoc_timeout": (
        "pandoc did not finish in time; skip this input, or raise timeout_s"
    ),
    "pandoc_failed": (
        "pandoc could not convert this input; skip it and report its id to zeocore"
    ),
    "output_too_large": (
        "the converted output is larger than the limit; skip this input"
    ),
    # packaging and data
    "extra_missing": (
        "beautifulsoup4 is not installed; run: pip install 'zeocore[pandoc]'"
    ),
    "data_invalid": ("zeocore's newsletter data file is invalid; reinstall zeocore"),
    "filter_missing": "zeocore's email filter is missing; reinstall zeocore",
    # the email subset (render_email_html)
    "heading_level_1": (
        "the issue has a level-1 heading, and the subject is the title; "
        "start at level 2 and render again"
    ),
    "heading_level_deep": (
        "the issue has a heading below level 3; use level 2 or 3 and render again"
    ),
    "ordered_list_start": (
        "an ordered list does not start at 1; start it at 1 and render again"
    ),
    "link_text_missing": (
        "a link has no text; give it text, or remove it, and render again"
    ),
    "image_alt_missing": (
        "an image has no alt text; give it alt text and render again"
    ),
    "image_size_missing": (
        "an image has no width and height in image_sizes; "
        "pass its size for that exact source and render again"
    ),
    "image_sizes_malformed": (
        "image_sizes has an unusable entry; use https sources with positive sizes"
    ),
    "image_src_unsupported": (
        "an image_sizes source holds a tab, newline or control character; "
        "use a plain https address"
    ),
    "tag_not_allowed": (
        "the output holds an element outside the email subset; "
        "remove that construct and render again"
    ),
    "attribute_not_allowed": (
        "the output holds an attribute outside the email subset; "
        "remove that construct and render again"
    ),
    "link_scheme": (
        "a link is not https or mailto; use an https address and render again"
    ),
    "link_ip_host": (
        "a link points at a bare IP address; use the site's name and render again"
    ),
    "link_userinfo": (
        "a link holds a user name or password; remove it and render again"
    ),
    "link_mailto_query": (
        "a mailto link holds parameters; use a plain address and render again"
    ),
    "link_mailto_invalid": (
        "a mailto link has no address, or more than one; "
        "use one plain address and render again"
    ),
    "link_invalid": "a link address is unusable; fix it and render again",
    "image_scheme": "an image is not https; use an https source and render again",
    "image_ip_host": (
        "an image comes from a bare IP address; use the site's name and render again"
    ),
    "image_pixel": ("an image is a one-pixel image; remove it, or give it a real size"),
    "image_size_invalid": (
        "an image size is not a whole number from 2 to 10000; fix it and render again"
    ),
    "html_unbalanced": (
        "the output is not well formed; simplify the Markdown and render again"
    ),
    "html_comment": (
        "the output holds a comment or declaration; remove it and render again"
    ),
    "control_character": (
        "the output holds a control character; remove it and render again"
    ),
}

_GENERIC: Final = re.compile(r"^(inline|block)_[a-z]+$")


class NewsletterRefusalError(ZeoIntegrationError):
    """A refusal with a stable ``code`` and a fixed message ending in an action."""

    def __init__(self, code: str) -> None:
        self.code: str = code if known(code) else "pandoc_failed"
        super().__init__(message_for(self.code))


def known(code: str) -> bool:
    """True for a code in the table, or an inline/block construct name."""
    return code in REFUSALS or bool(_GENERIC.match(code))


def message_for(code: str) -> str:
    """The fixed message for a code; never built from input."""
    if code in REFUSALS:
        return REFUSALS[code]
    kind, _, name = code.partition("_")
    return (
        f"the issue holds a {name} ({kind}) element, which the email subset "
        "does not allow; remove it and render again"
    )


def refusal(code: str) -> NewsletterRefusalError:
    """A refusal to raise: ``raise refusal("pandoc_missing")``."""
    return NewsletterRefusalError(code)
