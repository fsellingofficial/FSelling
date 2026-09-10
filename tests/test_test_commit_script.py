import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell")


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required")
def test_missing_venv_uses_path_python_and_never_reports_false_pass(tmp_path):
    root = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    (root / "static").mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(ROOT / "test-commit.ps1", root / "test-commit.ps1")
    (root / "static" / "probe.js").write_text("const ok = true;\n", encoding="utf-8")
    (bin_dir / "node.cmd").write_text("@exit /b 0\r\n", encoding="ascii")
    (bin_dir / "python.cmd").write_text("@exit /b 7\r\n", encoding="ascii")

    env = os.environ.copy()
    env["PATH"] = str(bin_dir)
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", root / "test-commit.ps1", "-TestOnly"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "TEST FAIL" in result.stdout
    assert "TEST PASS" not in result.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required")
def test_multiple_path_pythons_use_only_the_first_interpreter(tmp_path):
    root = tmp_path / "repo"
    first_bin = tmp_path / "first-bin"
    second_bin = tmp_path / "second-bin"
    (root / "static").mkdir(parents=True)
    first_bin.mkdir()
    second_bin.mkdir()
    shutil.copy2(ROOT / "test-commit.ps1", root / "test-commit.ps1")
    (root / "static" / "probe.js").write_text("const ok = true;\n", encoding="utf-8")
    (first_bin / "node.cmd").write_text("@exit /b 0\r\n", encoding="ascii")
    (first_bin / "python.cmd").write_text(
        "@echo FIRST_PYTHON\r\n@exit /b 7\r\n", encoding="ascii"
    )
    (second_bin / "python.cmd").write_text(
        "@echo SECOND_PYTHON\r\n@exit /b 0\r\n", encoding="ascii"
    )

    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(first_bin), str(second_bin)))
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", root / "test-commit.ps1", "-TestOnly"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "FIRST_PYTHON" in result.stdout
    assert "SECOND_PYTHON" not in result.stdout
    assert "TEST FAIL" in result.stdout
