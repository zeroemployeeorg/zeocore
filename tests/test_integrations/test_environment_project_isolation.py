"""Project isolation is separate from credential identity."""

from pathlib import Path

import pytest

from zeo_core.integrations.environment import IntegrationMode
from zeo_core.integrations.environments import IntegrationEnvironment


@pytest.mark.parametrize("mode", ["test", "production"])
def test_shared_bluesky_endpoint_with_distinct_accounts_is_supported(
    tmp_path: Path, mode: IntegrationMode
) -> None:
    source = {
        f"ZEO_{track}_BLUESKY_{name}": value
        for track in ("TEST", "PRODUCTION")
        for name, value in {
            "SERVICE_URL": "https://bsky.social",
            "IDENTIFIER": f"{track.lower()}.bsky.social",
            "APP_PASSWORD": f"synthetic-{track.lower()}-password",
        }.items()
    }
    child = IntegrationEnvironment(
        mode, tmp_path, ("social.bluesky",)
    ).child_environment(source)
    assert child["BLUESKY_SERVICE_URL"] == "https://bsky.social"
    assert child["BLUESKY_IDENTIFIER"] == f"{mode}.bsky.social"
    assert child["BLUESKY_APP_PASSWORD"] == f"synthetic-{mode}-password"
