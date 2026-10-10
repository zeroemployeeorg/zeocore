"""``zeocore``: zeocore's contracts for callers that don't import Python."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from zeo_core import __version__
from zeo_core.cli import SCHEMAS
from zeo_core.cli.__main__ import COMMANDS, main, run
from zeo_core.cli.schemas import JSON_SCHEMA_DIALECT, rendered_file

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "contracts" / "zeocore-v1"


def _no_stdin() -> bytes:
    raise AssertionError("this command must not read stdin")


def _call(*argv: str, stdin: bytes | None = None) -> tuple[int, dict[str, object]]:
    return run(argv, (lambda: stdin) if stdin is not None else _no_stdin)


def test_the_command_is_installed_as_zeocore() -> None:
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["zeocore"] == "zeo_core.cli.__main__:main"


def test_version_names_the_release_and_both_protocols() -> None:
    assert _call("version") == (
        0,
        {
            "ok": True,
            "zeocore": __version__,
            "cli_protocol": "1",
            "zeoconnect_protocol": "1",
            "commands": sorted(COMMANDS),
        },
    )


@pytest.mark.parametrize(
    "argv",
    [(), ("nope",), ("version", "x"), ("schema",), ("schema", "nope"), ("validate",)],
)
def test_an_unusable_command_exits_2(argv: tuple[str, ...]) -> None:
    status, answer = _call(*argv, stdin=b"{}")
    assert status == 2
    assert answer["ok"] is False
    assert answer["outcome"] == "invalid_request"


def test_schema_list_is_every_stable_name() -> None:
    assert _call("schema", "list") == (0, {"ok": True, "schemas": sorted(SCHEMAS)})


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_each_schema_is_2020_12_with_a_stable_id(name: str) -> None:
    status, schema = _call("schema", name)
    assert status == 0
    assert schema["$schema"] == JSON_SCHEMA_DIALECT
    assert schema["$id"] == (
        f"https://zeocore.zeo.ac/contracts/zeocore-v1/{name}.schema.json"
    )


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_the_committed_schema_file_matches_the_model(name: str) -> None:
    # On drift, regenerate: for each name, write rendered_file(name) to
    # contracts/zeocore-v1/<name>.schema.json, and review the diff as a
    # contract change.
    path = COMMITTED / f"{name}.schema.json"
    assert path.read_text() == rendered_file(name), f"{path.name} drifted"


def test_no_committed_schema_is_unnamed() -> None:
    assert sorted(path.name for path in COMMITTED.glob("*.schema.json")) == sorted(
        f"{name}.schema.json" for name in SCHEMAS
    )


def test_validate_answers_the_normalized_value() -> None:
    status, answer = _call(
        "validate",
        "hosted.operation-request",
        stdin=json.dumps(
            {
                "connection_id": "con_google_12345678",
                "operation_id": "google.drive.file.download",
                "arguments": {"file_id": "f"},
                "idempotency_key": "k",
            }
        ).encode(),
    )
    assert status == 0
    assert answer["ok"] is True
    value = answer["value"]
    assert isinstance(value, dict)
    # Optional fields added in later minor contracts come back as null.
    assert {key: item for key, item in value.items() if item is not None} == {
        "connection_id": "con_google_12345678",
        "operation_id": "google.drive.file.download",
        "arguments": {"file_id": "f"},
        "idempotency_key": "k",
    }


def test_a_value_that_does_not_match_is_held_and_never_echoes_input() -> None:
    canary = "secret-value-canary-1234"
    status, answer = _call(
        "validate",
        "connections.normalized-error",
        stdin=json.dumps({"code": canary, "message": "m", canary: canary}).encode(),
    )
    assert status == 20
    assert answer["outcome"] == "invalid"
    assert {"loc": ["code"], "type": "enum"} in answer["errors"]  # type: ignore[operator]
    # The unknown field's name is its loc, which is the caller's own key; no
    # value is ever repeated.
    assert json.dumps(answer).count(canary) == 1


def test_validation_errors_carry_only_loc_and_type() -> None:
    _, answer = _call(
        "validate",
        "connections.normalized-error",
        stdin=b'{"code": "not-a-code", "message": "hidden message text"}',
    )
    for error in answer["errors"]:  # type: ignore[attr-defined]
        assert set(error) == {"loc", "type"}
    assert "not-a-code" not in json.dumps(answer)
    assert "hidden message text" not in json.dumps(answer)


@pytest.mark.parametrize(
    "stdin",
    [b"", b"not json", b'{"a": 1, "a": 2}', b'{"a": NaN}', b"[" * 2000 + b"]" * 2000],
)
def test_input_must_be_strict_json(stdin: bytes) -> None:
    for argv in (("digest",), ("validate", "revolut.account")):
        status, answer = _call(*argv, stdin=stdin)
        assert status == 2, argv
        assert answer["outcome"] == "invalid_request"


def test_oversized_input_is_refused() -> None:
    status, _ = _call("digest", stdin=b'"' + b"x" * (1024 * 1024) + b'"')
    assert status == 2


def test_digest_is_the_rfc_8785_canonical_sha256() -> None:
    canonical = b'{"a":[1,2],"b":"\xc3\xa9"}'
    expected = "sha256:" + hashlib.sha256(canonical).hexdigest()
    for raw in (b'{"b": "\\u00e9", "a": [1, 2]}', b'{"a":[1,2],"b":"\xc3\xa9"}'):
        assert _call("digest", stdin=raw) == (0, {"ok": True, "sha256": expected})


def test_the_exit_family_is_the_youtube_publish_family() -> None:
    from zeo_core.cli import __main__ as cli
    from zeo_core.integrations.google.youtube import publish

    assert (cli.EXIT_DONE, cli.EXIT_INVALID) == (
        publish.EXIT_DONE,
        publish.EXIT_INVALID,
    )
    assert (cli.EXIT_APPROVAL, cli.EXIT_WAIT, cli.EXIT_HELD) == (
        publish.EXIT_APPROVAL,
        publish.EXIT_WAIT,
        publish.EXIT_HELD,
    )
    assert (cli.EXIT_NOT_PAIRED, cli.EXIT_AMBIGUOUS) == (12, 13)


def test_main_writes_exactly_one_json_line_and_exits_with_the_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["schema", "list"])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    result = json.loads(out)
    assert result["schemas"] == sorted(SCHEMAS)
    assert "event" not in result
    with pytest.raises(SystemExit) as exited:
        main(["nope"])
    assert exited.value.code == 2
