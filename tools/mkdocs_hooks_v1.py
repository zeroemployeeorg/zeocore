"""Build repository guides without duplicating their source Markdown.

MkDocs 1.6 File.generated retains the canonical source for edit links. Local
links are resolved from that source, then routed to a site page or GitHub.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page

ROOT = Path(__file__).resolve().parents[1]
GUIDES = (
    "README.md", "QUICKSTART.md", "GET-STARTED.md", "RELEASE_NOTES.md",
    "CHANGELOG.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md",
    "examples/README.md", "src/zeo_core/contracts/README.md",
    "src/zeo_core/contracts/EXAMPLES.md",
)
# Match inline Markdown and reference-link destinations, excluding whitespace.
LINK = re.compile(r"(?P<prefix>\]\(|^ {0,3}\[[^]\n]+\]:[ \t]*)(?P<url>[^\s)]+)", re.MULTILINE)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def source_map(files: Files) -> dict[str, str]:
    """Map repository-relative canonical sources to site-relative documents."""
    mapping = {"docs/" + f.src_uri: f.src_uri for f in files}
    mapping.update({name: "project/" + name for name in GUIDES})
    return mapping


def on_files(files: Files, config: MkDocsConfig) -> Files:
    """Expose maintained root guides as virtual pages with original edit links."""
    for name in GUIDES:
        f = File.generated(config, "project/" + name, abs_src_path=str(ROOT / name))
        f.edit_uri = "../" + name
        files.append(f)
    return files


def rewrite_target(target: str, source: str, destination: str, mapping: dict[str, str]) -> str:
    """Resolve a local destination honestly; nonexistent source targets fail."""
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not parts.path:
        return target
    path = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(parts.path)))
    actual = ROOT / path
    if path.startswith("../") or not actual.exists():
        raise PluginError(f"Missing documentation target in {source}: {target}")
    if actual.is_dir() and (actual / "README.md").is_file():
        path += "/README.md"
    if path in mapping:
        relative = posixpath.relpath(mapping[path], posixpath.dirname(destination) or ".")
        return urlunsplit(("", "", relative, parts.query, parts.fragment))
    kind = "tree" if actual.is_dir() else "blob"
    url = f"https://github.com/zeroemployeeorg/zeocore/{kind}/main/{quote(path)}"
    return urlunsplit((*urlsplit(url)[:3], parts.query, parts.fragment))


def on_page_markdown(markdown: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    """Translate prose links while leaving fenced runnable code untouched."""
    mapping = source_map(files)
    destination = page.file.src_uri
    source = destination.removeprefix("project/") if destination.startswith("project/") else "docs/" + destination
    output: list[str] = []
    fence: str | None = None
    for line in markdown.splitlines(keepends=True):
        marker = FENCE.match(line)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
            output.append(line)
            continue
        if fence is None:
            line = LINK.sub(lambda m: m.group("prefix") + rewrite_target(m.group("url"), source, destination, mapping), line)
        output.append(line)
    return "".join(output)
