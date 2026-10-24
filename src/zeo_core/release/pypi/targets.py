"""The reviewed table of PyPI release targets, and who acts for each.

The operator's delegation (2026-10-11, relayed by the Architect): the
``zeo-release`` GitHub App creates ``v*`` tags, and matorclawson approves the
``pypi`` environment's deployment. Two distinct identities, so neither can
release alone. Everything here is reviewed with zeocore's source; a local
manifest can name only a target in this table.

Each row's ``publisher`` is what PyPI must record for a release from it: the
repository, workflow and environment that PyPI's trusted publisher names.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

#: The only required reviewer of every ``pypi`` environment.
APPROVER: Final = "matorclawson"
#: The GitHub App that creates release tags, by slug.
RELEASE_APP_SLUG: Final = "zeo-release"
#: Its private key, in the signing host's Keychain (the operator's runbook).
RELEASE_APP_KEY_SERVICE: Final = "zeo-release-app-key"
#: Configuration the operator supplies, not secrets: the App's numeric id,
#: and the Keychain item's account.
RELEASE_APP_ID_VARIABLE: Final = "ZEOCORE_RELEASE_APP_ID"
RELEASE_APP_KEY_ACCOUNT_VARIABLE: Final = "ZEOCORE_RELEASE_APP_KEY_ACCOUNT"
#: The tag ref the ruleset must protect, and the deployment policies allowed.
RELEASE_TAG_REF: Final = "refs/tags/v*"
RELEASE_TAG_POLICIES: Final = frozenset({("v*", "tag"), ("v*.*.*", "tag")})


@dataclass(frozen=True)
class Target:
    project: str
    repository: str
    workflow: str
    environment: str


#: Read from each repository and from PyPI on 2026-10-11. zero-employee is
#: not here: it is now zeoruntime, released Go-only through the shared signed
#: release component, never PyPI (the operator, 2026-10-12, SOW-29 §4).
TARGETS: Final[Mapping[str, Target]] = {
    target.project: target
    for target in (
        Target("zeocore", "zeroemployeeorg/zeocore", "publish.yml", "pypi"),
        # 0.5.4 was published from profrodai/zeocreator with no environment;
        # its PyPI publisher is re-bound to this row before a release.
        Target("zeocreator", "ducktyper-ai/dt-creator", "publish.yml", "pypi"),
        Target("sovereign-agent", "profrodai/sovereign-agent", "publish.yml", "pypi"),
    )
}
