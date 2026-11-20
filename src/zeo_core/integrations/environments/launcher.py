"""Process-scoped integration credentials and configuration."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from zeo_core.integrations.environment import IntegrationBackend, IntegrationMode
from zeo_core.integrations.environments.catalog import CATALOG

# Preserve process necessities, never ambient SDK keys, proxy settings, config
# overrides, PYTHONPATH or a second environment's namespace. HOME is unchanged;
# ZeoCore credential/config readers explicitly use the managed state directory.
_PROCESS_VARIABLES = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "TERM",
    "VIRTUAL_ENV",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)
_SECRET_SUFFIXES = (
    "TOKEN",
    "API_KEY",
    "APP_PASSWORD",
    "PUBLISHABLE_KEY",
    "SECRET_KEY",
)


@dataclass(frozen=True)
class IntegrationEnvironment:
    """One application process, one mode, explicit provider inputs.

    Fixture mode removes all provider variables; applications supply their own
    controlled transports/services. Neither backend is an operating-system sandbox.
    """

    mode: IntegrationMode
    root: Path
    integrations: tuple[str, ...]
    backend: IntegrationBackend = "live"

    def __post_init__(self) -> None:
        if self.mode not in {"test", "production"}:
            raise ValueError("Mode must be test or production")
        if self.backend not in {"live", "fixture"} or (
            self.backend == "fixture" and self.mode != "test"
        ):
            raise ValueError("Fixture execution requires test mode")
        if not self.integrations or len(set(self.integrations)) != len(
            self.integrations
        ):
            raise ValueError("Choose at least one integration without duplicates")
        if any(name not in CATALOG for name in self.integrations):
            raise ValueError("Unknown integration; consult the setup catalog")
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @property
    def state_dir(self) -> Path:
        if self.backend == "fixture":
            return self.root / "fixtures" / self.mode
        return self.root / self.mode

    @property
    def work_dir(self) -> Path:
        return self.state_dir / "work"

    def prepare(self) -> None:
        """Create separate private directories; refuse aliases into another mode."""
        for path in (
            self.state_dir,
            self.work_dir,
            self.state_dir / "config",
            self.state_dir / "credentials",
            self.state_dir / "tmp",
        ):
            if path.resolve() != path:
                raise ValueError(
                    "Integration environment directories must not be symlinked"
                )
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = self.state_dir / "config" / "integrations.yaml"
        if config.resolve() != config:
            raise ValueError("Integration config must not be symlinked")
        try:
            descriptor = os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return
        with os.fdopen(descriptor, "w") as handle:
            handle.write("{}\n")

    def child_environment(
        self, source: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        """Return selected credentials only; never mutate the parent process."""
        source = os.environ if source is None else source
        result = {key: source[key] for key in _PROCESS_VARIABLES if key in source}
        prefix = f"ZEO_{self.mode.upper()}_"
        other = "ZEO_PRODUCTION_" if self.mode == "test" else "ZEO_TEST_"
        allowed = {key for name in self.integrations for key in CATALOG[name].variables}
        prefixes = tuple(
            key for name in self.integrations for key in CATALOG[name].prefixes
        )
        for key, value in source.items():
            if not key.startswith(prefix):
                continue
            target = key.removeprefix(prefix)
            if target not in allowed and not (prefixes and target.startswith(prefixes)):
                # Other selected-mode providers may be configured in the same shell.
                continue
            if self.backend == "fixture":
                continue
            if (
                value
                and target.endswith(_SECRET_SUFFIXES)
                and value == source.get(other + target)
            ):
                raise ValueError(
                    f"Test and production must use different credentials for {target}"
                )
            result[target] = value
        result.update(
            {
                "ZEO_INTEGRATION_MODE": self.mode,
                "ZEO_INTEGRATION_BACKEND": self.backend,
                "ZEO_INTEGRATION_STATE_DIR": str(self.state_dir),
                "ZEO_INTEGRATION_IDS": ",".join(self.integrations),
                # Prevent requests from replacing explicit tokens with HOME/.netrc.
                "NETRC": os.devnull,
                "TMPDIR": str(self.state_dir / "tmp"),
                "TMP": str(self.state_dir / "tmp"),
                "TEMP": str(self.state_dir / "tmp"),
            }
        )
        return result

    def run(
        self,
        command: Sequence[str],
        *,
        source: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> int:
        """Run the application without a shell and propagate its exit status."""
        if not command or not command[0]:
            raise ValueError("An application command is required")
        environment = self.child_environment(source)
        self.prepare()
        result = subprocess.run(  # noqa: S603 -- explicit application, no shell
            list(command),
            cwd=self.work_dir,
            env=environment,
            timeout=timeout,
            check=False,
        )
        return result.returncode
