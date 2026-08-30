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
        )

    def create(self) -> None:
        for path in (self.root, self.models, self.cache, self.data, self.logs, self.benchmarks):
            path.mkdir(parents=True, exist_ok=True)
