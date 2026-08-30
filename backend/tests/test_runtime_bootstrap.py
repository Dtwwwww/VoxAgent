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
