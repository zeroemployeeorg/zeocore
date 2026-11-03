"""The neutral part of the provider registry: an injected engine factory (deviation D4).

Upstream, this module held a table of vendor short names to dotted paths, and the engine
module imported whatever a profile named. Both leave Core. What stays is the contract:

* ``EngineFactory`` is the one thing a caller must supply to build engines. There is
  no default, no lookup by dotted path and no environment fallback.
* ``factory_from_map`` is the caller-supplied map for slice 1: provider names are KEYS
  into a mapping the caller built, never import paths that Core executes.

Core never sees a credential: whoever builds the map builds the credential-bearing
engines.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from zeo_core.voice.engine import AudioFormat


class EngineFactory(Protocol):
    """Build one engine of ``kind`` ("asr" or "tts") from a provider config."""

    def __call__(
        self,
        kind: str,
        config: Mapping[str, Any],
        audio_format: AudioFormat,
        language: str,
        additional_languages: list[str] | None = None,
    ) -> Any: ...  # noqa: ANN401 - the engine type is the caller's


#: What a map entry builds: the same arguments as the factory, without ``kind``.
EngineBuilder = Callable[[Mapping[str, Any], AudioFormat, str, list[str] | None], Any]


def require_factory(factory: object) -> None:
    """Refuse to build anything without an injected factory."""
    if factory is None or not callable(factory):
        raise TypeError(
            "an EngineFactory is required: Core loads no engine by name or path "
            "and reads no credential"
        )


def factory_from_map(
    builders: Mapping[str, Mapping[str, EngineBuilder]],
) -> EngineFactory:
    """A factory over ``{"asr": {name: builder}, "tts": {name: builder}}``.

    The provider ``name`` in a config selects a key; an unknown key is an error naming
    the known keys, and nothing is imported.
    """

    def factory(
        kind: str,
        config: Mapping[str, Any],
        audio_format: AudioFormat,
        language: str,
        additional_languages: list[str] | None = None,
    ) -> Any:  # noqa: ANN401 - the engine type is the caller's
        known = builders.get(kind)
        if known is None:
            raise ValueError(f"no builders supplied for kind {kind!r}")
        name = str(config.get("name"))
        if name not in known:
            supplied = ", ".join(sorted(known))
            raise ValueError(f"unknown {kind} provider {name!r}; supplied: {supplied}")
        settings = {key: value for key, value in config.items() if key != "name"}
        return known[name](settings, audio_format, language, additional_languages)

    return factory


__all__ = [
    "EngineBuilder",
    "EngineFactory",
    "factory_from_map",
    "require_factory",
]
