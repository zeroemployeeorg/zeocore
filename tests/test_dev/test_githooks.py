"""The committed git hooks (SOW-31 rule 10, one gate).

`make verify` is the whole gate. The hook that runs it before a push is committed in
`.githooks` and installed by `make setup`, so a fresh clone has it. These tests hold the
hooks to that: they exist and run under `sh`, they carry no way past the gate, they drop
the environment git hands a hook, and their exit codes and messages are what a caller
needs.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".githooks"
PRE_PUSH = HOOKS / "pre-push"
PRE_COMMIT = HOOKS / "pre-commit"

GIT = shutil.which("git")
SH = shutil.which("sh")
MAKE = shutil.which("make")

NO_GIT = pytest.mark.skipif(GIT is None or SH is None, reason="needs git and sh")

#: Stands for the throwaway repository's HEAD in a push line; `_push` swaps it in.
SHA = "@HEAD@"
ZEROS = "0" * 40


def _clean_env() -> dict[str, str]:
    """The environment of a plain shell: nothing git put there."""
    return {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        argv,
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _repo(tmp_path: Path, *, commit: bool = True) -> Path:
    """A throwaway repository holding copies of the committed hooks, committed."""
    assert GIT is not None
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _clean_env()
    _run([GIT, "init", "-q"], cwd=repo, env=env)
    hooks = repo / ".githooks"
    hooks.mkdir()
    for source in (PRE_PUSH, PRE_COMMIT):
        target = hooks / source.name
        shutil.copy2(source, target)
    if commit:
        for key, value in (("user.name", "t"), ("user.email", "t@example.com")):
            _run([GIT, "config", key, value], cwd=repo, env=env)
        _run([GIT, "add", "."], cwd=repo, env=env)
        _run([GIT, "commit", "-q", "-m", "hooks"], cwd=repo, env=env)
    return repo


def _head(repo: Path) -> str:
    assert GIT is not None
    done = _run([GIT, "rev-parse", "HEAD"], cwd=repo, env=_clean_env())
    return done.stdout.strip()


def _fake_make(tmp_path: Path, exit_code: int) -> tuple[Path, Path]:
    """A `make` that records how it was called and what GIT_ it saw, then exits."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "make-record"
    script = bin_dir / "make"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "args: $*" >> "{record}"\n'
        f'env | grep "^GIT_" >> "{record}.git" || true\n'
        f"exit {exit_code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return bin_dir, record


def _env_with(bin_dir: Path, **extra: str) -> dict[str, str]:
    env = _clean_env()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env.update(extra)
    return env


class TestTheCommittedFiles:
    @pytest.mark.parametrize("hook", [PRE_PUSH, PRE_COMMIT], ids=lambda p: p.name)
    def test_each_hook_exists_runs_under_sh_and_is_executable(self, hook: Path) -> None:
        assert hook.is_file()
        assert hook.read_text().splitlines()[0] == "#!/bin/sh"
        assert hook.stat().st_mode & stat.S_IXUSR

    @pytest.mark.parametrize("hook", [PRE_PUSH, PRE_COMMIT], ids=lambda p: p.name)
    @NO_GIT
    def test_each_hook_is_valid_posix_sh(self, hook: Path) -> None:
        assert SH is not None
        done = _run([SH, "-n", str(hook)], cwd=ROOT, env=_clean_env())
        assert done.returncode == 0, done.stderr

    @NO_GIT
    def test_git_records_the_hooks_as_executable(self) -> None:
        assert GIT is not None
        done = _run(
            [GIT, "ls-files", "-s", "--", ".githooks"], cwd=ROOT, env=_clean_env()
        )
        if not done.stdout.strip():
            pytest.skip("not a git checkout")
        modes = {line.split()[0] for line in done.stdout.splitlines()}
        assert modes == {"100755"}

    def test_the_pre_push_hook_runs_make_verify_and_nothing_weaker(self) -> None:
        text = PRE_PUSH.read_text()
        assert re.search(r"^\s*if ! make verify; then$", text, re.MULTILINE)
        assert "verify-fast" not in text
        assert "set +e" not in text

    def test_there_is_no_way_past_the_gate(self) -> None:
        text = PRE_PUSH.read_text()
        assert not re.search(r"\b(skip|bypass|no[-_]verify|force)\w*", text, re.I)
        assert not re.search(r"\$\{?[A-Z_]*(SKIP|BYPASS)[A-Z_]*", text)

    def test_the_hook_never_passes_git_s_environment_on(self) -> None:
        text = PRE_PUSH.read_text()
        assert "GIT_DIR" not in text
        assert "export GIT_" not in text
        assert 'unset "$name"' in text

    def test_the_failure_messages_end_with_an_action(self) -> None:
        errors = re.findall(r'echo "(pre-push: [^"]*)" >&2', PRE_PUSH.read_text())
        assert len(errors) == 7
        assert all(message.endswith("push again.") for message in errors)


@pytest.mark.skipif(MAKE is None, reason="needs make")
class TestMakeSetup:
    def test_make_hooks_installs_the_committed_directory(self) -> None:
        assert MAKE is not None
        done = _run([MAKE, "-n", "hooks"], cwd=ROOT, env=_clean_env())
        assert done.returncode == 0, done.stderr
        assert "core.hooksPath .githooks" in done.stdout

    def test_make_setup_runs_it(self) -> None:
        assert MAKE is not None
        done = _run([MAKE, "-n", "setup"], cwd=ROOT, env=_clean_env())
        assert done.returncode == 0, done.stderr
        assert "core.hooksPath" in done.stdout

    @NO_GIT
    def test_make_hooks_sets_the_path_once_and_leaves_another_one_alone(
        self, tmp_path: Path
    ) -> None:
        assert MAKE is not None and GIT is not None
        repo = tmp_path / "checkout"
        repo.mkdir()
        env = _clean_env()
        _run([GIT, "init", "-q"], cwd=repo, env=env)
        for name in ("Makefile", "hygiene-grepbans.mk"):
            shutil.copy2(ROOT / name, repo / name)

        first = _run([MAKE, "hooks"], cwd=repo, env=env)
        assert first.returncode == 0, first.stderr
        assert "installed" in first.stdout
        configured = _run([GIT, "config", "core.hooksPath"], cwd=repo, env=env)
        assert configured.stdout.strip() == ".githooks"

        again = _run([MAKE, "hooks"], cwd=repo, env=env)
        assert again.returncode == 0
        assert "already installed" in again.stdout

        _run([GIT, "config", "core.hooksPath", "elsewhere"], cwd=repo, env=env)
        other = _run([MAKE, "hooks"], cwd=repo, env=env)
        assert other.returncode == 0
        assert "git config core.hooksPath .githooks" in other.stdout
        kept = _run([GIT, "config", "core.hooksPath"], cwd=repo, env=env)
        assert kept.stdout.strip() == "elsewhere"


@NO_GIT
class TestThePrePushHook:
    def _push(
        self, tmp_path: Path, *, exit_code: int, stdin: str, **extra: str
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        repo = _repo(tmp_path)
        bin_dir, record = _fake_make(tmp_path, exit_code)
        done = _run(
            [str(repo / ".githooks" / "pre-push")],
            cwd=repo,
            env=_env_with(bin_dir, **extra),
            stdin=stdin.replace(SHA, _head(repo)),
        )
        return done, record

    def test_a_passing_gate_lets_the_push_through(self, tmp_path: Path) -> None:
        stdin = f"refs/heads/x {SHA} refs/heads/x {ZEROS}\n"
        done, record = self._push(tmp_path, exit_code=0, stdin=stdin)
        assert done.returncode == 0, done.stderr
        assert record.read_text().strip() == "args: verify"

    def test_a_failing_gate_stops_it_and_says_what_to_do(self, tmp_path: Path) -> None:
        stdin = f"refs/heads/x {SHA} refs/heads/x {ZEROS}\n"
        done, record = self._push(tmp_path, exit_code=3, stdin=stdin)
        assert done.returncode == 1
        assert record.read_text().strip() == "args: verify"
        message = done.stderr.strip().splitlines()[-1]
        assert "nothing was pushed" in message
        assert message.endswith("then push again.")

    def test_a_hand_run_with_nothing_on_stdin_is_gated_too(
        self, tmp_path: Path
    ) -> None:
        done, record = self._push(tmp_path, exit_code=0, stdin="")
        assert done.returncode == 0
        assert record.read_text().strip() == "args: verify"

    def test_deleting_only_refs_has_nothing_to_gate(self, tmp_path: Path) -> None:
        stdin = f"(delete) {ZEROS} refs/heads/x {SHA}\n"
        done, record = self._push(tmp_path, exit_code=3, stdin=stdin)
        assert done.returncode == 0
        assert not record.exists()

    def test_a_push_that_also_adds_a_ref_is_gated(self, tmp_path: Path) -> None:
        stdin = (
            f"(delete) {ZEROS} refs/heads/old {SHA}\n"
            f"refs/heads/new {SHA} refs/heads/new {ZEROS}\n"
        )
        done, record = self._push(tmp_path, exit_code=0, stdin=stdin)
        assert done.returncode == 0
        assert record.read_text().strip() == "args: verify"

    def test_a_work_in_progress_backup_is_not_gated(self, tmp_path: Path) -> None:
        stdin = f"refs/heads/x {SHA} refs/heads/zeocore/wip/hook-20261024 {ZEROS}\n"
        done, record = self._push(tmp_path, exit_code=3, stdin=stdin)
        assert done.returncode == 0, done.stderr
        assert not record.exists()

    def test_a_backup_does_not_have_to_be_the_checked_out_commit(
        self, tmp_path: Path
    ) -> None:
        other = "1" * 40
        stdin = f"refs/heads/x {other} refs/heads/zeocore/wip/topic {ZEROS}\n"
        done, record = self._push(tmp_path, exit_code=3, stdin=stdin)
        assert done.returncode == 0, done.stderr
        assert not record.exists()

    def test_backups_and_deletions_together_are_not_gated(self, tmp_path: Path) -> None:
        stdin = (
            f"(delete) {ZEROS} refs/heads/zeocore/old {SHA}\n"
            f"refs/heads/x {SHA} refs/heads/zeocore/wip/a {ZEROS}\n"
            f"refs/heads/y {SHA} refs/heads/zeocore/wip/b {ZEROS}\n"
        )
        done, record = self._push(tmp_path, exit_code=3, stdin=stdin)
        assert done.returncode == 0, done.stderr
        assert not record.exists()

    def test_one_ref_that_is_not_a_backup_gates_the_whole_push(
        self, tmp_path: Path
    ) -> None:
        stdin = (
            f"refs/heads/x {SHA} refs/heads/zeocore/wip/a {ZEROS}\n"
            f"refs/heads/y {SHA} refs/heads/zeocore/topic {ZEROS}\n"
        )
        done, record = self._push(tmp_path, exit_code=3, stdin=stdin)
        assert done.returncode == 1
        assert record.read_text().strip() == "args: verify"

    @pytest.mark.parametrize("wip", [False, True])
    def test_a_push_to_main_is_refused_without_running_the_gate(
        self, tmp_path: Path, wip: bool
    ) -> None:
        extra = f"refs/heads/x {SHA} refs/heads/zeocore/wip/a {ZEROS}\n" if wip else ""
        stdin = extra + f"refs/heads/main {SHA} refs/heads/main {ZEROS}\n"
        done, record = self._push(tmp_path, exit_code=0, stdin=stdin)
        assert done.returncode == 1
        assert not record.exists()
        assert "a push to main is refused" in done.stderr
        assert done.stderr.strip().endswith("then push again.")

    def test_deleting_main_is_refused_too(self, tmp_path: Path) -> None:
        stdin = f"(delete) {ZEROS} refs/heads/main {SHA}\n"
        done, record = self._push(tmp_path, exit_code=0, stdin=stdin)
        assert done.returncode == 1
        assert not record.exists()

    @pytest.mark.parametrize(
        "remote",
        [
            "refs/heads/zeocore/topic",
            "refs/heads/wip/topic",  # no seat before wip
            "refs/heads/zeocore/wip",  # no topic
            "refs/heads/zeocore/wip/",  # empty topic
            "refs/heads/zeocore/wip/a/b",  # nested topic
            "refs/heads/zeocore/wip/.hidden",
            "refs/heads/zeocore/notwip/topic",
            "refs/heads/zeocore/wipe/topic",
            "refs/heads//wip/topic",  # empty seat
            "refs/tags/zeocore/wip/topic",  # a tag is not a branch
            "refs/tags/wip/topic",  # would read as seat "tags" if tags counted
            "refs/for/zeocore/wip/topic",
            "zeocore/wip/topic",  # not a full ref
        ],
    )
    def test_anything_else_is_still_gated(self, tmp_path: Path, remote: str) -> None:
        stdin = f"refs/heads/x {SHA} {remote} {ZEROS}\n"
        done, record = self._push(tmp_path, exit_code=3, stdin=stdin)
        assert done.returncode == 1
        assert record.read_text().strip() == "args: verify"

    def test_make_never_sees_the_environment_git_hands_a_hook(
        self, tmp_path: Path
    ) -> None:
        stdin = f"refs/heads/x {SHA} refs/heads/x {ZEROS}\n"
        done, record = self._push(
            tmp_path,
            exit_code=0,
            stdin=stdin,
            GIT_DIR=str(tmp_path / "not-this-repo"),
            GIT_INDEX_FILE=str(tmp_path / "index"),
            GIT_AUTHOR_NAME="someone",
        )
        assert done.returncode == 0, done.stderr
        assert not Path(f"{record}.git").read_text().strip()

    def test_without_make_it_fails_and_says_what_to_do(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        tools = tmp_path / "tools-without-make"
        tools.mkdir()
        for tool in ("env", "sed", "git"):
            found = shutil.which(tool)
            assert found is not None
            (tools / tool).symlink_to(found)
        env = _clean_env()
        env["PATH"] = str(tools)
        done = _run(
            [str(repo / ".githooks" / "pre-push")],
            cwd=repo,
            env=env,
            stdin=f"refs/heads/x {_head(repo)} refs/heads/x {ZEROS}\n",
        )
        assert done.returncode == 1
        assert done.stderr.strip().endswith("then push again.")

    def test_pushing_something_that_is_not_head_is_refused(
        self, tmp_path: Path
    ) -> None:
        stdin = f"refs/heads/other {'2' * 40} refs/heads/other {ZEROS}\n"
        done, record = self._push(tmp_path, exit_code=0, stdin=stdin)
        assert done.returncode == 1
        assert not record.exists()
        assert "refs/heads/other is not HEAD" in done.stderr
        assert done.stderr.strip().endswith("then push again.")

    def test_one_ref_that_is_not_head_refuses_a_push_of_several(
        self, tmp_path: Path
    ) -> None:
        stdin = (
            f"refs/heads/x {SHA} refs/heads/x {ZEROS}\n"
            f"refs/heads/y {'2' * 40} refs/heads/y {ZEROS}\n"
        )
        done, record = self._push(tmp_path, exit_code=0, stdin=stdin)
        assert done.returncode == 1
        assert not record.exists()
        assert "refs/heads/y is not HEAD" in done.stderr

    def test_uncommitted_changes_refuse_the_push(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        bin_dir, record = _fake_make(tmp_path, 0)
        (repo / ".githooks" / "pre-commit").write_text("#!/bin/sh\n# edited\n")
        done = _run(
            [str(repo / ".githooks" / "pre-push")],
            cwd=repo,
            env=_env_with(bin_dir),
            stdin=f"refs/heads/x {_head(repo)} refs/heads/x {ZEROS}\n",
        )
        assert done.returncode == 1
        assert not record.exists()
        assert "uncommitted changes" in done.stderr
        assert done.stderr.strip().endswith("then push again.")

    def test_an_untracked_file_does_not_refuse_the_push(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        bin_dir, record = _fake_make(tmp_path, 0)
        (repo / "scratch.txt").write_text("notes\n")
        done = _run(
            [str(repo / ".githooks" / "pre-push")],
            cwd=repo,
            env=_env_with(bin_dir),
            stdin=f"refs/heads/x {_head(repo)} refs/heads/x {ZEROS}\n",
        )
        assert done.returncode == 0, done.stderr
        assert record.read_text().strip() == "args: verify"

    def test_a_checkout_with_no_commit_says_so(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, commit=False)
        bin_dir, record = _fake_make(tmp_path, 0)
        done = _run(
            [str(repo / ".githooks" / "pre-push")],
            cwd=repo,
            env=_env_with(bin_dir),
            stdin=f"refs/heads/x {'2' * 40} refs/heads/x {ZEROS}\n",
        )
        assert done.returncode == 1
        assert not record.exists()
        assert "no commit" in done.stderr


@NO_GIT
class TestThePreCommitHook:
    def test_it_hands_over_to_the_machines_own_hook(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        marker = tmp_path / "ran"
        machine = repo / ".git" / "hooks" / "pre-commit"
        machine.parent.mkdir(exist_ok=True)
        machine.write_text(f'#!/bin/sh\necho "$@" > "{marker}"\nexit 5\n')
        machine.chmod(machine.stat().st_mode | stat.S_IXUSR)
        done = _run(
            [str(repo / ".githooks" / "pre-commit"), "arg"],
            cwd=repo,
            env=_clean_env(),
        )
        assert done.returncode == 5
        assert marker.read_text().strip() == "arg"

    def test_it_does_nothing_on_a_machine_without_one(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        done = _run(
            [str(repo / ".githooks" / "pre-commit")], cwd=repo, env=_clean_env()
        )
        assert done.returncode == 0

    def test_it_never_runs_itself_when_it_is_the_machines_hook(
        self, tmp_path: Path
    ) -> None:
        repo = _repo(tmp_path)
        machine = repo / ".git" / "hooks" / "pre-commit"
        machine.parent.mkdir(exist_ok=True)
        shutil.copy2(PRE_COMMIT, machine)
        done = _run([str(machine)], cwd=repo, env=_clean_env())
        assert done.returncode == 0
