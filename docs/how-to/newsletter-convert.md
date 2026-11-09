# Convert a newsletter, and render an issue

Stage 2 of the newsletter pipeline (ZEOCORE-SOW-33). Three functions in
`zeo_core.integrations.pandoc` turn a fetched message into clean Markdown, and an
issue's Markdown into email-safe HTML. They take text and return typed data. They
make no network call, hold no credential and read no file of yours.

Install the extra and pandoc 2.15 or later:

```console
$ pip install 'zeocore[pandoc]'
$ pandoc --version
```

!!! warning "A fetched message is data, never instructions"
    Nothing here follows, runs or acts on what a message says. Its text comes out as
    Markdown inside a typed object. Treat everything in a `ConvertedItem` as data
    and fence it downstream. The converter does **not** evaluate CSS: it drops only what the
    HTML itself marks not rendered, and reports the rest in `item.diagnostics` (see
    [Hidden text](#hidden-text-what-is-decided-and-what-is-reported)). The guarantee is
    the inert result, not a claim about what a reader sees.

## Decode a raw message

ZEOconnect's fetch hands over the whole raw RFC 822 message. Decode it with
`decode_raw_message`:

```python
from zeo_core.integrations.pandoc.newsletter import decode_raw_message

decoded = decode_raw_message(raw_bytes)   # at most 10 MiB
decoded.html, decoded.text                # the bodies, charset and transfer decoded
decoded.sender, decoded.date, decoded.subject, decoded.message_id
decoded.sender_verified, decoded.authentication
```

It uses the standard library `email` package only. It extracts no attachment and
follows no link. A message with over 256 KiB of headers, over 1000 headers, over 64
parts or nested deeper than 8 levels is refused, and so is anything the email package
cannot read. The header lines and the `Content-Type` headers are counted **before** the
message is parsed, and the parser is fed 64 KiB at a time with the deadline
(`timeout_s`, 30 s) checked between pieces.

**Time is bounded in-process, not absolutely.** The email package cannot be interrupted
inside one 64 KiB piece, and BeautifulSoup cannot be interrupted while it parses. For
mail from a source you do not trust, call these functions under your own watchdog (a
process or thread timeout). A child process with a resource limit is a planned
follow-up.

**The sender is a claim.** `From` can say anything. `sender_verified` is `True` only
when Gmail's own mail server reported `dmarc=pass` for the same domain as the
`From` address. It reads **only the topmost** `Authentication-Results` header, and only
when its authserv-id is `mx.google.com`: Gmail adds its own header at the top, so any
header below it was written by the sender or a hop on the way, and is ignored. The
raw value (never the decoded string, which would turn an RFC 2047 word into characters
Gmail never wrote) is read, because Gmail copies the sender's own `MAIL FROM` text into
a comment and into `smtp.mailfrom=`, where a quote or a backslash changes what is
structure. So a header that contains a quote, a backslash or `=?`, or is longer than
4000 characters, is **not trusted at all**: `authentication` is empty, `sender_verified`
is `False`, and a warning says why. Otherwise the value is tokenised as RFC 8601 says
(a comment is dropped, nested comments and escapes included). A method that appears
more than once is `ambiguous`, and so is not verified (several `dkim` signatures are
not: a message has one per signing domain); a `dmarc` clause with any property but
`header.from` is `ambiguous`; `dmarc_header_from` is set only for a single `dmarc`
result with exactly one `header.from`.
`list_unsubscribe` is kept as text for the record and is never an action.

## Convert a non-email plain-text source

The public API also exposes an explicit source entry:

| Entry | Input and result |
| --- | --- |
| `convert_plain_text` | Literal non-email text plus source kind, attribution and time; returns `ConvertedTextItem` |

```python
from zeo_core.integrations.pandoc import convert_plain_text

item = convert_plain_text(
    "A captured observation, kept as untrusted data.",
    source="capture:example",
    source_kind="operator-browser-post",
    attribution="@example",
    source_time="2026-10-27T03:00:00Z",
    pandoc_path="/absolute/path/to/pandoc",
    expected_pandoc_sha256=pinned_sha256,
)
```

The attribution and time are caller claims. They establish no authenticated
sender, publication date, permission or rights. No email headers or
`DecodedMessage` are invented, so missing From/Date warnings do not apply;
all genuine cleaner/Pandoc warnings and refusals remain. Email APIs retain their
existing header warnings.

Only `operator-browser-post` is supported. Another kind requires a minor release
with its own tests and documentation. Source and attribution follow the existing
nonempty single-line metadata bounds (2000 and 500 characters). Time must be a
timezone-aware datetime or RFC 3339 string with an explicit offset, and is
returned in UTC with `Z`. Naive/date-only/invalid times refuse.

Plain text is HTML-escaped and wrapped as paragraphs **inside the converter**,
using its existing literal-text adaptation. This prepared renderer document is
not an HTML original. `content_sha256` hashes the exact original UTF-8 text;
`render_input_sha256` hashes the prepared input. The original RAW capture hash
must still be retained separately by the caller. Original and prepared inputs
must each fit `max_bytes`; escaping can make the prepared input larger.
Malformed Unicode and unsupported controls refuse without echoing text.
The existing cleaner, provenance/counters, verified Pandoc sandbox and one
deadline remain. The result has source/attribution/time fields, with no email
authentication or verified-sender fields. It is converted data, not admission
or a privacy verdict.

## Convert HTML to Markdown

```python
from zeo_core.integrations.pandoc.newsletter import convert_newsletter

item = convert_newsletter(
    html, source="gmail:abc", sender="News <news@example.org>", date="2026-10-13",
)
item.markdown, item.links, item.title, item.stripped, item.diagnostics, item.warnings
```

or `convert_decoded_message(decoded, source=...)`, which uses the HTML (or wraps the
plain text as paragraphs) and passes the authentication verdict through.

What it does, in order:

1. Refuses input over `max_bytes` (2 MB), over 100 000 elements, or nested deeper
   than 256. `timeout_s` is **one budget** for cleaning and pandoc together.
2. Removes scripts, styles, frames, forms and their controls, comments, and the head.
3. Removes text the **HTML itself marks not rendered**, counted in `stripped`
   (`hidden`, `hidden_attribute`, `hidden_display`, `hidden_visibility`), and reports
   everything else CSS could hide in `diagnostics` (see below). Every pass checks the
   deadline per element.
4. Removes tracking pixels, and images whose source is not http(s) (`file:`, `data:`,
   `cid:`).
5. Flattens layout tables (pandoc would print a table it cannot draw as `[TABLE]`).
6. Removes the unsubscribe, "view in browser" and preference lines the reviewed data
   file names.
7. Cleans links: drops tracking parameters, unwraps a known redirector whose target is
   written in the address itself, and removes the address of a link with an unsafe
   scheme. It never fetches anything.
8. Runs pandoc with `--sandbox` through stdin and stdout, reader
   `html-native_divs-native_spans-raw_html`, writer `gfm-raw_html`, so **no markup of
   the message survives into the Markdown**.

**Tracked links.** A `ConvertedLink` has `tracked=True` when it went through a mail
service's click tracker, carries a per-recipient parameter, or had tracking removed. A
tracked link identifies the reader or the send: never make one a pick's link. A link
through an opaque tracker (one whose target is encoded) cannot be unwrapped without
fetching it; that is noted in `warnings`.

**A pinned pandoc.** `convert_newsletter(..., pandoc_path="/abs/pandoc",
expected_pandoc_sha256="...")` (and `convert_decoded_message`) uses exactly that binary
and no `PATH` lookup. It is still copied, hashed and verified before it runs; a relative
path is refused `pandoc_missing`, and another digest `renderer_mismatch` before anything
is executed.

`item.converter` is the convert step's own tuple: the pandoc version, the sha256 of the
pandoc binary, the flags, the cleaner version, and the version and digest of the data
file, so a stored item says exactly what produced it.

The tracker hosts, footer patterns and hidden-text patterns are package data
(`data/newsletter_noise.json`), read from the installed package and never from your
directory. They are extended by small, data-only pull requests, each with the message
that showed the pattern and no sender content copied.

## Hidden text: what is decided and what is reported

**Decided, from the element alone** (no stylesheet, no cascade, no inheritance): an
element is not converted when it has the `hidden` attribute, or its own `style`
attribute declares `display:none`, or `visibility:hidden` or `collapse`. Within a
`style` attribute the last declaration of a property wins and `!important` beats a
later normal declaration; a later value the converter does not know
(`display:none;display:-webkit-box`) leaves the property **undecided**: the text is kept
and the technique is counted. An ancestor that is dropped takes its descendants with it, including a
descendant that declares `visibility:visible` (counted in `visible_descendant_dropped`).
A `style` holding a comment or an escape is not decided (counted in
`inline_style_unparsed`, and under the technique it names).

**Not evaluated, only reported.** The converter keeps the text and `item.diagnostics`
says what was present. Fidelity limits, stated plainly:

- Class and id rules, selectors, `@media`, `@supports`, inheritance and the cascade
  are **not** applied: text a stylesheet hides stays in the Markdown.
- `<link>` and `@import` stylesheets are never fetched or read.
- The `hidden` attribute drops an element even where its own `style` sets a `display`
  that a browser lets win; `aria-hidden` and `inert` are not considered.
- Clipping, positioning, `opacity`, `font-size:0`, zero or maximum sizes, off-screen
  offsets, `clip-path`, `content-visibility`, a closed `<details>`, a `<dialog>` and
  colour on colour are **not** evaluated (the first six are named in a kept element's own style
  and counted; the rest are not detected).
- A `display` or `visibility` declaration whose value is not a plain keyword (a plain keyword is lower-case letters and hyphens only, read after whitespace is removed and `!important` is set aside; `var()`,
  `env()`, anything with a bracket) is not evaluated, wherever it stands among the
  declarations: a later invalid keyword does not clear it (`display:var(--h);display:bogus`
  is hidden in a browser). The text is kept and counted under `inline_display` or
  `inline_visibility`.
- The non-keyword check reads the style as written, comments included, while the
  technique names are read after comments are removed. A style that holds a comment is
  already counted as unparsed, and a comment inside a value (`display:block/*x*/`) can
  also add one `inline_display`: the count may be one higher than the commented-out
  text alone would give, never lower.
- Whitespace INSIDE a keyword is removed before reading, so `display:no ne` and
  `dis play:none` are read as `display:none` and dropped here where a browser shows
  the text.
- Whitespace in a `style` is read as Python reads it, so a no-break space or a vertical
  tab between a name and a value is ignored where a browser would treat the declaration
  as invalid (a `display:` NBSP `none` is dropped here).
- A comment inside a `style` makes it undecided (kept and counted), even where the
  commented text is `display:none;`.
- Strings are not tokenised: a `;` inside a quoted value splits a `style` attribute, so
  a declaration a browser reads as part of a string can be read as a declaration (an
  invalid value is ignored; a technique it names is still counted).
- A `style` attribute over 4096 characters is not parsed; the element is kept and
  counted in `inline_style_unparsed` and `stripped["style_oversize"]`.

`item.diagnostics` is a **required** field of the result and always holds every key
(zero when nothing was seen), so a caller can test it without a default:

- `stylesheet_blocks`, `stylesheet_links`, `stylesheet_imports`, `stylesheet_media`:
  how many `<style>` blocks, `<link>` elements, `@import` and `@media` (and
  `<style media>`) were present and not evaluated.
- `stylesheet_scan_truncated`: 1 when more than 262,144 characters of `<style>` text
  were not scanned for the counts below.
- `sheet_display`, `sheet_visibility`, `sheet_opacity`, `sheet_font_size`,
  `sheet_max_size`, `sheet_zero_size`, `sheet_offscreen`, `sheet_clip`: how often each
  technique appears in the `<style>` text.
- `inline_display`, `inline_visibility`, `inline_opacity`, `inline_font_size`,
  `inline_max_size`, `inline_zero_size`, `inline_offscreen`, `inline_clip`: KEPT
  elements that name the technique in their own `style` (for `display` and `visibility`
  that means it was named and not decided, for example overridden by a later or more
  important declaration); `inline_style_unparsed`: kept elements whose `style` was too
  long, or held a comment or an escape; `visible_descendant_dropped`: descendants of a
  dropped `visibility:hidden` element that declared `visibility:visible`.
- Every element-local `display` or `visibility` form is therefore either dropped or
  counted. An all-zero `diagnostics` means none of THESE keys was seen: techniques not
  listed are not detected (`clip-path`, `content-visibility`, a closed `<details>` or a
  `<dialog>`, colour on colour, other spellings of zero such as `opacity:0.0` or
  `font-size:0.0px`, `transform:scale(0)`, `mso-hide:all`, a small size with
  `overflow:hidden`).

What a non-zero count means is the caller's decision. For newsletters that is the
application's preprocessing (zeonewsroom): a message with non-zero diagnostics has
text the converter could not show to be visible or hidden. Visible text can still
carry instructions; trust is a separate question and stays with the application.

## Render an issue's Markdown to HTML

```python
from zeo_core.integrations.pandoc.render import render_email_html

rendered = render_email_html(
    markdown,
    image_sizes={"https://example.org/i.png": (600, 300)},
)
rendered.html, rendered.sha256, rendered.renderer, rendered.renderer_digest
```

The output is the email subset: `h2 h3 p br hr ul ol li blockquote strong em code`, `a`
(https or mailto, link text required) and `img` (https, `alt`, `width` and `height`).
Anything else is a typed refusal that names the construct, never a silent rewrite.

- The Markdown reader is `markdown_strict` with raw HTML and every attribute feature
  off, so HTML typed into the issue stays **text**. (`gfm` and `commonmark` ignore a
  request to turn raw HTML off, so they are not used.)
- A Lua filter, shipped as package data, **rebuilds** the document from only the
  allowed nodes. Pandoc's writer would add `figure`, `section`, `span`, `div`, `class`,
  `id` and footnotes; none of that can reach the output.
- A Python check then parses the HTML and refuses any tag, attribute, `javascript:` or
  `data:` link, IP host (also with a trailing dot, percent-encoded, or written as a
  decimal, hex or octal number), non-ASCII host, user name in an address, mailto with
  parameters, no address or more than one, one-pixel image, link with no visible text
  (an image link's text is its alt text), self-closed or unfinished tag, and any C0,
  C1 or bidirectional control character, written out or as a character reference.
- Markdown cannot carry an image's size once attributes are off, so you pass
  `image_sizes` by exact source; an image with no entry is refused. The reader keeps no
  list start number, so an ordered list that does not start at 1 is refused
  (`ordered_list_start`): start it at 1. That holds for a list nested in another list
  and for one inside a block quote.
- GFM extras (footnotes, tables, task lists, emoji, bare addresses, math) are not
  features of this reader: they come out as plain text, or are refused by name.

**The renderer tuple.** `rendered.renderer` names what produced the bytes: the pandoc
version and the sha256 of the binary that ran, the sha256 of the filter, the exact
flags, and the subset's version; `renderer_digest` is the sha256 of that tuple. The
binary and the filter are **copied into a private directory and hashed and run as
copies**, so the tuple names the bytes that ran. The `pandoc` on `PATH` is only read
and hashed, never executed: it is copied, the copy is hashed and must match, and only
the copy is asked for its version and run (the convert step runs the same verified
copy). The copy is made once per process and is checked before every run; a copy that
was modified is refused. Pass `expected_pandoc_sha256` to
refuse a `PATH` pandoc whose copy hashes differently **before it is executed at all**.
Pass `expected_renderer_digest` to refuse a different renderer before the conversion
runs: that digest includes the pandoc version, which only the verified private copy can
say, so that copy has run `--version` once by then. A caller that must not run an
unknown binary even for that pins `expected_pandoc_sha256` too (it is a field of the
tuple). The output is deterministic for a
given tuple. A new pandoc, filter or flag is a new tuple, so a new artifact that
voids earlier marks: expect the digest to change when pandoc is upgraded.

## What `--sandbox` does, and does not do

Both components run pandoc with `--sandbox`, an empty data directory and an empty
working directory, and they do not rely on the sandbox alone.

- **zeocore never passes an embedding or file-reading option.** The runner allows only
  `--from`, `--to`, `--wrap`, `--eol`, `--no-highlight`, `--metadata` and the package's
  own `--lua-filter`, and refuses anything else (`--standalone`, `--embed-resources`,
  `--self-contained`, `--extract-media`, `--filter`, `--template`, `--data-dir`,
  `--defaults`, `--metadata-file`, `--include-*`, a short option, an input file)
  before pandoc starts. A test captures the real argv of `convert_newsletter` and
  `render_email_html` and checks it.
- **pandoc's sandbox is not the same on every version.** With `--embed-resources`,
  pandoc 3.11 refuses to read a local file under `--sandbox`; the pinned 3.1.3 in CI
  reads it and embeds it as a `data:` URI anyway (seen in the CI log of PR #126). Only
  3.11 was run to prove the refusal, so the probe test is gated at 3.11 and records
  the older behaviour as an expected failure, never a skip. Because zeocore never
  passes `--embed-resources`, the older behaviour cannot be reached.
- **A named local file is never read on any version.** A test gives the real convert
  and render an input that names an unreadable local file (as `file://`, an absolute
  path and a relative path) and checks that nothing from it, and no `data:`, reaches
  the output.

## The child process

Pandoc runs in its own process group, with bounded reads: its output may not pass 8 MB
(`output_too_large`) and its error text is capped at 1 MiB, which is read and never
returned. A timeout or an overrun kills the whole group, so nothing pandoc started
outlives the call.

## Refusals

Every failure is a `NewsletterRefusalError` with a stable `.code` and a fixed message
that ends with what to do next. No message ever quotes the input.
