"""Behavioral tests for the canonical test runner."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def test_runner_replaces_caller_path_and_sudo_identity(tmp_path: Path) -> None:
    """Caller executables and sudo identity must not reach pytest."""
    source_runner = Path(__file__).parents[1] / "scripts" / "run_tests.sh"
    repo = tmp_path / "checkout"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    runner = scripts / "run_tests.sh"
    shutil.copy2(source_runner, runner)

    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "activate").touch()
    fake_python = venv_bin / "python"
    fake_python.write_text(
        """#!/bin/bash
if [[ "$1" == "-c" && "$2" == "import pytest_split" ]]; then
  exit 0
fi
printf 'FAKE_PYTHON_PATH=%s\\n' "$PATH"
printf 'FAKE_PYTHON_SUDO_USER=%s\\n' "${SUDO_USER-unset}"
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    hostile_bin = tmp_path / "caller-tools"
    hostile_bin.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "HERMES_TEST_WORKERS": "1",
            "PATH": f"{hostile_bin}:/usr/bin:/bin",
            "SUDO_USER": "caller",
        }
    )

    result = subprocess.run(
        ["/bin/bash", str(runner), "-q"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    path_line = next(
        line for line in result.stdout.splitlines() if line.startswith("FAKE_PYTHON_PATH=")
    )
    effective_path = path_line.removeprefix("FAKE_PYTHON_PATH=")
    assert effective_path.split(":")[0] == str(venv_bin)
    assert str(hostile_bin) not in effective_path
    assert "FAKE_PYTHON_SUDO_USER=unset" in result.stdout
