from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_ROOT = Path(r"D:\VoxAgentData")


def resolve_data_root(cli_value: Path | None) -> Path:
    if cli_value is not None:
        return cli_value.expanduser().resolve()
    configured = os.environ.get("VOXAGENT_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_DATA_ROOT


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    models: Path
    cache: Path
    data: Path
    logs: Path
    benchmarks: Path
    ollama_models: Path
    speech_models: Path
    uv_cache: Path
    temp: Path
    pytest_temp: Path

    @classmethod
    def from_root(cls, root: Path) -> AppPaths:
        normalized = root.expanduser().resolve()
        return cls(
            root=normalized,
            models=normalized / "models",
            cache=normalized / "cache",
            data=normalized / "data",
            logs=normalized / "logs",
            benchmarks=normalized / "benchmarks",
            ollama_models=normalized / "models" / "ollama",
            speech_models=normalized / "models" / "speech",
            uv_cache=normalized / "cache" / "uv",
            temp=normalized / "cache" / "temp",
            pytest_temp=normalized / "cache" / "pytest",
        )

    def create(self) -> None:
        for path in (
            self.root,
            self.models,
            self.cache,
            self.data,
            self.logs,
            self.benchmarks,
            self.ollama_models,
            self.speech_models,
            self.uv_cache,
            self.temp,
            self.pytest_temp,
        ):
            path.mkdir(parents=True, exist_ok=True)


def runtime_environment(paths: AppPaths) -> dict[str, str]:
    return {
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
