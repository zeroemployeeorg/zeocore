# The shared release-on-merge component

Every merge to a golden `main` is a release of exactly that commit (RULING-471
amendment 1, section 3a). This note says which files are the executor, what a
repository passes to it, how a repository uses it unchanged, and how a runner
outside GitHub Actions runs it.

## Reserved paths (the executor's definition)

A change to any of these is a change to the executor, which the operator may
veto (section 3c). A review must name it as such.

- `.github/workflows/release-on-merge.yml` (the reusable workflow)
- `.github/workflows/publish.yml` (zeocore's own caller)
- `.github/release/plan.py` (the version computation)
- `.github/release/event.py` (the release event)
- `.github/release/install_with_retry.sh` (the smoke test's index-lag retry)
- `release.yaml` (a repository's release file: facts, no version, no commit)

`tests/test_release_metadata.py` fails if one of these is missing or not listed
here, and if the inline copy of `plan.py` in the workflow differs from the file.

## What the component takes

`project`, `verify`, `build`, `tag_prefix` (default `v`; the engine uses
`engine/v`) and `index` (default PyPI's JSON API; `none` for a repository with
no package; a repository that publishes a package must never pass `none`, it
switches off the index guards). `tag_prefix` is `v` or `<name>/v` with a
lower-case name, and anything else fails the run. It takes no version, commit or ref, and its only trigger is
`workflow_call`. A caller's only trigger is a push to `main`; no release, tag or
upload can be started by hand. The version is bare semver, computed from the
last tag in the namespace, the package index (unless `none`) and the
`changelog.d` fragments added since (`bump: patch|minor|major` on the first
line; none means patch). The tag is `tag_prefix` plus the version, so
`engine/v` gives `engine/v1.3.0`.

## Using it unchanged

A repository's own `publish.yml` calls the workflow at a pinned commit of this
repository, on every push to `main`:

```yaml
jobs:
  release:
    uses: zeroemployeeorg/zeocore/.github/workflows/release-on-merge.yml@<40-hex sha>
    permissions: {contents: write}
    with: {project: <distribution name>, verify: <shell>}
```

Its upload job runs in the caller, in an environment named `pypi` that deploys
from `main` only, with PyPI trusted publishing. PyPI matches the top-level
workflow, so the trusted publisher names the caller (`publish.yml`), not the
reusable file: the component's "shared workflow" is met by the caller calling
it unchanged. No token and no signing key is stored as an Actions secret.

## Running it outside Actions (private gold, on Pomar)

From a fresh checkout of the merged commit, at the repository root, with every
tag fetched:

```sh
TAG_PREFIX=engine/v INDEX=none python3 plan.py notes.md
```

It prints `version=`, `floor=` (empty without a `pyproject.toml`) and `tag=`
lines, writes the release notes to `notes.md`, and creates no tag. Run on a
commit that already carries a tag in the namespace it returns that version (a
re-run). A computed tag that exists at another commit, or a `changelog.d`
fragment changed or deleted since the last tag, fails the run.

## The release event

`event.py TAG NOTES [ARTIFACT_DIR] [--repository NAME]` prints one JSON document, schema
`zeo.release-event/v1`: `repository`, `tag`, `tag_object`, `commit`, `tree`,
`notes_sha256`, `artifacts` (each `path`, `size`, `sha256`) and `event_sha256`.

**Canonical bytes.** The payload is every field except `event_sha256`, as JSON
with sorted keys and no spaces. `event_sha256` is the sha256 of those bytes; it
is the one excluded field, so no field hashes itself. With `--detached DIR` the
script also writes `DIR/release-event.payload.json` (exactly the canonical
bytes) and `DIR/release-event.sha256` (the hex digest and a newline), so a
signer signs the payload file and a verifier recomputes the digest from it.

**The repository name** comes from `--repository`, else `GITHUB_REPOSITORY`,
else origin's URL; any value that carries userinfo (`https://user:token@...`)
is refused, so no credential can reach a published asset.

**What it does not do.** It signs nothing and trusts no caller-supplied flag. A
consumer checks the signature of the artifacts it names (PyPI attestations for
a package, Pomar's signed build for a runnable), recomputes `event_sha256`, and
refuses an event whose commit is not on `main` according to the gold
repository's own record, whose artifact digests do not match the bytes it
received, or whose signing receipts do not bind this payload. The verifier, its
trust profile and key identity belong to the signer, not to this directory.
