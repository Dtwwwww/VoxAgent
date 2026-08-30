import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "voxagent_runtime.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh.exe")


def _run_runtime(*arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_runtime_bootstrap_prepares_offline_paths_without_starting_services():
    with tempfile.TemporaryDirectory(prefix="voxagent-runtime-") as temporary:
        root = Path(temporary) / "runtime"
        environment = os.environ.copy()
        environment["VOXAGENT_ALLOW_TEST_PREFLIGHT_BYPASS"] = "1"

        result = _run_runtime(
            "-DataRoot",
            str(root),
            "-SkipPreflightForTests",
            environment=environment,
        )

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert Path(payload["data_root"]).samefile(root)
        assert payload["environment"]["OLLAMA_NO_CLOUD"] == "1"
        assert payload["environment"]["OLLAMA_HOST"] == "127.0.0.1:11434"
        assert payload["environment"]["OLLAMA_MODELS"].endswith("models\\ollama")
        assert payload["preflight"] == "bypassed_for_tests"


def test_runtime_bootstrap_honors_environment_data_root():
    with tempfile.TemporaryDirectory(prefix="voxagent-runtime-") as temporary:
        root = Path(temporary) / "from-env"
        environment = os.environ.copy()
        environment["VOXAGENT_DATA_ROOT"] = str(root)
        environment["VOXAGENT_ALLOW_TEST_PREFLIGHT_BYPASS"] = "1"

        result = _run_runtime("-SkipPreflightForTests", environment=environment)

        assert result.returncode == 0, result.stderr
        assert Path(json.loads(result.stdout)["data_root"]).samefile(root)


def _install_fake_uv(
    directory: Path,
    *,
    exit_code: int,
    issue_code: str = "ollama_offline_unverified",
) -> None:
    (directory / "uv.cmd").write_text(
        "@echo off\r\n"
        "echo {\"valid\":false,\"offline_status\":\"unverified\","
        f"\"issues\":[{{\"code\":\"{issue_code}\"}}]}}\r\n"
        f"exit /b {exit_code}\r\n",
        encoding="utf-8",
    )


def test_verified_ollama_command_is_blocked_before_execution(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _install_fake_uv(fake_bin, exit_code=2)
    marker = tmp_path / "command-ran.txt"
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["VOXAGENT_ALLOW_TEST_PREFLIGHT_BYPASS"] = "1"

    result = _run_runtime(
        "-DataRoot",
        str(tmp_path / "runtime"),
        "-SkipPreflightForTests",
        "-RequireVerifiedOllama",
        "-Command",
        f"Set-Content -LiteralPath '{marker}' -Value ran",
        environment=environment,
    )

    assert result.returncode != 0
    assert "Ollama runtime verification blocked command" in result.stderr
    assert not marker.exists()


def test_no_command_never_verifies_or_controls_ollama(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _install_fake_uv(fake_bin, exit_code=2)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["VOXAGENT_ALLOW_TEST_PREFLIGHT_BYPASS"] = "1"

    result = _run_runtime(
        "-DataRoot",
        str(tmp_path / "runtime"),
        "-SkipPreflightForTests",
        "-RequireVerifiedOllama",
        environment=environment,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["behavior"].startswith("Prepared directories")


def test_unverified_ollama_override_requires_switch_and_high_friction_env(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _install_fake_uv(fake_bin, exit_code=2)
    marker = tmp_path / "command-ran.txt"
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["VOXAGENT_ALLOW_TEST_PREFLIGHT_BYPASS"] = "1"

    blocked = _run_runtime(
        "-DataRoot",
        str(tmp_path / "runtime"),
        "-SkipPreflightForTests",
        "-RequireVerifiedOllama",
        "-AllowUnverifiedOllama",
        "-Command",
        f"Set-Content -LiteralPath '{marker}' -Value ran",
        environment=environment,
    )
    assert blocked.returncode != 0
    assert not marker.exists()

    environment["VOXAGENT_ACCEPT_UNVERIFIED_OLLAMA"] = (
        "I_ACCEPT_EXISTING_OLLAMA_WITH_UNVERIFIED_OFFLINE_STATE"
    )
    result = _run_runtime(
        "-DataRoot",
        str(tmp_path / "runtime"),
        "-SkipPreflightForTests",
        "-RequireVerifiedOllama",
        "-AllowUnverifiedOllama",
        "-Command",
        f"Set-Content -LiteralPath '{marker}' -Value ran",
        environment=environment,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8-sig").strip() == "ran"

    marker.unlink()
    _install_fake_uv(fake_bin, exit_code=2, issue_code="ollama_api_digest_mismatch")
    unsafe = _run_runtime(
        "-DataRoot",
        str(tmp_path / "runtime"),
        "-SkipPreflightForTests",
        "-RequireVerifiedOllama",
        "-AllowUnverifiedOllama",
        "-Command",
        f"Set-Content -LiteralPath '{marker}' -Value ran",
        environment=environment,
    )
    assert unsafe.returncode != 0
    assert not marker.exists()
