# Speech routing (`zeo_core.voice`)

`zeo_core.voice` routes speech recognition (ASR) and speech synthesis (TTS) across several
providers: it tries them in order or by measured latency, cools a failing one down, and
fails over without the caller hearing silence. It holds the routing, health, metrics,
failure classification, utterance policy, audio conversion and benchmark statistics. It
holds no vendor, no credential and no network client.

**Offline contract only.** The route to a real vendor (streaming, audio and cancellation
through ZEOconnect) is not part of this release. Nothing here calls a provider.

## Public names

| Import | What it is |
|---|---|
| `zeo_core.voice.RoutedASR`, `RoutedTTS` | The routers. Built with `from_config_dict(config, format, language, factory=...)`. |
| `zeo_core.voice.HealthRegistry`, `ProviderHealth` | Provider health, cool-down and permanent failures. |
| `zeo_core.voice.profile.VoiceProfile` | A versioned speech profile; `build_asr(factory)`, `build_tts(factory)`, `engines(factory)`. |
| `zeo_core.voice._provider_registry` | `EngineFactory` (the protocol a caller supplies) and `factory_from_map`. |
| `zeo_core.voice.engine` | Standalone engine types: `AudioFormat`, `RasaAudioBytes` and its neutral alias `AudioBytes`, `ASREngine`, `TTSEngine`, events and errors. |
| `zeo_core.voice.contract` | The contract scanner. It takes an explicit source root. |
| `zeo_core.voice.bench` | Benchmark statistics (word error rate, agreement, latency). |

## The factory is required

Routers and profiles build engines only through a caller-supplied `EngineFactory`:

```python
from zeo_core.voice._provider_registry import factory_from_map
from zeo_core.voice.profile import VoiceProfile

factory = factory_from_map({"asr": {"my-asr": build_asr}, "tts": {"my-tts": build_tts}})
asr, tts = VoiceProfile.load("profile.json").engines(factory)
```

- With no factory the call raises `TypeError`. There is no default, no environment fallback
  and no loading by dotted path.
- A provider `name` in a profile is a key into the supplied map. Core never imports a
  module named by data.
- Core never reads a credential. Whoever builds the map builds the engines, and holds
  their keys.

## How this differs from the upstream router

This package is derived from the Apache-2.0 `voicerouter` pattern of
`RasaHQ/rasa-community-resources` (commit `9f85e2be`). Other than module names, import
order and line wrapping, the changes are numbered, and each has its own test in
`tests/test_voice/test_deviations.py`:

| | Change |
|---|---|
| D1 | The engine types are always the standalone ones; an installed Rasa never changes them. |
| D2 | `AudioBytes` is added beside `RasaAudioBytes`. |
| D3 | `structlog` is replaced by a small stdlib shim (`zeo_core.voice._log`); context binding and processors are not reproduced. |
| D4 | The default name and dotted-path engine factories are removed; an `EngineFactory` is required. |
| D5 | The `required_env_vars` check and `env_present` leave Core; the vendor engines own that policy. |
| D6 | `audioop` is the standard library on Python 3.12 and the `audioop-lts` package on 3.13 and later. |
| D7 | The contract scanner takes an explicit source root and imports nothing to find one. |
| D8 | A provider that fails after it connected is closed, bounded to five seconds, before the router stops tracking it. A language the engine refuses before touching its socket leaves the connection open. |

The tests in `tests/test_voice/` are derived from the upstream tests by function. The
differences (stand-in HTTP and AWS errors, an inline profile, an explicit scanner root)
are listed in `tests/test_voice/NOTICE`.

## Licence

The files in `src/zeo_core/voice/` and `tests/test_voice/` that come from the upstream
pattern are Apache-2.0 (`src/zeo_core/voice/LICENSE`, `src/zeo_core/voice/NOTICE`,
`tests/test_voice/NOTICE`); the rest of ZeoCore is MIT. Use of the upstream names is for
identification of compatibility only and implies no endorsement.
