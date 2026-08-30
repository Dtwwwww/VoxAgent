from pathlib import Path

from typer.testing import CliRunner

from voxagent.cli import app
from voxagent.config import AppPaths, resolve_data_root, runtime_environment

runner = CliRunner()


def test_default_data_root_is_on_d_drive(monkeypatch):
    monkeypatch.delenv("VOXAGENT_DATA_ROOT", raising=False)
    assert resolve_data_root(None) == Path(r"D:\VoxAgentData")


def test_cli_root_overrides_environment(monkeypatch, tmp_path):
    env_root = tmp_path / "env"
    cli_root = tmp_path / "cli"
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(env_root))
    assert resolve_data_root(cli_root) == cli_root.resolve()


def test_create_makes_all_runtime_directories(tmp_path):
    paths = AppPaths.from_root(tmp_path / "runtime")
    paths.create()
    assert paths.models.is_dir()
    assert paths.cache.is_dir()
    assert paths.data.is_dir()
    assert paths.logs.is_dir()
    assert paths.benchmarks.is_dir()
    assert paths.ollama_models.is_dir()
    assert paths.speech_models.is_dir()
    assert paths.uv_cache.is_dir()
    assert paths.temp.is_dir()
    assert paths.pytest_temp.is_dir()


def test_runtime_environment_is_offline_loopback_and_rooted(tmp_path):
    paths = AppPaths.from_root(tmp_path / "runtime")

    environment = runtime_environment(paths)

    assert environment == {
        "VOXAGENT_DATA_ROOT": str(paths.root),
        "VOXAGENT_MODEL_ROOT": str(paths.models),
        "VOXAGENT_SPEECH_MODEL_ROOT": str(paths.speech_models),
        "OLLAMA_MODELS": str(paths.ollama_models),
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_HOST": "127.0.0.1:11434",
        "UV_CACHE_DIR": str(paths.uv_cache),
        "TEMP": str(paths.temp),
        "TMP": str(paths.temp),
        "VOXAGENT_PYTEST_TEMP": str(paths.pytest_temp),
    }


def test_paths_subcommand_creates_and_prints_requested_root(tmp_path):
    root = tmp_path / "cli-root"
    result = runner.invoke(app, ["paths", "--data-root", str(root)])
    assert result.exit_code == 0
    assert result.stdout.strip() == str(root.resolve())
    assert root.is_dir()
