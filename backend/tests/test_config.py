from pathlib import Path

from typer.testing import CliRunner

from voxagent.cli import app
from voxagent.config import AppPaths, resolve_data_root

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


def test_paths_subcommand_creates_and_prints_requested_root(tmp_path):
    root = tmp_path / "cli-root"
    result = runner.invoke(app, ["paths", "--data-root", str(root)])
    assert result.exit_code == 0
    assert result.stdout.strip() == str(root.resolve())
    assert root.is_dir()
