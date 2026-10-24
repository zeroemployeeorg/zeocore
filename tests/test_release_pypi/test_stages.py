"""The PyPI adapter's stages (ZEOCORE-SOW-26), with fake GitHub and PyPI reads."""

from __future__ import annotations

import base64
import json
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from zeo_core.cli.release import make_command
from zeo_core.release.pypi import (
    GitHubCli,
    ManifestError,
    Outcome,
    PyPIHttp,
    PyPIManifest,
    TargetUnreadableError,
    admit,
    apply,
    approve,
    load,
    plan,
    reconcile,
    verify,
)
from zeo_core.release.pypi.reads import (
    ApprovalAnswerLostError,
    ApprovalResult,
    EnvironmentPolicy,
    Identity,
    PendingDeployment,
    Provenance,
    PyPIFile,
    RunApproval,
    TagAnswerLostError,
    TagResult,
    TagRuleset,
    WorkflowRun,
)
from zeo_core.release.signing import SigningJournal
from zeo_core.release.signing.toolchain import Completed

REPO = "zeroemployeeorg/zeocore"
COMMIT = "a" * 40
VERSION = "0.13.0"
PUBLISHER = {
    "kind": "GitHub",
    "repository": REPO,
    "workflow": "publish.yml",
    "environment": "pypi",
}


def _manifest(**changes: str) -> PyPIManifest:
    values = {
        "project": "zeocore",
        "version": VERSION,
        "repository": REPO,
        "commit": COMMIT,
        "workflow": "publish.yml",
        "environment": "pypi",
        **changes,
    }
    return PyPIManifest.model_validate(values)


def _section(version: str = VERSION) -> dict[str, str]:
    return {
        "project": "zeocore",
        "version": version,
        "repository": REPO,
        "commit": COMMIT,
        "workflow": "publish.yml",
        "environment": "pypi",
    }


def _committed(version: str = VERSION) -> dict[str, str]:
    """The section as committed: a file can't name its own commit."""
    section = _section(version)
    del section["commit"]
    return section


def _files_at(version: str = VERSION) -> dict[str, bytes]:
    pyproject = f'[project]\nname = "zeocore"\nversion = "{version}"\n'
    return {
        "pyproject.toml": pyproject.encode(),
        "release.yaml": yaml.safe_dump({"pypi": _committed(version)}).encode(),
        "CHANGELOG.md": f"# Changelog\n\n## [{version}] - 2026-10-11\n".encode(),
        ".github/workflows/publish.yml": b"name: Publish\n",
    }


APP_ID = 4242
GOOD_POLICY = EnvironmentPolicy(
    required_reviewers=frozenset({"matorclawson"}),
    prevent_self_review=True,
    can_admins_bypass=False,
    custom_policies=(("v*", "tag"),),
)
GOOD_RULESET = TagRuleset(
    id=7,
    enforcement="active",
    includes=("refs/tags/v*",),
    excludes=(),
    rules=frozenset({"creation", "update", "deletion"}),
    current_user_can_bypass="never",
    bypass=None,  # hidden from a non-Admin, as under ruling r7
)
WAITING = WorkflowRun("waiting", None, "v0.13.0", 99)
PENDING = (PendingDeployment(555, "pypi", True),)


@dataclass
class FakeGitHub:
    merged: bool = True
    files: dict[str, bytes] = field(default_factory=_files_at)
    tag: str | None = None
    policy: EnvironmentPolicy | None = field(default_factory=lambda: GOOD_POLICY)
    rulesets: tuple[TagRuleset, ...] = (GOOD_RULESET,)
    approver: Identity = field(default_factory=lambda: Identity("matorclawson", True))
    workflow_runs: tuple[WorkflowRun, ...] = ()
    pending: tuple[PendingDeployment, ...] = PENDING
    approval_result: ApprovalResult | None = "approved"  # None: the answer is lost
    approvals: list[tuple[int, int]] = field(default_factory=list)
    reviews: tuple[RunApproval, ...] = ()
    broken: bool = False

    def _check(self) -> None:
        if self.broken:
            raise TargetUnreadableError("GitHub could not be read")

    def default_branch(self, repo: str) -> str:
        self._check()
        return "main"

    def is_ancestor(self, repo: str, commit: str, branch: str) -> bool:
        return self.merged

    def file_at(self, repo: str, commit: str, path: str) -> bytes | None:
        return self.files.get(path)

    def tag_commit(self, repo: str, tag: str) -> str | None:
        self._check()
        return self.tag

    def environment(self, repo: str, environment: str) -> EnvironmentPolicy | None:
        self._check()
        return self.policy

    def tag_rulesets(self, repo: str) -> tuple[TagRuleset, ...]:
        return self.rulesets

    def identity(self, repo: str) -> Identity:
        self._check()
        return self.approver

    def runs(self, repo: str, workflow: str, commit: str) -> tuple[WorkflowRun, ...]:
        self._check()
        return self.workflow_runs

    def pending_deployments(
        self, repo: str, run_id: int
    ) -> tuple[PendingDeployment, ...]:
        return self.pending

    def run_approvals(self, repo: str, run_id: int) -> tuple[RunApproval, ...]:
        self._check()
        return self.reviews

    def approve_deployment(
        self, repo: str, run_id: int, environment_id: int, comment: str
    ) -> ApprovalResult:
        self.approvals.append((run_id, environment_id))
        if self.approval_result is None:
            raise ApprovalAnswerLostError("lost")
        if self.approval_result == "approved":
            self.workflow_runs = (WorkflowRun("in_progress", None, "v0.13.0", run_id),)
        return self.approval_result


@dataclass
class FakeApp:
    """The release App: it tags, and it reports who it is."""

    github: FakeGitHub
    app_id: int = APP_ID
    slug: str = "zeo-release"
    tag_result: TagResult | None = "created"  # None: the answer is lost
    created: list[str] = field(default_factory=list)

    def identity(self) -> tuple[int, str]:
        return self.app_id, self.slug

    def create_tag(self, repo: str, tag: str, commit: str) -> TagResult:
        self.created.append(tag)
        if self.tag_result is None:
            raise TagAnswerLostError("lost")
        if self.tag_result == "created":
            self.github.tag = commit
        return self.tag_result


@dataclass
class FakePyPI:
    release: tuple[PyPIFile, ...] | None = None
    attested: dict[str, tuple[Provenance, ...] | None] = field(default_factory=dict)
    broken: bool = False

    def files(self, project: str, version: str) -> tuple[PyPIFile, ...] | None:
        if self.broken:
            raise TargetUnreadableError("PyPI could not be reached")
        return self.release

    def provenance(
        self, project: str, version: str, filename: str
    ) -> tuple[Provenance, ...] | None:
        return self.attested.get(filename)


WHEEL = PyPIFile(f"zeocore-{VERSION}-py3-none-any.whl", "1" * 64, False)
SDIST = PyPIFile(f"zeocore-{VERSION}.tar.gz", "2" * 64, False)


def _attested(item: PyPIFile, publisher: Mapping[str, Any] = PUBLISHER) -> Provenance:
    return Provenance(publisher, ((item.filename, item.sha256),))


def _published(**attested: tuple[Provenance, ...] | None) -> FakePyPI:
    default = {WHEEL.filename: (_attested(WHEEL),), SDIST.filename: (_attested(SDIST),)}
    return FakePyPI(release=(WHEEL, SDIST), attested={**default, **attested})


# -- plan ---------------------------------------------------------------------------


def test_plan_creates_the_tag_when_everything_agrees() -> None:
    outcome = plan(_manifest(), FakeGitHub(), FakePyPI())
    assert (outcome.status, outcome.reason) == ("done", "planned")
    assert outcome.facts == {"tag": "v0.13.0", "commit": COMMIT, "stage": "create-tag"}


def test_plan_sees_a_tag_already_at_the_commit_as_applied() -> None:
    outcome = plan(_manifest(), FakeGitHub(tag=COMMIT), FakePyPI())
    assert outcome.facts["stage"] == "already-applied"


@pytest.mark.parametrize(
    ("github", "pypi", "reason"),
    [
        (FakeGitHub(merged=False), FakePyPI(), "commit_unreviewed"),
        (FakeGitHub(files=_files_at("0.12.9")), FakePyPI(), "manifest_unreviewed"),
        (
            FakeGitHub(
                files={
                    **_files_at(),
                    "pyproject.toml": b'[project]\nversion = "0.12.9"\n',
                }
            ),
            FakePyPI(),
            "version_mismatch",
        ),
        (
            FakeGitHub(files={**_files_at(), "release.yaml": b"pypi: [1"}),
            FakePyPI(),
            "version_mismatch",
        ),
        (
            FakeGitHub(files={**_files_at(), "pyproject.toml": b"not toml ["}),
            FakePyPI(),
            "version_mismatch",
        ),
        (
            FakeGitHub(files={**_files_at(), "CHANGELOG.md": b"# nothing\n"}),
            FakePyPI(),
            "version_mismatch",
        ),
        (FakeGitHub(files={}), FakePyPI(), "workflow_missing"),
        (FakeGitHub(), _published(), "version_published"),
        (FakeGitHub(tag="b" * 40), FakePyPI(), "tag_mismatch"),
    ],
)
def test_plan_refuses(github: FakeGitHub, pypi: FakePyPI, reason: str) -> None:
    outcome = plan(_manifest(), github, pypi)
    assert (outcome.status, outcome.reason) == ("refused", reason)


@pytest.mark.parametrize(
    ("github", "pypi"),
    [(FakeGitHub(broken=True), FakePyPI()), (FakeGitHub(), FakePyPI(broken=True))],
)
def test_plan_holds_when_a_target_is_unreadable(
    github: FakeGitHub, pypi: FakePyPI
) -> None:
    outcome = plan(_manifest(), github, pypi)
    assert (outcome.status, outcome.reason) == ("held", "target_unreadable")


# -- admit --------------------------------------------------------------------------


def test_admit_when_only_the_two_delegated_identities_can_release() -> None:
    outcome = admit(_manifest(), FakeGitHub(), APP_ID)
    assert (outcome.status, outcome.reason) == ("done", "admitted")
    assert outcome.facts["release_app_id"] == APP_ID
    assert outcome.facts["tag_ruleset"] == 7


def _policy(**changes: object) -> EnvironmentPolicy:
    return replace(GOOD_POLICY, **changes)  # type: ignore[arg-type]


def _ruleset(**changes: object) -> TagRuleset:
    return replace(GOOD_RULESET, **changes)  # type: ignore[arg-type]


_BOTH = (("Integration", APP_ID, "always"), ("RepositoryRole", 5, "always"))


@pytest.mark.parametrize("bypass", [None, (), (("Integration", 1, "always"),), _BOTH])
def test_admit_doesnt_rely_on_the_bypass_list(
    bypass: tuple[tuple[str, int | None, str], ...] | None,
) -> None:
    # SOW-29 option C: hidden or not, the bypass list decides nothing.
    github = FakeGitHub(rulesets=(_ruleset(bypass=bypass),))
    assert admit(_manifest(), github, APP_ID).reason == "admitted"


@pytest.mark.parametrize(
    ("github", "reason"),
    [
        (FakeGitHub(policy=None), "promotion_unbound"),
        (FakeGitHub(policy=_policy(required_reviewers=None)), "promotion_unbound"),
        (
            FakeGitHub(policy=_policy(required_reviewers=frozenset())),
            "promotion_unbound",
        ),
        (
            FakeGitHub(
                policy=_policy(required_reviewers=frozenset({"example-reviewer"}))
            ),
            "promotion_unbound",
        ),
        (
            FakeGitHub(
                policy=_policy(required_reviewers=frozenset({"matorclawson", "x"}))
            ),
            "promotion_unbound",
        ),
        (FakeGitHub(policy=_policy(prevent_self_review=False)), "self_review_allowed"),
        (FakeGitHub(policy=_policy(can_admins_bypass=True)), "admin_bypass_possible"),
        (
            FakeGitHub(policy=_policy(custom_policies=None)),
            "deployment_policy_unrestricted",
        ),
        (
            FakeGitHub(policy=_policy(custom_policies=())),
            "deployment_policy_unrestricted",
        ),
        (
            FakeGitHub(
                policy=_policy(custom_policies=(("v*", "tag"), ("main", "branch")))
            ),
            "deployment_policy_unrestricted",
        ),
        (FakeGitHub(rulesets=()), "tag_ruleset_missing"),
        (
            FakeGitHub(rulesets=(_ruleset(enforcement="evaluate"),)),
            "tag_ruleset_missing",
        ),
        (
            FakeGitHub(rulesets=(_ruleset(includes=("refs/tags/release-*",)),)),
            "tag_ruleset_missing",
        ),
        (
            FakeGitHub(rulesets=(_ruleset(excludes=("refs/tags/v0*",)),)),
            "tag_ruleset_missing",
        ),
        (
            FakeGitHub(rulesets=(_ruleset(rules=frozenset({"creation"})),)),
            "tag_ruleset_missing",
        ),
        (
            FakeGitHub(rulesets=(_ruleset(current_user_can_bypass="always"),)),
            "approver_can_bypass",
        ),
        (
            FakeGitHub(
                rulesets=(_ruleset(current_user_can_bypass="pull_requests_only"),)
            ),
            "approver_can_bypass",
        ),
        (FakeGitHub(approver=Identity("example-reviewer", True)), "approver_mismatch"),
    ],
)
def test_admit_refuses_unless_only_the_delegated_pair_can_release(
    github: FakeGitHub, reason: str
) -> None:
    outcome = admit(_manifest(), github, APP_ID)
    assert (outcome.status, outcome.reason) == ("refused", reason)


def test_admit_needs_the_release_app_configured() -> None:
    assert admit(_manifest(), FakeGitHub(), None).reason == "release_app_unconfigured"


def test_admit_holds_when_github_is_unreadable() -> None:
    outcome = admit(_manifest(), FakeGitHub(broken=True), APP_ID)
    assert outcome.reason == "target_unreadable"


# -- apply ----------------------------------------------------------------------------


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[SigningJournal]:
    opened = SigningJournal(tmp_path / "journal.jsonl")
    yield opened
    opened.close()


def test_apply_creates_the_tag_once_as_the_release_app(journal: SigningJournal) -> None:
    github = FakeGitHub()
    app = FakeApp(github)
    first = apply(_manifest(), github, FakePyPI(), app, journal)
    again = apply(_manifest(), github, FakePyPI(), app, journal)
    assert (first.status, first.reason) == ("done", "tag_created")
    assert (again.status, again.reason) == ("done", "already_applied")
    assert app.created == ["v0.13.0"]


@pytest.mark.parametrize(
    ("app_id", "slug"), [(APP_ID, "someone-else"), (1, "zeo-release")]
)
def test_apply_refuses_a_key_that_isnt_the_release_apps(
    journal: SigningJournal, app_id: int, slug: str
) -> None:
    github = FakeGitHub()

    class Other(FakeApp):
        def identity(self) -> tuple[int, str]:
            return app_id, slug

    app = Other(github)
    outcome = apply(_manifest(), github, FakePyPI(), app, journal)
    assert (outcome.status, outcome.reason) == ("refused", "creator_not_release_app")
    assert app.created == []


def test_apply_holds_when_the_app_cant_say_who_it_is(journal: SigningJournal) -> None:
    class Unsigned(FakeApp):
        def identity(self) -> tuple[int, str]:
            raise TargetUnreadableError("the Keychain is locked")

    github = FakeGitHub()
    outcome = apply(_manifest(), github, FakePyPI(), Unsigned(github), journal)
    assert (outcome.status, outcome.reason) == ("held", "target_unreadable")


def test_apply_refuses_before_any_effect(journal: SigningJournal) -> None:
    for github in (
        FakeGitHub(merged=False),
        FakeGitHub(policy=_policy(can_admins_bypass=True)),
    ):
        app = FakeApp(github)
        outcome = apply(_manifest(), github, FakePyPI(), app, journal)
        assert outcome.status == "refused"
        assert app.created == []


def test_a_lost_answer_is_ambiguous_and_never_retried(journal: SigningJournal) -> None:
    github = FakeGitHub()
    app = FakeApp(github, tag_result=None)
    lost = apply(_manifest(), github, FakePyPI(), app, journal)
    assert (lost.status, lost.reason) == ("ambiguous", "apply_unreconciled")
    app.tag_result = "created"
    again = apply(_manifest(), github, FakePyPI(), app, journal)
    assert (again.status, again.reason) == ("ambiguous", "apply_unreconciled")
    assert app.created == ["v0.13.0"]


def test_a_lost_answer_that_landed_is_settled_by_readback(
    journal: SigningJournal,
) -> None:
    github = FakeGitHub()
    app = FakeApp(github, tag_result=None)
    apply(_manifest(), github, FakePyPI(), app, journal)
    github.tag = COMMIT  # the tag did land
    outcome = apply(_manifest(), github, FakePyPI(), app, journal)
    assert outcome.reason == "already_applied"
    assert not journal.is_open(f"pypi:{REPO}:0.13.0")


@pytest.mark.parametrize("result", ["not_sent", "rejected"])
def test_a_definite_failure_closes_the_intent(
    journal: SigningJournal, result: TagResult
) -> None:
    github = FakeGitHub()
    app = FakeApp(github, tag_result=result)
    outcome = apply(_manifest(), github, FakePyPI(), app, journal)
    assert (outcome.status, outcome.reason) == ("held", "tag_not_created")
    app.tag_result = "created"
    assert apply(_manifest(), github, FakePyPI(), app, journal).reason == "tag_created"


class Racing(FakeApp):
    """GitHub answers 422: something already made the tag, somewhere."""

    race: str | None = None

    def create_tag(self, repo: str, tag: str, commit: str) -> TagResult:
        self.github.tag = self.race
        return "exists"


def test_an_existing_ref_at_the_commit_counts_as_created(
    journal: SigningJournal,
) -> None:
    github = FakeGitHub()
    landed = Racing(github)
    landed.race = COMMIT
    assert (
        apply(_manifest(), github, FakePyPI(), landed, journal).reason == "tag_created"
    )


def test_an_existing_ref_elsewhere_is_not_created(journal: SigningJournal) -> None:
    github = FakeGitHub()
    elsewhere = Racing(github)
    elsewhere.race = "b" * 40
    outcome = apply(_manifest(), github, FakePyPI(), elsewhere, journal)
    assert outcome.reason == "tag_not_created"


def test_an_existing_ref_that_cant_be_read_is_ambiguous(
    journal: SigningJournal,
) -> None:
    class Unreadable(FakeGitHub):
        calls = 0

        def tag_commit(self, repo: str, tag: str) -> str | None:
            self.calls += 1
            if self.calls > 1:
                raise TargetUnreadableError("down")
            return None

    class Exists(FakeApp):
        def create_tag(self, repo: str, tag: str, commit: str) -> TagResult:
            return "exists"

    github = Unreadable()
    outcome = apply(_manifest(), github, FakePyPI(), Exists(github), journal)
    assert outcome.status == "ambiguous"


def test_an_open_intent_is_settled_before_admit_can_hide_it(
    journal: SigningJournal,
) -> None:
    github = FakeGitHub()
    app = FakeApp(github, tag_result=None)
    apply(_manifest(), github, FakePyPI(), app, journal)
    github.policy = _policy(can_admins_bypass=True)
    outcome = apply(_manifest(), github, FakePyPI(), app, journal)
    assert (outcome.status, outcome.reason) == ("ambiguous", "apply_unreconciled")


def test_one_release_one_key_across_commits(journal: SigningJournal) -> None:
    github = FakeGitHub()
    app = FakeApp(github, tag_result=None)
    apply(_manifest(), github, FakePyPI(), app, journal)
    other = apply(_manifest(commit="c" * 40), github, FakePyPI(), app, journal)
    assert (other.status, other.reason) == ("ambiguous", "apply_unreconciled")


# -- approve --------------------------------------------------------------------------


def _tagged(**changes: object) -> FakeGitHub:
    github = FakeGitHub(tag=COMMIT, workflow_runs=(WAITING,))
    for name, value in changes.items():
        setattr(github, name, value)
    return github


def test_approve_once_for_the_exact_run_and_environment(
    journal: SigningJournal,
) -> None:
    github = _tagged()
    first = approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    again = approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    assert (first.status, first.reason) == ("done", "approved")
    assert first.facts == {"run_id": 99, "environment_id": 555}
    assert (again.status, again.reason) == ("done", "already_approved")
    assert github.approvals == [(99, 555)]


_TESTPYPI = PendingDeployment(556, "testpypi", True)


@pytest.mark.parametrize(
    ("changes", "status", "reason"),
    [
        ({"tag": None}, "held", "tag_absent"),
        ({"tag": "b" * 40}, "refused", "tag_mismatch"),
        ({"merged": False}, "refused", "commit_unreviewed"),
        (
            {"policy": _policy(can_admins_bypass=True)},
            "refused",
            "admin_bypass_possible",
        ),
        ({"workflow_runs": ()}, "waiting", "publish_pending"),
        (
            {"workflow_runs": (WorkflowRun("waiting", None, "main", 98),)},
            "waiting",
            "publish_pending",
        ),
        (
            {"workflow_runs": (WAITING, replace(WAITING, id=100))},
            "refused",
            "approval_unexpected",
        ),
        (
            {"workflow_runs": (replace(WAITING, id=None),)},
            "refused",
            "approval_unexpected",
        ),
        ({"pending": ()}, "refused", "approval_unexpected"),
        ({"pending": (_TESTPYPI,)}, "refused", "approval_unexpected"),
        ({"pending": (*PENDING, _TESTPYPI)}, "refused", "approval_unexpected"),
        (
            {"pending": (PendingDeployment(555, "pypi", False),)},
            "refused",
            "approver_cannot_approve",
        ),
    ],
)
def test_approve_refuses_anything_but_the_one_waiting_run(
    journal: SigningJournal, changes: dict[str, object], status: str, reason: str
) -> None:
    github = _tagged(**changes)
    outcome = approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    assert (outcome.status, outcome.reason) == (status, reason)
    assert github.approvals == []


def test_approve_refuses_when_pypi_already_has_the_version(
    journal: SigningJournal,
) -> None:
    outcome = approve(_manifest(), _tagged(), _published(), APP_ID, journal)
    assert outcome.reason == "version_published"


def test_approve_holds_when_github_is_unreadable(journal: SigningJournal) -> None:
    class Broken(FakeGitHub):
        def pending_deployments(
            self, repo: str, run_id: int
        ) -> tuple[PendingDeployment, ...]:
            raise TargetUnreadableError("down")

    github = Broken(tag=COMMIT, workflow_runs=(WAITING,))
    outcome = approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    assert (outcome.status, outcome.reason) == ("held", "target_unreadable")


def test_a_lost_approval_is_ambiguous_and_never_resent(journal: SigningJournal) -> None:
    github = _tagged(approval_result=None)
    lost = approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    assert (lost.status, lost.reason) == ("ambiguous", "approve_unreconciled")
    github.approval_result = "approved"
    again = approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    assert (again.status, again.reason) == ("ambiguous", "approve_unreconciled")
    assert github.approvals == [(99, 555)]


def _lost_then(
    journal: SigningJournal, *reviews: RunApproval
) -> tuple[FakeGitHub, Outcome]:
    github = _tagged(approval_result=None)
    approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    github.workflow_runs = (WorkflowRun("in_progress", None, "v0.13.0", 99),)
    github.reviews = reviews
    return github, approve(_manifest(), github, FakePyPI(), APP_ID, journal)


def test_a_lost_approval_is_settled_only_by_the_approvers_recorded_review(
    journal: SigningJournal,
) -> None:
    github, outcome = _lost_then(
        journal, RunApproval("approved", "matorclawson", frozenset({"pypi"}))
    )
    assert (outcome.status, outcome.reason) == ("done", "already_approved")
    assert github.approvals == [(99, 555)]


@pytest.mark.parametrize(
    "reviews",
    [
        (),
        (RunApproval("approved", "someone-else", frozenset({"pypi"})),),
        (RunApproval("approved", "matorclawson", frozenset({"testpypi"})),),
    ],
)
def test_a_run_that_moved_on_without_our_approval_stays_ambiguous(
    journal: SigningJournal, reviews: tuple[RunApproval, ...]
) -> None:
    _, outcome = _lost_then(journal, *reviews)
    assert (outcome.status, outcome.reason) == ("ambiguous", "approve_unreconciled")


def test_a_recorded_rejection_closes_the_intent(journal: SigningJournal) -> None:
    _, outcome = _lost_then(
        journal, RunApproval("rejected", "matorclawson", frozenset({"pypi"}))
    )
    assert (outcome.status, outcome.reason) == ("held", "approval_rejected")
    assert not journal.is_open(f"approve:{REPO}:0.13.0")


def test_unreadable_reviews_stay_ambiguous(journal: SigningJournal) -> None:
    class Broken(FakeGitHub):
        def run_approvals(self, repo: str, run_id: int) -> tuple[RunApproval, ...]:
            raise TargetUnreadableError("down")

    github = Broken(tag=COMMIT, workflow_runs=(WAITING,), approval_result=None)
    approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    github.workflow_runs = (WorkflowRun("in_progress", None, "v0.13.0", 99),)
    outcome = approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    assert outcome.reason == "approve_unreconciled"


def test_a_run_that_isnt_waiting_is_held_not_ok(journal: SigningJournal) -> None:
    github = _tagged(workflow_runs=(WorkflowRun("in_progress", None, "v0.13.0", 99),))
    outcome = approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    assert (outcome.status, outcome.reason) == ("held", "run_not_waiting")
    assert outcome.facts == {"run_ids": [99]}
    assert github.approvals == []


def test_plan_refuses_a_commit_without_the_workflow() -> None:
    files = {k: v for k, v in _files_at().items() if not k.startswith(".github/")}
    outcome = plan(_manifest(), FakeGitHub(files=files), FakePyPI())
    assert (outcome.status, outcome.reason) == ("refused", "workflow_missing")


def test_github_reads_run_approvals() -> None:
    answer = [
        {
            "state": "approved",
            "user": {"login": "matorclawson"},
            "environments": [{"name": "pypi"}],
        }
    ]
    runner = Runner(
        ok(answer),
        ok([{"state": "approved", "user": {}}]),
        ok([{"state": "approved", "user": {"login": "x"}, "environments": ["pypi"]}]),
    )
    gh = GitHubCli(runner)
    assert gh.run_approvals(REPO, 99) == (
        RunApproval("approved", "matorclawson", frozenset({"pypi"})),
    )
    assert runner.calls[0][2] == f"repos/{REPO}/actions/runs/99/approvals"
    for _ in range(2):
        with pytest.raises(TargetUnreadableError):
            gh.run_approvals(REPO, 99)


@pytest.mark.parametrize("result", ["rejected", "not_sent"])
def test_a_refused_approval_closes_the_intent(
    journal: SigningJournal, result: ApprovalResult
) -> None:
    github = _tagged(approval_result=result)
    outcome = approve(_manifest(), github, FakePyPI(), APP_ID, journal)
    assert (outcome.status, outcome.reason) == ("held", "approval_not_sent")
    github.approval_result = "approved"
    assert (
        approve(_manifest(), github, FakePyPI(), APP_ID, journal).reason == "approved"
    )


# -- reconcile ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("github", "status", "reason"),
    [
        (FakeGitHub(), "held", "tag_absent"),
        (FakeGitHub(tag="b" * 40), "refused", "tag_mismatch"),
        (FakeGitHub(tag=COMMIT), "waiting", "publish_pending"),
        (
            FakeGitHub(
                tag=COMMIT, workflow_runs=(WorkflowRun("waiting", None, "v0.13.0"),)
            ),
            "waiting",
            "awaiting_approval",
        ),
        (
            FakeGitHub(
                tag=COMMIT, workflow_runs=(WorkflowRun("in_progress", None, "v0.13.0"),)
            ),
            "waiting",
            "publish_running",
        ),
        (
            FakeGitHub(
                tag=COMMIT,
                workflow_runs=(WorkflowRun("completed", "failure", "v0.13.0"),),
            ),
            "held",
            "publish_failed",
        ),
        (
            FakeGitHub(
                tag=COMMIT,
                workflow_runs=(WorkflowRun("completed", "success", "v0.13.0"),),
            ),
            "done",
            "published",
        ),
        (FakeGitHub(broken=True), "held", "target_unreadable"),
    ],
)
def test_reconcile_reads_the_tag_then_the_run(
    github: FakeGitHub, status: str, reason: str
) -> None:
    outcome = reconcile(_manifest(), github)
    assert (outcome.status, outcome.reason) == (status, reason)


# -- verify -------------------------------------------------------------------------


def test_verify_records_the_publisher_for_every_file() -> None:
    outcome = verify(_manifest(), _published())
    assert (outcome.status, outcome.reason) == ("done", "publisher_recorded")
    assert "not verified" in outcome.message
    assert [f["filename"] for f in outcome.facts["files"]] == [
        WHEEL.filename,
        SDIST.filename,
    ]


@pytest.mark.parametrize(
    "pypi",
    [
        FakePyPI(release=(WHEEL,), attested={WHEEL.filename: (_attested(WHEEL),)}),
        FakePyPI(release=(SDIST,), attested={SDIST.filename: (_attested(SDIST),)}),
        FakePyPI(
            release=(PyPIFile(WHEEL.filename, "1" * 64, True), SDIST),
            attested={
                WHEEL.filename: (_attested(WHEEL),),
                SDIST.filename: (_attested(SDIST),),
            },
        ),
        _published(**{WHEEL.filename: None}),
        _published(**{WHEEL.filename: ()}),
        _published(
            **{SDIST.filename: (_attested(SDIST, {**PUBLISHER, "workflow": "x.yml"}),)}
        ),
        _published(
            **{
                SDIST.filename: (
                    _attested(SDIST, {**PUBLISHER, "repository": "fork/z"}),
                )
            }
        ),
        _published(
            **{
                SDIST.filename: (
                    _attested(SDIST),
                    _attested(SDIST, {**PUBLISHER, "environment": "testpypi"}),
                )
            }
        ),
        # The publisher is right, but the attestation is for other bytes.
        _published(
            **{SDIST.filename: (Provenance(PUBLISHER, ((SDIST.filename, "9" * 64),)),)}
        ),
        _published(
            **{
                SDIST.filename: (
                    Provenance(PUBLISHER, ((WHEEL.filename, WHEEL.sha256),)),
                )
            }
        ),
        _published(
            **{
                SDIST.filename: (
                    Provenance(
                        PUBLISHER,
                        (
                            (SDIST.filename, SDIST.sha256),
                            (WHEEL.filename, WHEEL.sha256),
                        ),
                    ),
                )
            }
        ),
    ],
)
def test_verify_refuses_anything_but_the_expected_publisher(pypi: FakePyPI) -> None:
    outcome = verify(_manifest(), pypi)
    assert (outcome.status, outcome.reason) == ("refused", "provenance_mismatch")


@pytest.mark.parametrize(
    ("pypi", "reason"),
    [(FakePyPI(), "version_absent"), (FakePyPI(broken=True), "target_unreadable")],
)
def test_verify_holds_without_a_release(pypi: FakePyPI, reason: str) -> None:
    outcome = verify(_manifest(), pypi)
    assert (outcome.status, outcome.reason) == ("held", reason)


# -- the manifest -------------------------------------------------------------------


def _text(**changes: object) -> str:
    section = {
        "project": "zeocore",
        "version": "0.13.0",
        "repository": REPO,
        "commit": COMMIT,
        "workflow": "publish.yml",
        "environment": "pypi",
        **changes,
    }
    return yaml.safe_dump({"pypi": section, "signing": {}})


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "release.yaml"
    path.write_text(text)
    return path


def test_the_manifest_loads(tmp_path: Path) -> None:
    text = _text()
    assert load(_write(tmp_path, text)).tag == "v0.13.0"


@pytest.mark.parametrize(
    "text",
    [
        "{not yaml",
        "[]",
        "signing: {}\n",
        "pypi:\n  project: zeocore\n",
        _text(version="v1"),
    ],
)
def test_a_bad_manifest_is_invalid(tmp_path: Path, text: str) -> None:
    with pytest.raises(ManifestError):
        load(_write(tmp_path, text))
    with pytest.raises(ManifestError, match="can't be read"):
        load(tmp_path / "absent.yaml")


# -- the command ----------------------------------------------------------------------


def _command(
    github: FakeGitHub,
    pypi: FakePyPI,
    environ: dict[str, str] | None = None,
    app: FakeApp | None = None,
) -> Callable[[Sequence[str], bytes], tuple[int, dict[str, Any]]]:
    return make_command(
        environ={"ZEOCORE_RELEASE_APP_ID": str(APP_ID)} if environ is None else environ,
        pypi_reads=lambda: (github, pypi),  # type: ignore[arg-type,return-value]
        release_app=lambda _id, _env: app or FakeApp(github),
    )


def _manifest_file(tmp_path: Path) -> bytes:
    text = _text()
    return json.dumps({"manifest": str(_write(tmp_path, text))}).encode()


@pytest.mark.parametrize(
    ("stage", "github", "pypi", "status", "outcome"),
    [
        ("plan", FakeGitHub(), FakePyPI(), 0, "planned"),
        ("admit", FakeGitHub(policy=None), FakePyPI(), 20, "promotion_unbound"),
        (
            "reconcile",
            FakeGitHub(
                tag=COMMIT, workflow_runs=(WorkflowRun("waiting", None, "v0.13.0"),)
            ),
            FakePyPI(),
            11,
            "awaiting_approval",
        ),
        ("verify", FakeGitHub(), _published(), 0, "publisher_recorded"),
    ],
)
def test_the_pypi_command(
    tmp_path: Path,
    stage: str,
    github: FakeGitHub,
    pypi: FakePyPI,
    status: int,
    outcome: str,
) -> None:
    got, answer = _command(github, pypi)(["pypi", stage], _manifest_file(tmp_path))
    assert (got, answer["outcome"], answer["target"]) == (
        status,
        outcome,
        "pypi:zeocore",
    )


@pytest.mark.parametrize(
    ("args", "stdin"),
    [
        (["pypi"], b"{}"),
        (["pypi", "apply"], b"{}"),
        (["pypi", "plan"], b"not json"),
        (["pypi", "plan"], b'{"manifest": 3}'),
        (["pypi", "plan"], b'{"manifest": "/absent.yaml"}'),
    ],
)
def test_the_pypi_command_refuses_a_bad_request(args: list[str], stdin: bytes) -> None:
    status, answer = _command(FakeGitHub(), FakePyPI())(args, stdin)
    assert (status, answer["outcome"]) == (2, "invalid_request")


# -- the real readers, with fake transports -------------------------------------


class Runner:
    def __init__(self, *answers: Completed | BaseException) -> None:
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> Completed:
        self.calls.append(list(argv))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def ok(body: object) -> Completed:
    return Completed(0, json.dumps(body).encode(), b"")


MISSING = Completed(1, b"", b"gh: Not Found (HTTP 404)")
BROKEN = Completed(1, b"", b"HTTP 502")


def test_github_reads_a_lightweight_and_an_annotated_tag() -> None:
    gh = GitHubCli(
        Runner(
            ok({"object": {"type": "commit", "sha": COMMIT}}),
            ok({"object": {"type": "tag", "sha": "t" * 40}}),
            ok({"object": {"type": "commit", "sha": "c" * 40}}),
            MISSING,
        )
    )
    assert gh.tag_commit(REPO, "v1") == COMMIT
    assert gh.tag_commit(REPO, "v2") == "c" * 40
    assert gh.tag_commit(REPO, "v3") is None


@pytest.mark.parametrize(
    "answers",
    [
        (ok({"object": "nope"}),),
        (ok({"object": {"type": "tree", "sha": "x"}}),),
        (ok({"object": {"type": "tag", "sha": "t"}}), MISSING),
        tuple(ok({"object": {"type": "tag", "sha": "t"}}) for _ in range(5)),
        (BROKEN,),
    ],
)
def test_github_unreadable_tags(answers: tuple[Completed, ...]) -> None:
    with pytest.raises(TargetUnreadableError):
        GitHubCli(Runner(*answers)).tag_commit(REPO, "v1")


def test_github_reads_an_environment_policy() -> None:
    runner = Runner(
        ok(
            {
                "can_admins_bypass": False,
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                },
                "protection_rules": [
                    {
                        "type": "required_reviewers",
                        "prevent_self_review": True,
                        "reviewers": [
                            {"reviewer": {"login": "matorclawson"}},
                        ],
                    }
                ],
            }
        ),
        ok({"total_count": 1, "branch_policies": [{"name": "v*", "type": "tag"}]}),
        ok({"protection_rules": [{"type": "wait_timer"}]}),
        MISSING,
    )
    gh = GitHubCli(runner)
    assert gh.environment(REPO, "pypi") == GOOD_POLICY
    assert (
        "environments/pypi/deployment-branch-policies?per_page=100&page=1"
        in (runner.calls[1][2])
    )
    assert gh.environment(REPO, "pypi") == EnvironmentPolicy(None, False, True, None)
    assert gh.environment(REPO, "absent") is None


_CUSTOM = {
    "deployment_branch_policy": {
        "protected_branches": False,
        "custom_branch_policies": True,
    },
    "protection_rules": [
        {
            "type": "required_reviewers",
            "reviewers": [{"reviewer": {"login": "matorclawson"}}],
        }
    ],
}


def test_deployment_policies_are_read_across_pages() -> None:
    first = [{"name": "v*", "type": "tag"}] * 100
    runner = Runner(
        ok(_CUSTOM),
        ok({"total_count": 101, "branch_policies": first}),
        ok(
            {
                "total_count": 101,
                "branch_policies": [{"name": "main", "type": "branch"}],
            }
        ),
    )
    policy = GitHubCli(runner).environment(REPO, "pypi")
    assert policy is not None and policy.custom_policies is not None
    assert len(policy.custom_policies) == 101
    assert ("main", "branch") in policy.custom_policies
    assert runner.calls[2][2].endswith("page=2")


@pytest.mark.parametrize(
    "pages",
    [
        # A short read: GitHub says 31, the pages hold 30, then nothing.
        (
            ok(
                {
                    "total_count": 31,
                    "branch_policies": [{"name": "v*", "type": "tag"}] * 30,
                }
            ),
            ok({"total_count": 31, "branch_policies": []}),
        ),
        (ok({"branch_policies": []}),),
        (ok({"total_count": 1, "branch_policies": "v*"}),),
        (ok({"total_count": 1, "branch_policies": ["v*"]}),),
        (MISSING,),
    ],
)
def test_a_policy_list_that_isnt_read_in_full_is_unreadable(
    pages: tuple[Completed, ...],
) -> None:
    with pytest.raises(TargetUnreadableError):
        GitHubCli(Runner(ok(_CUSTOM), *pages)).environment(REPO, "pypi")


@pytest.mark.parametrize(
    "rules",
    [
        {"protection_rules": "required_reviewers"},
        {
            "protection_rules": [
                {"type": "required_reviewers", "reviewers": "example-reviewer"}
            ]
        },
    ],
)
def test_a_scalar_where_a_list_belongs_is_unreadable(rules: dict[str, object]) -> None:
    with pytest.raises(TargetUnreadableError):
        GitHubCli(Runner(ok(rules))).environment(REPO, "pypi")


def test_github_reads_the_acting_identity() -> None:
    gh = GitHubCli(
        Runner(
            ok({"login": "matorclawson"}),
            ok({"permissions": {"admin": True}}),
            ok({"login": 3}),
            ok({}),
        )
    )
    assert gh.identity(REPO) == Identity("matorclawson", True)
    with pytest.raises(TargetUnreadableError):
        gh.identity(REPO)


def test_github_reads_runs_and_wraps_other_reads() -> None:
    run = {"status": "completed", "conclusion": "success", "head_branch": "v0.13.0"}
    runner = Runner(
        ok({"workflow_runs": [run]}),
        ok({"workflow_runs": [{"status": "completed"}]}),
        BROKEN,
        BROKEN,
        BROKEN,
    )
    gh = GitHubCli(runner)
    assert gh.runs(REPO, "publish.yml", COMMIT) == (
        WorkflowRun("completed", "success", "v0.13.0"),
    )
    assert "head_sha=" + COMMIT in runner.calls[0][2]
    assert gh.runs(REPO, "publish.yml", COMMIT) == (
        WorkflowRun("completed", None, None),
    )
    for read in (
        lambda: gh.default_branch(REPO),
        lambda: gh.is_ancestor(REPO, COMMIT, "main"),
        lambda: gh.file_at(REPO, COMMIT, "release.yaml"),
    ):
        with pytest.raises(TargetUnreadableError):
            read()


@pytest.mark.parametrize(
    "answer",
    [
        MISSING,  # no such workflow: never "waiting" forever
        ok({"workflow_runs": [{"status": 3}]}),
        ok({"workflow_runs": ["x"]}),
    ],
)
def test_github_unreadable_runs(answer: Completed) -> None:
    with pytest.raises(TargetUnreadableError):
        GitHubCli(Runner(answer)).runs(REPO, "publish.yml", COMMIT)


@pytest.mark.parametrize("entry", [{"reviewer": {}}, {"reviewer": "x"}, "x"])
def test_an_unreadable_reviewer_is_never_assumed(entry: object) -> None:
    answer = ok(
        {"protection_rules": [{"type": "required_reviewers", "reviewers": [entry]}]}
    )
    with pytest.raises(TargetUnreadableError):
        GitHubCli(Runner(answer)).environment(REPO, "pypi")


def test_a_non_dict_publisher_is_unreadable() -> None:
    bundle = _bundle(PUBLISHER, WHEEL)
    bundle["attestation_bundles"][0]["publisher"] = "GitHub"
    with pytest.raises(TargetUnreadableError):
        PyPIHttp(lambda u, h: _http(200, bundle)).provenance("zeocore", VERSION, "x")


def test_reconcile_ignores_runs_for_other_refs() -> None:
    github = FakeGitHub(
        tag=COMMIT, workflow_runs=(WorkflowRun("completed", "success", "main"),)
    )
    outcome = reconcile(_manifest(), github)
    assert (outcome.status, outcome.reason) == ("waiting", "publish_pending")


@pytest.mark.parametrize(
    "changes",
    [
        {"repository": "fork/zeocore"},
        {"workflow": "other.yml"},
        {"environment": "testpypi"},
        {"project": "other"},
    ],
)
def test_the_target_is_pinned(changes: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="reviewed target"):
        _manifest(**changes)


def _release_at(section: dict[str, str]) -> FakeGitHub:
    content = yaml.safe_dump({"pypi": section}).encode()
    return FakeGitHub(files={**_files_at(), "release.yaml": content})


def test_a_local_section_unlike_the_committed_one_is_unreviewed() -> None:
    # The committed file names another version; locally it says 0.13.0.
    outcome = plan(_manifest(), _release_at(_committed("0.13.5")), FakePyPI())
    assert (outcome.status, outcome.reason) == ("refused", "manifest_unreviewed")


def test_a_committed_section_naming_a_commit_is_not_accepted() -> None:
    outcome = plan(_manifest(), _release_at(_section()), FakePyPI())
    assert (outcome.status, outcome.reason) == ("refused", "version_mismatch")


def _http(status: int, body: object) -> httpx.Response:
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return httpx.Response(status, content=content)


def _bundle(publisher: Mapping[str, Any], item: PyPIFile) -> dict[str, Any]:
    statement = {
        "subject": [{"name": item.filename, "digest": {"sha256": item.sha256}}]
    }
    encoded = base64.b64encode(json.dumps(statement).encode()).decode()
    return {
        "attestation_bundles": [
            {
                "publisher": publisher,
                "attestations": [{"envelope": {"statement": encoded}}],
            }
        ]
    }


def test_pypi_reads_files_and_provenance() -> None:
    urls: list[str] = []
    answers = [
        _http(
            200,
            {
                "urls": [
                    {
                        "filename": WHEEL.filename,
                        "digests": {"sha256": "1" * 64},
                        "yanked": False,
                    }
                ]
            },
        ),
        _http(200, _bundle(PUBLISHER, WHEEL)),
        _http(404, b""),
    ]

    def get(url: str, headers: Mapping[str, str]) -> httpx.Response:
        urls.append(url)
        return answers.pop(0)

    index = PyPIHttp(get)
    assert index.files("zeocore", VERSION) == (WHEEL,)
    assert index.provenance("zeocore", VERSION, WHEEL.filename) == (_attested(WHEEL),)
    assert index.files("zeocore", "9.9.9") is None
    assert urls[0] == f"https://pypi.org/pypi/zeocore/{VERSION}/json"
    assert urls[1].startswith("https://pypi.org/integrity/zeocore/")


@pytest.mark.parametrize(
    "response",
    [
        _http(500, b""),
        _http(200, b"not json"),
        _http(200, {"urls": [{"filename": "x"}]}),
        httpx.ConnectError("down"),
    ],
)
def test_pypi_unreadable_answers(response: httpx.Response | Exception) -> None:
    def get(url: str, headers: Mapping[str, str]) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    with pytest.raises(TargetUnreadableError):
        PyPIHttp(get).files("zeocore", VERSION)


def test_pypi_unreadable_provenance() -> None:
    def get(url: str, headers: Mapping[str, str]) -> httpx.Response:
        return _http(200, {"attestation_bundles": [{}]})

    with pytest.raises(TargetUnreadableError):
        PyPIHttp(get).provenance("zeocore", VERSION, WHEEL.filename)
    assert (
        PyPIHttp(lambda u, h: _http(404, b"")).provenance("zeocore", VERSION, "x")
        is None
    )
    bad_statement = {
        "attestation_bundles": [
            {
                "publisher": PUBLISHER,
                "attestations": [{"envelope": {"statement": "!!"}}],
            }
        ]
    }
    with pytest.raises(TargetUnreadableError):
        PyPIHttp(lambda u, h: _http(200, bad_statement)).provenance(
            "zeocore", VERSION, "x"
        )
    with pytest.raises(TargetUnreadableError):
        PyPIHttp(lambda u, h: _http(200, [1])).files("zeocore", VERSION)


# -- the command: apply and approve -----------------------------------------


def test_the_apply_and_approve_commands_journal_in_the_users_state(
    tmp_path: Path,
) -> None:
    github = FakeGitHub()
    environ = {
        "ZEOCORE_RELEASE_APP_ID": str(APP_ID),
        "XDG_STATE_HOME": str(tmp_path / "s"),
    }
    command = _command(github, FakePyPI(), environ)
    status, answer = command(["pypi", "apply"], _manifest_file(tmp_path))
    assert (status, answer["outcome"]) == (0, "tag_created")
    github.workflow_runs = (WAITING,)
    status, answer = command(["pypi", "approve"], _manifest_file(tmp_path))
    assert (status, answer["outcome"]) == (0, "approved")
    journal = tmp_path / "s" / "zeocore" / "release" / "pypi-journal.jsonl"
    text = journal.read_text()
    assert f"pypi:{REPO}:0.13.0" in text and f"approve:{REPO}:0.13.0" in text
    journal.write_text("not json\n")
    status, answer = command(["pypi", "apply"], _manifest_file(tmp_path))
    assert (status, answer["outcome"]) == (20, "journal_unreadable")


def test_apply_needs_the_release_app_id(tmp_path: Path) -> None:
    status, answer = _command(FakeGitHub(), FakePyPI(), {})(
        ["pypi", "apply"], _manifest_file(tmp_path)
    )
    assert (status, answer["outcome"]) == (20, "release_app_unconfigured")


@pytest.mark.parametrize("value", ["zero", "-3", "0"])
def test_a_bad_release_app_id_is_invalid(tmp_path: Path, value: str) -> None:
    status, answer = _command(
        FakeGitHub(), FakePyPI(), {"ZEOCORE_RELEASE_APP_ID": value}
    )(["pypi", "admit"], _manifest_file(tmp_path))
    assert (status, answer["outcome"]) == (2, "invalid_request")


def test_another_run_holding_the_journal_is_held(tmp_path: Path) -> None:
    environ = {
        "ZEOCORE_RELEASE_APP_ID": str(APP_ID),
        "XDG_STATE_HOME": str(tmp_path / "s"),
    }
    path = tmp_path / "s" / "zeocore" / "release" / "pypi-journal.jsonl"
    path.parent.mkdir(parents=True)
    holder = SigningJournal(path)
    try:
        status, answer = _command(FakeGitHub(), FakePyPI(), environ)(
            ["pypi", "apply"], _manifest_file(tmp_path)
        )
    finally:
        holder.close()
    assert (status, answer["outcome"]) == (20, "apply_in_progress")


# -- the new GitHub reads -------------------------------------------------------


def _ruleset_detail(**changes: object) -> dict[str, object]:
    detail: dict[str, object] = {
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}],
        "bypass_actors": [
            {"actor_id": APP_ID, "actor_type": "Integration", "bypass_mode": "always"}
        ],
        "current_user_can_bypass": "never",
    }
    detail.update(changes)
    return detail


def test_github_reads_tag_rulesets() -> None:
    runner = Runner(
        ok([{"id": 1, "target": "branch"}, {"id": 7, "target": "tag"}]),
        ok(_ruleset_detail()),
    )
    rulesets = GitHubCli(runner).tag_rulesets(REPO)
    assert rulesets == (
        replace(GOOD_RULESET, bypass=(("Integration", APP_ID, "always"),)),
    )
    assert runner.calls[0][2].endswith("rulesets?per_page=100&includes_parents=true")


@pytest.mark.parametrize(
    "answers",
    [
        (ok({"not": "a list"}),),
        (ok(["x"]),),
        (ok([{"id": 7, "target": "tag"}]), MISSING),
        (ok([{"id": 7, "target": "tag"}]), ok(_ruleset_detail(rules=[{"type": 3}]))),
        (ok([{"id": 7, "target": "tag"}]), ok(_ruleset_detail(bypass_actors=["x"]))),
        (ok([{"id": 7, "target": "tag"}]), ok(_ruleset_detail(enforcement=None))),
        (ok([{"id": 7, "target": "tag"}]), ok(_ruleset_detail(rules="creation"))),
        (ok([{"id": n, "target": "branch"} for n in range(100)]),),
        (Completed(0, b"not json", b""),),
    ],
)
def test_unreadable_rulesets(answers: tuple[Completed, ...]) -> None:
    with pytest.raises(TargetUnreadableError):
        GitHubCli(Runner(*answers)).tag_rulesets(REPO)


def test_a_ruleset_without_ref_conditions_protects_nothing() -> None:
    runner = Runner(
        ok([{"id": 7, "target": "tag"}]),
        ok(
            _ruleset_detail(
                conditions=None, bypass_actors=[{"actor_type": "Integration"}]
            )
        ),
    )
    (ruleset,) = GitHubCli(runner).tag_rulesets(REPO)
    assert ruleset.includes == () and ruleset.bypass == (
        ("Integration", None, "always"),
    )


def test_github_reads_pending_deployments() -> None:
    answer = [
        {"environment": {"id": 555, "name": "pypi"}, "current_user_can_approve": True}
    ]
    runner = Runner(ok(answer), ok([{"environment": "pypi"}]))
    gh = GitHubCli(runner)
    assert gh.pending_deployments(REPO, 99) == PENDING
    assert runner.calls[0][2] == f"repos/{REPO}/actions/runs/99/pending_deployments"
    with pytest.raises(TargetUnreadableError):
        gh.pending_deployments(REPO, 99)


@pytest.mark.parametrize(
    ("answer", "result"),
    [
        (ok({}), "approved"),
        (Completed(1, b"", b"gh: Not allowed (HTTP 422)"), "rejected"),
        (OSError("no gh"), "not_sent"),
    ],
)
def test_github_approves_one_deployment(
    answer: Completed | OSError, result: str
) -> None:
    runner = Runner(answer)
    assert GitHubCli(runner).approve_deployment(REPO, 99, 555, "why") == result
    assert runner.calls[0][2:7] == [
        "-X",
        "POST",
        f"repos/{REPO}/actions/runs/99/pending_deployments",
        "-F",
        "environment_ids[]=555",
    ]
    assert "state=approved" in runner.calls[0]


@pytest.mark.parametrize(
    "answer",
    [Completed(1, b"", b"HTTP 502"), subprocess.TimeoutExpired("gh", 1)],
)
def test_a_lost_approval_answer(answer: Completed | Exception) -> None:
    with pytest.raises(ApprovalAnswerLostError):
        GitHubCli(Runner(answer)).approve_deployment(REPO, 99, 555, "why")


def test_the_reviewed_targets() -> None:
    from zeo_core.release.pypi import TARGETS

    assert sorted(TARGETS) == ["sovereign-agent", "zeocore", "zeocreator"]
    assert "zero-employee" not in TARGETS  # Go-only now, never PyPI
    assert {t.environment for t in TARGETS.values()} == {"pypi"}
    assert _manifest(project="zeocreator", repository="ducktyper-ai/dt-creator").tag


def test_a_hidden_bypass_list_is_read_as_hidden_never_empty() -> None:
    # GitHub omits bypass_actors for a user without Admin on the repository.
    detail = _ruleset_detail()
    del detail["bypass_actors"]
    runner = Runner(ok([{"id": 7, "target": "tag"}]), ok(detail))
    (ruleset,) = GitHubCli(runner).tag_rulesets(REPO)
    assert ruleset.bypass is None
    assert ruleset.current_user_can_bypass == "never"


def test_a_ruleset_without_current_user_can_bypass_is_unreadable() -> None:
    detail = _ruleset_detail()
    del detail["current_user_can_bypass"]
    runner = Runner(ok([{"id": 7, "target": "tag"}]), ok(detail))
    with pytest.raises(TargetUnreadableError, match="can bypass"):
        GitHubCli(runner).tag_rulesets(REPO)


def test_admit_holds_when_the_bypass_list_is_hidden() -> None:
    class Hidden(FakeGitHub):
        def tag_rulesets(self, repo: str) -> tuple[TagRuleset, ...]:
            raise TargetUnreadableError(
                "the approver can't read the ruleset's bypass list; "
                "GitHub shows it only to a repository Admin"
            )

    outcome = admit(_manifest(), Hidden(), APP_ID)
    assert (outcome.status, outcome.reason) == ("held", "target_unreadable")
    assert "Admin" in outcome.message
