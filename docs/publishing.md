# Building and publishing the documentation

The site uses MkDocs Material, following Zeocreator's documentation layout and
`gh-pages` publishing model. Markdown guides stay readable on GitHub. The build
also exposes the root quickstart, manual, release notes, examples index and
contract guides without maintaining duplicate copies.

## Preview locally

From a checkout, with uv and Python 3.14 available:

```bash
uv sync --frozen --only-group documentation
uv run --no-sync mkdocs serve
```

Open the local address printed by MkDocs. `uv sync --only-group documentation`
creates a docs-only environment; to retain your development environment, set
`UV_PROJECT_ENVIRONMENT` to a separate virtual-environment directory first.
This group is separate from the `zeocore[docs]` extra, which installs **Google Docs** dependencies.

## Build and check

```bash
uv run --no-sync mkdocs build --strict
```

The build fails on missing documentation targets, invalid navigation or broken
anchors. Search, light/dark mode, code copying and generated Python signatures
are included. Code and non-page assets link to their GitHub source; guides link
to their corresponding site pages. Generated HTML goes in `site/`, never main.

## GitHub Actions and Pages

The **Documentation** workflow builds pull requests and publishes changes to
`main` after a strict build. It can also be dispatched manually on `main`.
Publication commits generated files to **`gh-pages`** using `mkdocs gh-deploy`,
then explicitly requests a Pages build. GitHub does not automatically trigger a
Pages build for pushes made with the workflow token. See the
[GitHub publishing-source documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).
Pull requests and dispatches on other branches build without publishing.

In repository **Settings → Pages → Build and deployment**, select
**Deploy from a branch**, branch **gh-pages**, folder **/ (root)**.
The published site is <https://zeroemployeeorg.github.io/zeocore/>.

For an authorized initial publication before the workflow is on main:

```bash
uv run --no-sync mkdocs gh-deploy --strict
```

This requires Git push access. It updates the generated branch, preserving the
source branch. Do not edit generated HTML or force-push over another publisher.
If Pages reports a deployment failure, inspect the Pages Actions run and repair
the source or repository setting before reporting the site as published.
