"""Exercise routing failures and the actual built site's link destinations."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

from mkdocs.exceptions import PluginError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "docs_hook", ROOT / "tools/mkdocs_hooks_v1.py"
)
assert spec is not None and spec.loader is not None
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class LinkRoutingTests(unittest.TestCase):
    def test_root_guide_routes_to_site(self) -> None:
        actual = hook.rewrite_target(
            "../QUICKSTART.md#step-1",
            "docs/README.md",
            "README.md",
            {"QUICKSTART.md": "project/QUICKSTART.md"},
        )
        self.assertEqual(actual, "project/QUICKSTART.md#step-1")

    def test_example_code_routes_to_real_source(self) -> None:
        actual = hook.rewrite_target(
            "../../examples/kit_usage.py",
            "docs/tutorials/kit-marketing.md",
            "tutorials/kit-marketing.md",
            {},
        )
        self.assertEqual(
            actual,
            "https://github.com/zeroemployeeorg/zeocore/blob/main/examples/kit_usage.py",
        )

    def test_missing_link_is_a_build_error(self) -> None:
        with self.assertRaises(PluginError):
            hook.rewrite_target("does-not-exist.md", "docs/README.md", "README.md", {})

    def test_escape_is_a_build_error(self) -> None:
        with self.assertRaises(PluginError):
            hook.rewrite_target("../../outside.md", "docs/README.md", "README.md", {})

    def test_code_fences_are_preserved_and_prose_links_resolve(self) -> None:
        markdown = "```python\n[x](missing.py)\n```\n[x](../QUICKSTART.md)\n"
        page = SimpleNamespace(file=SimpleNamespace(src_uri="README.md"))
        actual = hook.on_page_markdown(markdown, page, None, [])
        self.assertEqual(
            actual, "```python\n[x](missing.py)\n```\n[x](project/QUICKSTART.md)\n"
        )

    def test_external_urls_and_fragments_are_preserved(self) -> None:
        for url in (
            "https://example.org/a?q=b#c",
            "#local",
            "mailto:person@example.org",
        ):
            self.assertEqual(
                hook.rewrite_target(url, "docs/README.md", "README.md", {}), url
            )
