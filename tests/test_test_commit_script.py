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
