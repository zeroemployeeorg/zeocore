"""Real fresh-kernel execution and cleanup positive controls."""

import importlib
import json
import os
from pathlib import Path

import pytest

from zeo_core.integrations.notebook import NotebookExecutionRequest, execute_notebook


def notebook(path: Path, code: str, *, stale: bool = False) -> None:
    path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "id": "exercise",
                        "cell_type": "code",
                        "source": code,
                        "metadata": {},
                        "execution_count": 99 if stale else None,
                        "outputs": [
                            {
                                "output_type": "stream",
                                "name": "stdout",
                                "text": "STALE_OUTPUT",
                            }
                        ]
                        if stale
                        else [],
                    }
                ],
            }
        )
    )


def request(root: Path, **kwargs: object) -> NotebookExecutionRequest:
    values: dict[str, object] = {
        "source_path": "source.ipynb",
        "output_path": "executed.ipynb",
        "workspace_root": str(root),
        "working_directory": str(root),
        "timeout_seconds": 20,
        "cell_timeout_seconds": 5,
    }
    values.update(kwargs)
    return NotebookExecutionRequest.model_validate(values)


@pytest.mark.parametrize(
    "code,kind,options",
    [
        ("def broken(", "SYNTAX_ERROR", {}),
        ("assert False", "ASSERTION_FAILURE", {}),
        ("raise RuntimeError('failure')", "CELL_EXCEPTION", {}),
        ("import os; os._exit(17)", "KERNEL_CRASH", {}),
        ("import time; time.sleep(10)", "CELL_TIMEOUT", {"cell_timeout_seconds": 1}),
        ("import time; time.sleep(10)", "WHOLE_RUN_TIMEOUT", {"timeout_seconds": 2}),
        (
            "print('never starts')",
            "MISSING_KERNEL",
            {"kernel_name": "missing-zeocore-kernel"},
        ),
        ("", "UNEVALUATED_CODE", {}),
    ],
)
def test_failure_cannot_succeed(
    tmp_path: Path,
    code: str,
    kind: str,
    options: dict,
) -> None:
    notebook(tmp_path / "source.ipynb", code, stale=True)
    result = execute_notebook(request(tmp_path, **options))
    assert not result.success
    assert result.content is not None, result
    assert result.content.failure_kind == kind, result
    assert result.content.cleanup == "SUCCEEDED"
    assert result.content.executed_output is None
    assert not (tmp_path / "executed.ipynb").exists()
    assert not list(tmp_path.glob(".execution-*"))


def test_fresh_kernel_and_secret_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    secret = "NEVER_INHERIT_THIS_SYNTHETIC_CREDENTIAL"  # noqa: S105 -- synthetic sentinel
    monkeypatch.setenv("PROVIDER_TOKEN", secret)
    monkeypatch.setenv("UNRELATED_VALUE", secret)
    monkeypatch.setenv("HTTP_PROXY", secret)
    notebook(
        tmp_path / "source.ipynb",
        """
import os
assert "FRESH_STATE" not in globals()
assert "PROVIDER_TOKEN" not in os.environ
assert "UNRELATED_VALUE" not in os.environ
assert "HTTP_PROXY" not in os.environ
FRESH_STATE = 5
print(os.getpid())
print(dict(os.environ))
""",
        stale=True,
    )
    source = (tmp_path / "source.ipynb").read_bytes()
    first = execute_notebook(request(tmp_path))
    second = execute_notebook(request(tmp_path, output_path="second.ipynb"))
    assert first.success and second.success, (first, second)
    assert first.content is not None and second.content is not None
    for receipt in (first.content, second.content):
        assert receipt.code_cells_executed == 1
        assert receipt.python_version
        assert receipt.cleanup == "SUCCEEDED"
        assert receipt.process_isolation == "UNAVAILABLE"
        assert receipt.network_isolation == "UNAVAILABLE"
        assert receipt.executed_output is not None
        assert receipt.source.sha256 != receipt.executed_output.sha256
    first_bytes = (tmp_path / "executed.ipynb").read_text()
    second_bytes = (tmp_path / "second.ipynb").read_text()
    for evidence in (
        first_bytes,
        second_bytes,
        first.model_dump_json(),
        second.model_dump_json(),
        capsys.readouterr().out,
    ):
        assert secret not in evidence
        assert "STALE_OUTPUT" not in evidence
    a = json.loads(first_bytes)["cells"][0]["outputs"][0]["text"]
    b = json.loads(second_bytes)["cells"][0]["outputs"][0]["text"]
    assert a != b  # contains distinct kernel PIDs and temporary HOME directories
    assert (tmp_path / "source.ipynb").read_bytes() == source


@pytest.mark.parametrize(
    "ending,options",
    [
        ("print('done')", {}),
        ("raise RuntimeError('fail')", {}),
        # A timeout must fire while the cell still sleeps, after the kernel has started
        # and written its pid files. Seconds of margin, not one or two, so a busy
        # machine does not turn a slow kernel start into a missing file.
        ("import time; time.sleep(60)", {"cell_timeout_seconds": 5}),
        (
            "import time; time.sleep(60)",
            {"timeout_seconds": 12, "cell_timeout_seconds": 30},
        ),
        ("os._exit(23)", {}),
    ],
)
def test_descendant_cleanup(tmp_path: Path, ending: str, options: dict) -> None:
    code = (
        """
import os, subprocess, sys
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path("child.pid").write_text(str(child.pid))
Path("kernel.pid").write_text(str(os.getpid()))
"""
        + ending
    )
    notebook(tmp_path / "source.ipynb", code)
    result = execute_notebook(request(tmp_path, **options))
    assert result.content is not None, result
    assert result.content.cleanup == "SUCCEEDED", result
    psutil = importlib.import_module("psutil")
    for name in ("child.pid", "kernel.pid"):
        pid = int((tmp_path / name).read_text())
        try:
            process = psutil.Process(pid)
            assert not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass
    assert result.success == (ending == "print('done')")


@pytest.mark.parametrize(
    "document,code",
    [
        ("", "EMPTY_INPUT"),
        ('{"cells":[]}', "ZERO_CELLS"),
        ('{"cells":[{"cell_type":"markdown"}]}', "NO_CODE_CELLS"),
        ('{"cells":[{"cell_type":"alien"}]}', "UNSUPPORTED_CELL_TYPE"),
    ],
)
def test_empty_preflight(tmp_path: Path, document: str, code: str) -> None:
    (tmp_path / "source.ipynb").write_text(document)
    result = execute_notebook(request(tmp_path))
    assert result.error == code
    assert list(tmp_path.iterdir()) == [tmp_path / "source.ipynb"]


def test_allowlist_and_overwrite_refusal(tmp_path: Path) -> None:
    notebook(tmp_path / "source.ipynb", "print(1)")
    result = execute_notebook(
        request(tmp_path, environment_allowlist=("PROVIDER_TOKEN",))
    )
    assert result.error == "UNSAFE_ENVIRONMENT_ALLOWLIST"
    result = execute_notebook(request(tmp_path, output_path="source.ipynb"))
    assert not result.success
    assert os.path.exists(tmp_path / "source.ipynb")
