"""Release on merge: every merge to main is a release with a computed version.

The workflow's shape is checked from its YAML. The version script,
.github/release/plan.py (a copy of it runs inline in release-on-merge.yml), is
run against throwaway git repositories, with real merges, tags and
changelog.d fragments.
"""

import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict[Any, Any]:
    document: dict[Any, Any] = yaml.safe_load((WORKFLOWS / name).read_text())
    return document


def test_release_announcement_waits_for_installed_package_smoke() -> None:
    """A successful upload alone must never announce a verified release."""
    release = _workflow("publish.yml")["jobs"]["github-release"]
    assert {"publish-pypi", "smoke-test"} <= set(release["needs"])
    assert "needs.smoke-test.result == 'success'" in release["if"]
    assert "needs.publish-pypi.result == 'success'" in release["if"]


def test_a_release_runs_on_every_push_to_main_and_on_nothing_else() -> None:
    document = _workflow("publish.yml")
    # PyYAML reads the bare `on:` key as the boolean True.
    assert document[True] == {"push": {"branches": ["main"]}}


def test_every_publish_waits_on_the_main_only_pypi_environment() -> None:
    jobs = _workflow("publish.yml")["jobs"]
    environments = {
        job["environment"]["name"] for job in jobs.values() if "environment" in job
    }
    assert environments == {"pypi"}
    assert jobs["publish-pypi"]["needs"] == "release"


def test_the_tag_is_made_after_the_build_and_before_any_publish() -> None:
    caller = _workflow("publish.yml")["jobs"]["release"]
    assert caller["uses"] == "./.github/workflows/release-on-merge.yml"
    jobs = _workflow("release-on-merge.yml")["jobs"]
    assert set(jobs["tag"]["needs"]) == {"plan", "build"}
    assert jobs["tag"]["permissions"] == {"contents": "write"}
    assert "permissions" not in jobs["build"]
    # The reusable half never uploads to an index.
    used = {
        step.get("uses", "").split("@")[0]
        for job in jobs.values()
        for step in job.get("steps", [])
    }
    assert "pypa/gh-action-pypi-publish" not in used


def test_no_file_a_seat_edits_names_the_version() -> None:
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert "version" not in project
    assert project["dynamic"] == ["version"]
    assert "version" not in yaml.safe_load((ROOT / "release.yaml").read_text())["pypi"]


def test_pandoc_comes_only_from_the_pinned_checksummed_installer() -> None:
    """A slow Ubuntu mirror timed out CI once: no workflow uses apt for it."""
    for name in ("ci.yml", "publish.yml"):
        text = (WORKFLOWS / name).read_text()
        assert "tools/install-pandoc.sh" in text, name
        assert "apt-get install -y pandoc" not in text, name
    script = (ROOT / "tools" / "install-pandoc.sh").read_text()
    # Only live lines count: a commented-out check checks nothing.
    live = [
        line.strip()
        for line in script.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "set -euo pipefail" in live
    assert any(re.fullmatch(r'SHA256="[0-9a-f]{64}"', line) for line in live)
    check = next(
        i for i, line in enumerate(live) if "sha256sum --check --strict" in line
    )
    install = next(i for i, line in enumerate(live) if line.startswith("sudo dpkg -i"))
    assert check < install, "the checksum must be verified before dpkg installs it"
    assert "--proto '=https' --proto-redir '=https' --tlsv1.2" in script
    assert os.access(ROOT / "tools" / "install-pandoc.sh", os.X_OK)


# --------------------------------------------------------------------------
# The post-upload digest check
# --------------------------------------------------------------------------


def _publish_step(name: str) -> dict[str, Any]:
    steps = _workflow("publish.yml")["jobs"]["publish-pypi"]["steps"]
    found: dict[str, Any] = next(s for s in steps if s.get("name") == name)
    return found


def test_the_digests_are_recorded_before_the_upload() -> None:
    names = [
        s.get("name") for s in _workflow("publish.yml")["jobs"]["publish-pypi"]["steps"]
    ]
    assert names.index("Record the built files' digests") < names.index(
        "Publish to PyPI"
    )


def _run_digest_step(
    tmp_path: Path, tools: tuple[str, ...], *, files: bool = True, sdist: bool = True
) -> subprocess.CompletedProcess[bytes]:
    """Run the workflow's digest step with only `tools` (plus sort) on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("sort", *tools):
        found = shutil.which(tool)
        assert found, f"{tool} is not installed on this host"
        if not (bin_dir / tool).exists():
            (bin_dir / tool).symlink_to(found)
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    if files:
        (dist / "demo-1.2.4-py3-none-any.whl").write_bytes(b"wheel")
        if sdist:
            (dist / "demo-1.2.4.tar.gz").write_bytes(b"sdist")
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603 - the workflow's own step
        [bash, "-c", _publish_step("Record the built files' digests")["run"]],
        cwd=tmp_path,
        env={"PATH": str(bin_dir)},
        capture_output=True,
        check=False,
    )


_WANTED = {"demo-1.2.4-py3-none-any.whl": b"wheel", "demo-1.2.4.tar.gz": b"sdist"}


@pytest.mark.parametrize("tool", ["sha256sum", "shasum"])
def test_the_digests_are_recorded_with_either_checksum_tool(
    tmp_path: Path, tool: str
) -> None:
    """GNU `sha256sum` on Linux, `shasum -a 256` on macOS: the same manifest."""
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} is not installed on this host")
    done = _run_digest_step(tmp_path, (tool,))
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "built.sha256").read_text().splitlines() == sorted(
        f"{hashlib.sha256(content).hexdigest()}  {name}"
        for name, content in _WANTED.items()
    )


def test_the_digest_step_fails_without_a_checksum_tool(tmp_path: Path) -> None:
    done = _run_digest_step(tmp_path, ())
    assert done.returncode != 0
    assert (
        not (tmp_path / "built.sha256").exists()
        or not (tmp_path / "built.sha256").read_text()
    )


def test_the_digest_step_fails_when_there_is_nothing_to_record(
    tmp_path: Path,
) -> None:
    tool = "sha256sum" if shutil.which("sha256sum") else "shasum"
    done = _run_digest_step(tmp_path, (tool,), files=False)
    assert done.returncode != 0


def test_the_digest_step_fails_on_a_partial_build(tmp_path: Path) -> None:
    """A wheel with no sdist leaves a non-empty manifest; only `pipefail` fails it."""
    tool = "sha256sum" if shutil.which("sha256sum") else "shasum"
    done = _run_digest_step(tmp_path, (tool,), sdist=False)
    assert done.returncode != 0, done.stderr
    # The wheel's line was written: `test -s` alone would have passed this.
    assert (
        (tmp_path / "built.sha256")
        .read_text()
        .strip()
        .endswith("demo-1.2.4-py3-none-any.whl")
    )


@pytest.mark.parametrize("tampered,status", [(False, 0), (True, 1)])
def test_the_digest_check_compares_only_pypis_files(
    tmp_path: Path, tampered: bool, status: int
) -> None:
    """The publish action leaves *.publish.attestation files; PyPI doesn't list them."""
    dist = tmp_path / "dist"
    dist.mkdir()
    files = {"demo-1.2.4-py3-none-any.whl": b"wheel", "demo-1.2.4.tar.gz": b"sdist"}
    for name, content in files.items():
        (dist / name).write_bytes(content)
    subprocess.run(  # noqa: S603 - the workflow's own step
        ["bash", "-c", _publish_step("Record the built files' digests")["run"]],  # noqa: S607
        cwd=tmp_path,
        check=True,
    )
    for name in files:  # what the publish action writes after the upload
        (dist / f"{name}.publish.attestation").write_text("{}")
    served = {
        "urls": [
            {
                "filename": name,
                "digests": {
                    "sha256": hashlib.sha256(
                        content + (b"x" if tampered and name.endswith(".gz") else b"")
                    ).hexdigest()
                },
            }
            for name, content in files.items()
        ]
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
            ok = self.path == "/zeocore/1.2.4/json"
            self.send_response(200 if ok else 404)
            self.end_headers()
            self.wfile.write(json.dumps(served).encode() if ok else b"{}")

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        check = _publish_step(
            "Check PyPI holds exactly this build's files for the version"
        )["run"]
        result = subprocess.run(  # noqa: S603 - the workflow's own step
            ["bash", "-c", check],  # noqa: S607
            cwd=tmp_path,
            env={
                **os.environ,
                "VERSION": "1.2.4",
                "INDEX": f"http://127.0.0.1:{server.server_address[1]}",
            },
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == status, result.stdout + result.stderr
    if tampered:
        assert "are not this build's" in result.stdout


# --------------------------------------------------------------------------
# The version script
# --------------------------------------------------------------------------


PLAN = ROOT / ".github" / "release" / "plan.py"


def _script() -> str:
    return PLAN.read_text()


def _inline_script() -> str:
    text = (WORKFLOWS / "release-on-merge.yml").read_text()
    found = re.search(
        r"python3 - release-notes\.md <<'PY' >> \"\$GITHUB_OUTPUT\"\n(.*?)\n\s*PY\n",
        text,
        re.DOTALL,
    )
    assert found, "release-on-merge.yml no longer runs the inline version script"
    return textwrap.dedent(found.group(1))


class Index:
    """A local stand-in for PyPI's JSON API, answering for project ``demo``.

    ``listed`` is what the project page lists; ``present`` is which
    per-version pages answer 200, normally the same versions; ``status``
    forces every answer's code.
    """

    def __init__(self) -> None:
        self.listed: set[str] = {"1.2.3"}
        self.present: set[str] | None = None
        self.status: int | None = None
        self.paths: list[str] = []
        index = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
                index.paths.append(self.path)
                body, code = index.answer(self.path)
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def answer(self, path: str) -> tuple[bytes, int]:
        if self.status is not None:
            return b"{}", self.status
        present = self.listed if self.present is None else self.present
        if path == "/demo/json":
            if not self.listed:
                return b"{}", 404
            return json.dumps({"releases": {v: [] for v in self.listed}}).encode(), 200
        found = re.fullmatch(r"/demo/([^/]+)/json", path)
        if found and found[1] in present:
            return b"{}", 200
        return b"{}", 404


class Repo:
    def __init__(self, root: Path, index: Index) -> None:
        self.root = root
        self.index = index
        # Never inherit GIT_DIR, GIT_INDEX_FILE or GIT_WORK_TREE: a git hook
        # (pre-push runs make verify) sets them, and git would then act on
        # the real repository instead of this throwaway one.
        self.env = {
            # Never inherit the runner's GITHUB_* either: on Actions GITHUB_REPOSITORY
            # is set, and a test of the remote-derived name would silently use it.
            **{
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("GIT_", "GITHUB_"))
            },
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
        self.git("init", "-q", "-b", "main")
        resolved = Path(self.git("rev-parse", "--absolute-git-dir")).resolve()
        assert resolved == (root / ".git").resolve(), f"git resolved {resolved}"
        self.write("pyproject.toml", '[project]\nrequires-python = ">=3.14"\n')
        self.commit("start")

    def git(self, *args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed git in a throwaway repository
            ["git", *args],  # noqa: S607
            cwd=self.root,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def merge(self, branch: str, **files: str) -> None:
        """Merge a PR branch that writes ``files`` into main, with a merge commit."""
        self.git("checkout", "-q", "-b", branch)
        for path, text in files.items():
            self.write(path.replace("__", "/"), text)
        self.commit(branch)
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", f"Merge {branch}", branch)

    def release(self, **options: str) -> tuple[int, dict[str, str], str, str]:
        notes = self.root / "notes.md"
        result = subprocess.run(  # noqa: S603 - the extracted workflow script
            [sys.executable, "-", str(notes)],
            input=_script(),
            cwd=self.root,
            env={**self.env, "PROJECT": "demo", "INDEX": self.index.url, **options},
            capture_output=True,
            text=True,
            check=False,
        )
        outputs = dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )
        text = notes.read_text() if notes.exists() else ""
        return result.returncode, outputs, text, result.stderr


@pytest.fixture
def index() -> Iterator[Index]:
    made = Index()
    yield made
    made.server.shutdown()
    made.server.server_close()


@pytest.fixture
def repo(tmp_path: Path, index: Index) -> Repo:
    made = Repo(tmp_path, index)
    made.git("tag", "v1.2.3")
    return made


def test_a_merge_with_no_fragment_is_a_patch(repo: Repo) -> None:
    repo.merge("fix", **{"src.py": "x = 1\n"})
    status, outputs, notes, _ = repo.release()
    assert status == 0
    assert outputs == {"version": "1.2.4", "floor": "3.14", "tag": "v1.2.4"}
    assert "patch release" in notes


@pytest.mark.parametrize(
    "fragments,version",
    [
        ({"changelog.d__a.md": "bump: patch\nFixed a thing.\n"}, "1.2.4"),
        ({"changelog.d__a.md": "bump: minor\nAdded a thing.\n"}, "1.3.0"),
        (
            {
                "changelog.d__a.md": "bump: patch\nFixed.\n",
                "changelog.d__b.md": "bump: major\nBroke an API.\n",
            },
            "2.0.0",
        ),
    ],
)
def test_the_highest_declared_level_of_the_added_fragments_wins(
    repo: Repo, fragments: dict[str, str], version: str
) -> None:
    repo.merge("feature", **fragments)
    status, outputs, notes, stderr = repo.release()
    assert status == 0, stderr
    assert outputs["version"] == version
    for text in fragments.values():
        assert text.split("\n", 1)[1].strip() in notes


def test_an_old_fragment_on_main_is_never_counted_again(
    repo: Repo, index: Index
) -> None:
    repo.merge("one", **{"changelog.d__a.md": "bump: major\nBig.\n"})
    repo.git("tag", "v2.0.0")
    index.listed.add("2.0.0")
    repo.merge("two", **{"src.py": "x = 2\n"})
    status, outputs, _, stderr = repo.release()
    assert status == 0, stderr
    assert outputs["version"] == "2.0.1"


@pytest.mark.parametrize(
    "fragment", ["Fixed a thing.\n", "bump: huge\nFixed.\n", "bump:\n"]
)
def test_a_fragment_without_a_valid_bump_line_fails(repo: Repo, fragment: str) -> None:
    repo.merge("bad", **{"changelog.d__a.md": fragment})
    status, _, _, stderr = repo.release()
    assert status == 1
    assert "first line must be" in stderr


def test_changing_a_merged_fragment_fails(repo: Repo) -> None:
    repo.merge("one", **{"changelog.d__a.md": "bump: patch\nFixed.\n"})
    repo.git("tag", "v1.2.4")
    repo.merge("two", **{"changelog.d__a.md": "bump: major\nRewritten.\n"})
    status, _, _, stderr = repo.release()
    assert status == 1
    assert "changed or deleted" in stderr


def test_the_fragment_readme_may_change(repo: Repo) -> None:
    repo.merge("docs", **{"changelog.d__README.md": "How fragments work.\n"})
    status, outputs, _, stderr = repo.release()
    assert status == 0, stderr
    assert outputs["version"] == "1.2.4"


def test_a_commit_that_already_carries_its_tag_is_a_re_run(repo: Repo) -> None:
    repo.merge("feature", **{"changelog.d__a.md": "bump: minor\nAdded.\n"})
    repo.git("tag", "v1.3.0")
    status, outputs, _, stderr = repo.release()
    assert status == 0, stderr
    assert outputs["version"] == "1.3.0"
    assert "a re-run" in stderr


def test_a_computed_tag_already_at_another_commit_fails(repo: Repo) -> None:
    repo.git("checkout", "-q", "-b", "elsewhere")
    repo.write("other.py", "y = 1\n")
    repo.commit("elsewhere")
    repo.git("tag", "v1.2.4")
    repo.git("checkout", "-q", "main")
    repo.merge("fix", **{"src.py": "x = 1\n"})
    status, _, _, stderr = repo.release()
    assert status == 1
    assert "already exists at another commit" in stderr


def test_with_no_release_tag_to_count_from_it_fails(
    tmp_path: Path, index: Index
) -> None:
    repo = Repo(tmp_path, index)
    repo.merge("fix", **{"src.py": "x = 1\n"})
    status, _, _, stderr = repo.release()
    assert status == 1
    assert "no vX.Y.Z tag" in stderr


def test_a_version_on_pypi_without_a_tag_is_counted_from(
    repo: Repo, index: Index
) -> None:
    """0.15.1 reached PyPI from a dispatch and has no tag: count above it."""
    index.listed = {"1.2.3", "1.2.4"}
    repo.merge("fix", **{"changelog.d__a.md": "bump: patch\nFixed.\n"})
    status, outputs, _, stderr = repo.release()
    assert status == 0, stderr
    assert outputs["version"] == "1.2.5"
    assert "/demo/1.2.5/json" in index.paths


def test_a_computed_version_the_index_already_has_is_refused(
    repo: Repo, index: Index
) -> None:
    # The project page lags behind the version page: only a 404 is free.
    index.present = {"1.2.3", "1.2.4"}
    repo.merge("fix", **{"src.py": "x = 1\n"})
    status, _, _, stderr = repo.release()
    assert status == 1
    assert "1.2.4 is already on the index" in stderr


@pytest.mark.parametrize("code", [500, 503, 403])
def test_an_index_that_answers_neither_200_nor_404_fails(
    repo: Repo, index: Index, code: int
) -> None:
    index.status = code
    repo.merge("fix", **{"src.py": "x = 1\n"})
    status, _, _, stderr = repo.release()
    assert status == 1
    assert f"answered {code}" in stderr


def test_a_project_the_index_doesnt_know_fails(repo: Repo, index: Index) -> None:
    index.listed = set()
    repo.merge("fix", **{"src.py": "x = 1\n"})
    status, _, _, stderr = repo.release()
    assert status == 1
    assert "isn't on the index yet" in stderr


def test_a_fragment_from_a_merge_whose_run_failed_still_counts(repo: Repo) -> None:
    # Merge one's run never tagged; merge two's run must still see its bump.
    repo.merge("one", **{"changelog.d__a.md": "bump: minor\nAdded a thing.\n"})
    repo.merge("two", **{"src.py": "x = 1\n"})
    status, outputs, notes, stderr = repo.release()
    assert status == 0, stderr
    assert outputs["version"] == "1.3.0"
    assert "Added a thing." in notes


def test_a_tag_that_never_reached_the_index_stops_the_next_release(
    repo: Repo, index: Index
) -> None:
    # v1.2.4's publish failed, and another merge landed before its re-run.
    repo.merge("one", **{"changelog.d__a.md": "bump: patch\nLost notes.\n"})
    repo.git("tag", "v1.2.4")
    repo.merge("two", **{"src.py": "x = 1\n"})
    status, _, _, stderr = repo.release()
    assert status == 1
    assert "v1.2.4 is tagged but not on the index" in stderr


def test_a_tag_the_project_page_omits_but_the_index_serves_is_fine(
    repo: Repo, index: Index
) -> None:
    # The project page can lag the version page: only the version's own 404
    # means it never reached the index.
    repo.merge("one", **{"src.py": "x = 1\n"})
    repo.git("tag", "v1.2.4")
    index.present = {"1.2.3", "1.2.4"}
    repo.merge("two", **{"src.py": "x = 2\n"})
    status, outputs, _, stderr = repo.release()
    assert status == 0, stderr
    assert outputs["version"] == "1.2.5"


def test_a_re_run_never_asks_the_index(repo: Repo, index: Index) -> None:
    repo.merge("fix", **{"src.py": "x = 1\n"})
    repo.git("tag", "v1.2.4")
    index.status = 500
    status, outputs, _, stderr = repo.release()
    assert status == 0, stderr
    assert outputs["version"] == "1.2.4"
    assert index.paths == []


# --------------------------------------------------------------------------
# One component for every repository: its shape, its namespace, its runner
# --------------------------------------------------------------------------


def test_the_inline_copy_is_the_runner_file() -> None:
    """A runner outside Actions (Pomar) runs plan.py; Actions runs the copy."""
    assert _inline_script().rstrip() == _script().rstrip()


def test_the_component_takes_no_version_commit_or_ref() -> None:
    document = _workflow("release-on-merge.yml")
    assert set(document[True]) == {"workflow_call"}
    inputs = set(document[True]["workflow_call"]["inputs"])
    assert inputs == {"project", "verify", "build", "tag_prefix", "index"}


def test_only_the_docs_site_can_be_started_by_hand() -> None:
    """No release, tag or upload can be cut by a person pressing a button."""
    by_hand = {
        path.name
        for path in [*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]
        if "workflow_dispatch" in yaml.safe_load(path.read_text())[True]
    }
    assert by_hand == {"docs.yml"}  # the docs site; it releases nothing
    for name in ("publish.yml", "release-on-merge.yml"):
        triggers = set(_workflow(name)[True])
        assert not triggers & {"pull_request", "pull_request_target", "schedule"}
    # The caller's trigger is exactly a push to main: no tags, no paths filter.
    assert _workflow("publish.yml")[True] == {"push": {"branches": ["main"]}}
    assert set(_workflow("release-on-merge.yml")[True]) == {"workflow_call"}


def test_the_default_namespace_is_v_and_the_default_index_is_pypi() -> None:
    inputs = _workflow("release-on-merge.yml")[True]["workflow_call"]["inputs"]
    assert inputs["tag_prefix"]["default"] == "v"
    assert inputs["index"]["default"] == "https://pypi.org/pypi"


def test_a_namespaced_repository_with_no_index_counts_from_its_own_tags(
    tmp_path: Path, index: Index
) -> None:
    made = Repo(tmp_path, index)
    made.git("tag", "-a", "-m", "engine", "engine/v1.2.3")
    made.git("tag", "v9.9.9")  # another namespace: not counted
    made.merge("work", **{"changelog.d__more.md": "bump: minor\n\nA feature.\n"})
    code, outputs, notes, stderr = made.release(TAG_PREFIX="engine/v", INDEX="none")
    assert code == 0, stderr
    assert outputs["version"] == "1.3.0"
    assert outputs["tag"] == "engine/v1.3.0"
    assert "A feature." in notes
    assert index.paths == []  # no package index was asked


def test_a_pyproject_less_repository_has_no_python_floor(
    tmp_path: Path, index: Index
) -> None:
    made = Repo(tmp_path, index)
    (tmp_path / "pyproject.toml").unlink()
    made.commit("no python package here")
    made.git("tag", "engine/v1.0.0")
    made.merge("work", **{"src.py": "x = 1\n"})
    code, outputs, _, stderr = made.release(TAG_PREFIX="engine/v", INDEX="none")
    assert code == 0, stderr
    assert outputs["floor"] == "" and outputs["tag"] == "engine/v1.0.1"


def test_a_default_run_still_tags_v_and_still_asks_the_index(repo: Repo) -> None:
    repo.merge("work", **{"changelog.d__more.md": "bump: patch\n\nA fix.\n"})
    code, outputs, _, stderr = repo.release()
    assert code == 0, stderr
    assert outputs["tag"] == "v1.2.4" and outputs["version"] == "1.2.4"
    assert repo.index.paths  # PyPI's answer is still the positive control


# --------------------------------------------------------------------------
# The release event
# --------------------------------------------------------------------------

EVENT = ROOT / ".github" / "release" / "event.py"


def _event(made: Repo, tag: str, artifacts: Path | None = None) -> dict[str, Any]:
    notes = made.root / "notes.md"
    notes.write_text("A fix.\n")
    command = [sys.executable, str(EVENT), tag, str(notes)]
    if artifacts is not None:
        command.append(str(artifacts))
    result = subprocess.run(  # noqa: S603 - the repository's own script
        command,
        cwd=made.root,
        env=made.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    document: dict[str, Any] = json.loads(result.stdout)
    return document


def _released(tmp_path: Path, index: Index) -> tuple[Repo, Path]:
    (tmp_path / "repo").mkdir()
    made = Repo(tmp_path / "repo", index)
    made.git("remote", "add", "origin", "https://example.invalid/org/demo.git")
    made.git("tag", "v1.2.4")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "demo-1.2.4.tar.gz").write_bytes(b"sdist")
    (dist / "demo-1.2.4-py3-none-any.whl").write_bytes(b"wheel")
    return made, dist


def test_the_release_event_names_the_commit_tree_notes_and_every_file(
    tmp_path: Path, index: Index
) -> None:
    made, dist = _released(tmp_path, index)
    event = _event(made, "v1.2.4", dist)
    assert event["schema"] == "zeo.release-event/v1"
    assert event["tag"] == "v1.2.4"
    assert event["commit"] == made.git("rev-parse", "HEAD")
    assert event["tree"] == made.git("rev-parse", "HEAD^{tree}")
    assert event["tag_object"] == made.git("rev-parse", "refs/tags/v1.2.4")
    assert event["notes_sha256"] == hashlib.sha256(b"A fix.\n").hexdigest()
    assert [a["path"] for a in event["artifacts"]] == [
        "demo-1.2.4-py3-none-any.whl",
        "demo-1.2.4.tar.gz",
    ]
    wheel = event["artifacts"][0]
    assert wheel["sha256"] == hashlib.sha256(b"wheel").hexdigest()
    assert wheel["size"] == len(b"wheel")


def test_the_release_event_ends_with_a_digest_of_everything_above_it(
    tmp_path: Path, index: Index
) -> None:
    made, dist = _released(tmp_path, index)
    event = _event(made, "v1.2.4", dist)
    body = {k: v for k, v in event.items() if k != "event_sha256"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    assert event["event_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    body["artifacts"][0]["sha256"] = "0" * 64  # an altered event no longer matches
    altered = json.dumps(body, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(altered.encode()).hexdigest() != event["event_sha256"]


def test_a_release_with_no_artifact_still_has_an_event(
    tmp_path: Path, index: Index
) -> None:
    made, _ = _released(tmp_path, index)
    assert _event(made, "v1.2.4")["artifacts"] == []


def test_the_github_release_carries_the_event_and_checks_out_first() -> None:
    steps = _workflow("publish.yml")["jobs"]["github-release"]["steps"]
    names = [step["name"] for step in steps]
    assert names[0].startswith("Check out")  # checkout cleans the tree
    run = " ".join(step.get("run", "") for step in steps)
    assert ".github/release/event.py" in run
    assert "release-event.json" in run.split("gh release create", 1)[1]


RESERVED = [
    ".github/workflows/release-on-merge.yml",
    ".github/workflows/publish.yml",
    ".github/release/plan.py",
    ".github/release/event.py",
    "release.yaml",
]


def test_the_executors_files_exist_and_are_on_the_reserved_list() -> None:
    readme = (ROOT / ".github" / "release" / "README.md").read_text()
    for path in RESERVED:
        assert (ROOT / path).is_file(), path
        assert f"`{path}`" in readme, path


def test_the_detached_files_are_the_canonical_bytes_and_their_digest(
    tmp_path: Path, index: Index
) -> None:
    made, dist = _released(tmp_path, index)
    notes = made.root / "notes.md"
    notes.write_text("A fix.\n")
    out = tmp_path / "detached"
    result = subprocess.run(  # noqa: S603 - the repository's own script
        [
            sys.executable,
            str(EVENT),
            "v1.2.4",
            str(notes),
            str(dist),
            "--detached",
            str(out),
        ],
        cwd=made.root,
        env=made.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    event = json.loads(result.stdout)
    payload = (out / "release-event.payload.json").read_bytes()
    digest = (out / "release-event.sha256").read_text()
    assert digest == event["event_sha256"] + "\n"
    assert hashlib.sha256(payload).hexdigest() == event["event_sha256"]
    body = json.loads(payload)
    assert "event_sha256" not in body  # the one excluded field
    assert {**body, "event_sha256": event["event_sha256"]} == event
    assert b" " not in payload.replace(b"A fix", b"")  # no spaces: sorted, compact


def test_a_recomputed_digest_over_an_altered_payload_is_not_the_events_digest(
    tmp_path: Path, index: Index
) -> None:
    """A verifier recomputes the digest from the signed payload bytes, and also
    compares the commit and file digests with what it received: a digest alone
    over altered content proves nothing, so the event alone never authenticates."""
    made, dist = _released(tmp_path, index)
    event = _event(made, "v1.2.4", dist)
    body = {k: v for k, v in event.items() if k != "event_sha256"}
    body["commit"] = "0" * 40  # a different commit
    recomputed = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert recomputed != event["event_sha256"]
    assert body["commit"] != made.git("rev-parse", "HEAD")


def test_the_tag_is_the_prefix_and_the_bare_version_never_doubled(
    tmp_path: Path, index: Index
) -> None:
    made = Repo(tmp_path, index)
    made.git("tag", "-a", "-m", "engine", "engine/v2.0.0")
    made.merge("work", **{"src.py": "x = 1\n"})
    _, outputs, _, _ = made.release(TAG_PREFIX="engine/v", INDEX="none")
    assert outputs["version"] == "2.0.1" and outputs["tag"] == "engine/v2.0.1"
    assert "vv" not in outputs["tag"]


def _event_run(made: Repo, *extra: str, **env: str) -> subprocess.CompletedProcess[str]:
    notes = made.root / "notes.md"
    notes.write_text("A fix.\n")
    return subprocess.run(  # noqa: S603 - the repository's own script
        [sys.executable, str(EVENT), "v1.2.4", str(notes), *extra],
        cwd=made.root,
        env={**made.env, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_credential_in_the_remote_is_refused_and_never_printed(
    tmp_path: Path, index: Index
) -> None:
    made, _ = _released(tmp_path, index)
    made.git(
        "remote",
        "set-url",
        "origin",
        "https://user:s3cr3t-token@example.invalid/org/demo.git",
    )
    result = _event_run(made)
    assert result.returncode != 0 and "userinfo" in result.stderr
    assert "s3cr3t" not in result.stdout + result.stderr


def test_a_credential_in_an_explicit_name_is_refused_too(
    tmp_path: Path, index: Index
) -> None:
    made, _ = _released(tmp_path, index)
    result = _event_run(
        made, "--repository", "https://user:s3cr3t-token@example.invalid/org/demo"
    )
    assert result.returncode != 0 and "s3cr3t" not in result.stdout + result.stderr
    ok = _event_run(made, "--repository", "org/demo")
    assert ok.returncode == 0 and json.loads(ok.stdout)["repository"] == "org/demo"


def test_the_actions_repository_name_is_used_when_the_runner_has_one(
    tmp_path: Path, index: Index
) -> None:
    made, dist = _released(tmp_path, index)
    made.env["GITHUB_REPOSITORY"] = "org/demo"
    assert _event(made, "v1.2.4", dist)["repository"] == "org/demo"


@pytest.mark.parametrize(
    "prefix", ["", "V", "release-", "../v", "engine/v ", "a/b/v", "Engine/v", "v1"]
)
def test_a_tag_prefix_outside_the_closed_grammar_fails(repo: Repo, prefix: str) -> None:
    code, _, _, stderr = repo.release(TAG_PREFIX=prefix, INDEX="none")
    assert code != 0 and "TAG_PREFIX" in stderr


def test_the_runners_repository_name_wins_over_a_poisoned_remote_and_no_token_prints(
    tmp_path: Path, index: Index
) -> None:
    """Both orders pinned: with GITHUB_REPOSITORY set (as on Actions) the name comes
    from it and the credential in the remote is never read or printed; with it
    unset the same remote is refused."""
    made, _ = _released(tmp_path, index)
    made.git(
        "remote",
        "set-url",
        "origin",
        "https://user:s3cr3t-token@example.invalid/org/demo.git",
    )
    runner = _event_run(made, GITHUB_REPOSITORY="org/demo")
    assert runner.returncode == 0, runner.stderr
    assert json.loads(runner.stdout)["repository"] == "org/demo"
    assert "s3cr3t" not in runner.stdout + runner.stderr
    local = _event_run(made)
    assert local.returncode != 0 and "s3cr3t" not in local.stdout + local.stderr
