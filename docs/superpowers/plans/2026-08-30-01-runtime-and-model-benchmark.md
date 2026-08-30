# Runtime and Model Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible Windows runtime probe and LLM benchmark harness, select the safe local Qwen default for the target GTX 1660 Ti laptop, and install the exact speech assets that Plan 02 will benchmark through real adapters.

**Architecture:** A small Python package owns application paths, hardware probes, model manifests, Ollama access, shared timing primitives, and deterministic LLM selection rules. It writes machine-readable LLM artifacts under `benchmarks/`, verifies speech assets and runtime imports, but does not yet construct the conversational ASR/TTS adapters.

**Tech Stack:** Python 3.12, uv, pytest, psutil, httpx, Typer, sherpa-onnx, NumPy, SoundFile, Ollama, FFmpeg.

## Global Constraints

- Target platform is Windows 11 x64, single user.
- Python must be `>=3.12,<3.13`; Node.js must be `>=24,<25` when the frontend is introduced.
- Runtime data defaults to `D:\VoxAgentData`; large models and caches must not default to C:.
- C: must have at least 15GB free before model installation; the selected data drive must have at least 20GB free (D: for the default `D:\VoxAgentData` root).
- GTX 1660 Ti peak model workload must remain below approximately 5.4GB VRAM.
- Local operation is the default; this plan must not configure or call a cloud model.
- Do not install a full CUDA Toolkit or CMake for this phase; use prebuilt Ollama and sherpa-onnx runtimes.
- Every accepted entry in the combined baseline must include UTC timestamp, model identifier, machine snapshot, latency, RAM, VRAM, and success/failure reason; raw per-model timing artifacts may be joined with the resource sampler in Task 5.

## Current Standard Runtime Commands

The executable implementation now uses one PowerShell entry point for operational commands. The
task-by-task snippets below remain the original TDD construction record; use this wrapper for
current verification and future reruns so every cache/model/temp path follows the selected root:

```powershell
& .\scripts\voxagent_runtime.ps1
& .\scripts\voxagent_runtime.ps1 -Command {
    Push-Location backend
    uv run --extra dev --extra speech pytest -q
    uv run --extra dev ruff check src tests
    uv run voxagent validate-baseline --baseline ../benchmarks/target-machine-baseline.json --json
    Pop-Location
}
```

The wrapper defaults to `D:\VoxAgentData`, honors `VOXAGENT_DATA_ROOT` and explicit `-DataRoot`,
sets Ollama to loopback/offline mode, and gates execution on C: plus the selected data drive.
Speech downloads invoke the same preflight before any archive download.

---

## Planned File Structure

```text
README.md
backend/
  pyproject.toml
  uv.lock
  src/voxagent/
    __init__.py
    cli.py
    config.py
    diagnostics/
      __init__.py
      hardware.py
      llm_benchmark.py
      selection.py
      speech_benchmark.py
    llm/
      __init__.py
      ollama.py
    speech/
      __init__.py
      model_manifest.py
  tests/
    test_config.py
    diagnostics/
      test_hardware.py
      test_llm_benchmark.py
      test_selection.py
      test_speech_benchmark.py
    llm/
      test_ollama.py
scripts/
  download_speech_models.ps1
benchmarks/
  .gitkeep
```

`backend` is the future local service package. `diagnostics` contains code that is safe to run before the app exists. `benchmarks` stores committed target-machine reports without model weights or raw recordings.

### Task 1: Python package, deterministic paths, and CLI shell

**Files:**
- Create: `README.md`
- Create: `backend/pyproject.toml`
- Create: `backend/src/voxagent/__init__.py`
- Create: `backend/src/voxagent/config.py`
- Create: `backend/src/voxagent/cli.py`
- Create: `backend/tests/test_config.py`
- Create: `benchmarks/.gitkeep`

**Interfaces:**
- Produces: `AppPaths.from_root(root: Path) -> AppPaths`
- Produces: `AppPaths.create() -> None`
- Produces: `resolve_data_root(cli_value: Path | None) -> Path`
- Produces: Typer command `voxagent paths --data-root PATH`

- [ ] **Step 1: Create the package manifest and package markers**

Create `backend/pyproject.toml`:

```toml
[project]
name = "voxagent"
version = "0.1.0"
description = "Local-first Windows voice companion"
requires-python = ">=3.12,<3.13"
dependencies = [
  "httpx>=0.28,<1",
  "numpy>=2.0,<3",
  "psutil>=6.1,<8",
  "soundfile>=0.13,<1",
  "typer>=0.15,<1",
]

[project.optional-dependencies]
speech = ["sherpa-onnx>=1.12,<2"]
dev = [
  "pytest>=8.3,<10",
  "pytest-asyncio>=0.25,<2",
  "ruff>=0.9,<1",
]

[project.scripts]
voxagent = "voxagent.cli:app"

[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/voxagent"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC"]
```

Create `backend/src/voxagent/__init__.py`:

```python
__all__ = ["__version__"]
__version__ = "0.1.0"
```

Create `benchmarks/.gitkeep` as an empty file. Run:

```powershell
cd backend
uv sync --extra dev --extra speech
```

Expected: `uv.lock` is created and the command exits with code 0.

- [ ] **Step 2: Write the failing path tests**

Create `backend/tests/test_config.py`:

```python
from pathlib import Path

from voxagent.config import AppPaths, resolve_data_root


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
```

- [ ] **Step 3: Run the path tests and verify failure**

Run:

```powershell
cd backend
uv run pytest tests/test_config.py -v
```

Expected: FAIL during import with `ModuleNotFoundError: No module named 'voxagent.config'`.

- [ ] **Step 4: Implement deterministic runtime paths**

Create `backend/src/voxagent/config.py`:

```python
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
    def from_root(cls, root: Path) -> "AppPaths":
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
```

Create `backend/src/voxagent/cli.py`:

```python
from pathlib import Path

import typer

from voxagent.config import AppPaths, resolve_data_root

app = typer.Typer(no_args_is_help=True)


@app.command("paths")
def show_paths(
    data_root: Path | None = typer.Option(None, "--data-root", dir_okay=True),
) -> None:
    paths = AppPaths.from_root(resolve_data_root(data_root))
    paths.create()
    typer.echo(str(paths.root))


if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run tests, lint, and the CLI**

Run:

```powershell
cd backend
uv run pytest tests/test_config.py -v
uv run ruff check src tests
uv run voxagent paths --data-root "$env:TEMP\voxagent-plan-check"
```

Expected: tests PASS, Ruff exits 0, and the CLI prints an absolute path ending in `voxagent-plan-check`.

- [ ] **Step 6: Add the repository README**

Create `README.md`:

```markdown
# VoxAgent（声灵）

Local-first Windows voice companion for a GTX 1660 Ti / 16GB target machine.

## Repository layout

- `backend/`: Python local service and diagnostics
- `frontend/`: React/Electron client, introduced by Plan 02 and Plan 05
- `benchmarks/`: committed machine-readable benchmark summaries
- `docs/superpowers/specs/`: approved product design
- `docs/superpowers/plans/`: ordered implementation plans

Runtime models, caches, user data, and logs belong under `D:\VoxAgentData` and are never committed.
```

- [ ] **Step 7: Commit the foundation**

```powershell
git add README.md backend benchmarks/.gitkeep
git commit -m "build: scaffold VoxAgent diagnostics package"
```

Expected: commit succeeds and `git status --short` is empty.

### Task 2: Hardware snapshot and preflight policy

**Files:**
- Create: `backend/src/voxagent/diagnostics/__init__.py`
- Create: `backend/src/voxagent/diagnostics/hardware.py`
- Create: `backend/tests/diagnostics/test_hardware.py`
- Modify: `backend/src/voxagent/cli.py`

**Interfaces:**
- Consumes: `AppPaths`
- Produces: `collect_hardware(paths: AppPaths) -> HardwareSnapshot`
- Produces: `evaluate_preflight(snapshot: HardwareSnapshot) -> tuple[PreflightIssue, ...]`
- Produces: Typer command `voxagent preflight --json`

- [ ] **Step 1: Write failing policy tests**

Create `backend/src/voxagent/diagnostics/__init__.py` as an empty file.

Create `backend/tests/diagnostics/test_hardware.py`:

```python
from voxagent.diagnostics.hardware import (
    DiskSnapshot,
    GpuSnapshot,
    HardwareSnapshot,
    evaluate_preflight,
)


def snapshot(*, c_free: float = 20, d_free: float = 100, ram_free: float = 8, vram: int = 6144):
    return HardwareSnapshot(
        cpu_name="Intel Core i5-9300H",
        cpu_cores=4,
        cpu_threads=8,
        ram_total_gb=15.88,
        ram_free_gb=ram_free,
        gpu=GpuSnapshot("NVIDIA GeForce GTX 1660 Ti", vram, 5000, "572.16", "7.5"),
        disks=(DiskSnapshot("C:\\", 200, c_free), DiskSnapshot("D:\\", 557, d_free)),
    )


def test_target_machine_passes_when_headroom_is_available():
    assert evaluate_preflight(snapshot()) == ()


def test_preflight_reports_all_blocking_resource_failures():
    issues = evaluate_preflight(snapshot(c_free=3.7, d_free=10, ram_free=2.5, vram=4096))
    assert {issue.code for issue in issues} == {
        "c_drive_low",
        "data_drive_low",
        "ram_low",
        "vram_unsupported",
    }
    assert all(issue.blocking for issue in issues)
```

- [ ] **Step 2: Run the hardware tests and verify failure**

Run:

```powershell
cd backend
uv run pytest tests/diagnostics/test_hardware.py -v
```

Expected: FAIL during import because `voxagent.diagnostics.hardware` does not exist.

- [ ] **Step 3: Implement collection and policy evaluation**

Create `backend/src/voxagent/diagnostics/hardware.py`:

```python
from __future__ import annotations

import csv
import shutil
import subprocess
import winreg
from dataclasses import asdict, dataclass
from io import StringIO

import psutil

from voxagent.config import AppPaths


@dataclass(frozen=True, slots=True)
class GpuSnapshot:
    name: str
    memory_total_mb: int
    memory_free_mb: int
    driver_version: str
    compute_capability: str


@dataclass(frozen=True, slots=True)
class DiskSnapshot:
    drive: str
    total_gb: float
    free_gb: float


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    cpu_name: str
    cpu_cores: int
    cpu_threads: int
    ram_total_gb: float
    ram_free_gb: float
    gpu: GpuSnapshot | None
    disks: tuple[DiskSnapshot, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    code: str
    message: str
    blocking: bool


def _gb(value: int) -> float:
    return round(value / (1024**3), 2)


def _gpu_snapshot() -> GpuSnapshot | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    command = [
        executable,
        "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
    row = next(csv.reader(StringIO(result.stdout.strip())))
    return GpuSnapshot(row[0].strip(), int(row[1]), int(row[2]), row[3].strip(), row[4].strip())


def _disk(drive: str) -> DiskSnapshot:
    usage = shutil.disk_usage(drive)
    return DiskSnapshot(drive, _gb(usage.total), _gb(usage.free))


def _cpu_name() -> str:
    key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
        value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
    return str(value).strip()


def collect_hardware(paths: AppPaths) -> HardwareSnapshot:
    memory = psutil.virtual_memory()
    return HardwareSnapshot(
        cpu_name=_cpu_name(),
        cpu_cores=psutil.cpu_count(logical=False) or 0,
        cpu_threads=psutil.cpu_count(logical=True) or 0,
        ram_total_gb=_gb(memory.total),
        ram_free_gb=_gb(memory.available),
        gpu=_gpu_snapshot(),
        disks=(_disk("C:\\"), _disk(f"{paths.root.drive}\\")),
    )


def evaluate_preflight(snapshot: HardwareSnapshot) -> tuple[PreflightIssue, ...]:
    disks = {disk.drive.upper(): disk for disk in snapshot.disks}
    issues: list[PreflightIssue] = []
    if disks.get("C:\\") is None or disks["C:\\"].free_gb < 15:
        issues.append(PreflightIssue("c_drive_low", "C: requires at least 15GB free", True))
    data_disk = next((disk for key, disk in disks.items() if key != "C:\\"), None)
    if data_disk is None or data_disk.free_gb < 20:
        issues.append(PreflightIssue("data_drive_low", "Data drive requires 20GB free", True))
    if snapshot.ram_free_gb < 6:
        issues.append(PreflightIssue("ram_low", "Close apps until at least 6GB RAM is available", True))
    if snapshot.gpu is None or snapshot.gpu.memory_total_mb < 6000:
        issues.append(PreflightIssue("vram_unsupported", "A 6GB NVIDIA GPU is required for 4B mode", True))
    return tuple(issues)
```

- [ ] **Step 4: Add the preflight CLI command**

Append these imports and command to `backend/src/voxagent/cli.py`:

```python
import json
from dataclasses import asdict

from voxagent.diagnostics.hardware import collect_hardware, evaluate_preflight


@app.command("preflight")
def preflight(
    data_root: Path | None = typer.Option(None, "--data-root", dir_okay=True),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    paths = AppPaths.from_root(resolve_data_root(data_root))
    snapshot = collect_hardware(paths)
    issues = evaluate_preflight(snapshot)
    payload = {"hardware": snapshot.to_dict(), "issues": [asdict(issue) for issue in issues]}
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo("PASS" if not issues else "BLOCKED")
    if any(issue.blocking for issue in issues):
        raise typer.Exit(code=2)
```

- [ ] **Step 5: Run tests and a real target-machine probe**

Run:

```powershell
cd backend
uv run pytest tests/diagnostics/test_hardware.py -v
uv run ruff check src tests
uv run voxagent preflight --json
```

Expected: tests PASS. On the current machine the final command exits 2 until C: has 15GB free and at least 6GB RAM is available; the JSON must still report GTX 1660 Ti with approximately 6144MB VRAM.

- [ ] **Step 6: Commit hardware diagnostics**

```powershell
git add backend/src/voxagent backend/tests/diagnostics
git commit -m "feat: add target hardware preflight"
```

### Task 3: Ollama client and repeatable LLM benchmark

**Files:**
- Create: `backend/src/voxagent/llm/__init__.py`
- Create: `backend/src/voxagent/llm/ollama.py`
- Create: `backend/src/voxagent/diagnostics/llm_benchmark.py`
- Create: `backend/tests/llm/test_ollama.py`
- Create: `backend/tests/diagnostics/test_llm_benchmark.py`
- Modify: `backend/src/voxagent/cli.py`

**Interfaces:**
- Produces: `OllamaClient.stream_chat(model: str, messages: list[dict[str, str]]) -> AsyncIterator[str]`
- Produces: `run_llm_benchmark(client, model, prompts) -> LlmBenchmark`
- Produces: `voxagent benchmark-llm --model MODEL --output FILE`

- [ ] **Step 1: Write a failing streaming client test**

Create `backend/src/voxagent/llm/__init__.py` as an empty file.

Create `backend/tests/llm/test_ollama.py`:

```python
import json

import httpx
import pytest

from voxagent.llm.ollama import OllamaClient


@pytest.mark.asyncio
async def test_stream_chat_yields_only_content_chunks():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        lines = [
            json.dumps({"message": {"content": "你"}, "done": False}),
            json.dumps({"message": {"content": "好"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]
        return httpx.Response(200, text="\n".join(lines))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(http)
        chunks = [chunk async for chunk in client.stream_chat("qwen", [{"role": "user", "content": "你好"}])]
    assert chunks == ["你", "好"]
```

- [ ] **Step 2: Run the client test and verify failure**

Run:

```powershell
cd backend
uv run pytest tests/llm/test_ollama.py -v
```

Expected: FAIL because `voxagent.llm.ollama` does not exist.

- [ ] **Step 3: Implement the Ollama streaming adapter**

Create `backend/src/voxagent/llm/ollama.py`:

```python
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx


class OllamaClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def stream_chat(
        self,
        model: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {"num_ctx": 8192, "temperature": 0.7, "top_p": 0.8},
        }
        async with self._http.stream("POST", "/api/chat", json=payload, timeout=120) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                item = json.loads(line)
                content = item.get("message", {}).get("content", "")
                if content:
                    yield content
```

- [ ] **Step 4: Write benchmark aggregation tests**

Create `backend/tests/diagnostics/test_llm_benchmark.py`:

```python
import pytest

from voxagent.diagnostics.llm_benchmark import run_llm_benchmark


class FakeClient:
    async def stream_chat(self, model, messages):
        assert model == "test-model"
        assert messages[-1]["role"] == "user"
        yield "第一段"
        yield "第二段"


@pytest.mark.asyncio
async def test_benchmark_records_every_prompt(monkeypatch):
    times = iter([10.0, 10.4, 10.8, 20.0, 20.3, 20.7])
    monkeypatch.setattr("voxagent.diagnostics.llm_benchmark.perf_counter", lambda: next(times))
    result = await run_llm_benchmark(FakeClient(), "test-model", ("你好", "打开记事本"))
    assert len(result.runs) == 2
    assert result.runs[0].ttft_seconds == pytest.approx(0.4)
    assert result.runs[0].total_seconds == pytest.approx(0.8)
    assert result.runs[0].text == "第一段第二段"
```

- [ ] **Step 5: Implement benchmark aggregation**

Create `backend/src/voxagent/diagnostics/llm_benchmark.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter

from voxagent.llm.ollama import OllamaClient


@dataclass(frozen=True, slots=True)
class LlmRun:
    prompt: str
    ttft_seconds: float
    total_seconds: float
    text: str


@dataclass(frozen=True, slots=True)
class LlmBenchmark:
    model: str
    runs: tuple[LlmRun, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


async def run_llm_benchmark(
    client: OllamaClient,
    model: str,
    prompts: tuple[str, ...],
) -> LlmBenchmark:
    runs: list[LlmRun] = []
    for prompt in prompts:
        started = perf_counter()
        first_chunk_at: float | None = None
        chunks: list[str] = []
        async for chunk in client.stream_chat(model, [{"role": "user", "content": prompt}]):
            if first_chunk_at is None:
                first_chunk_at = perf_counter()
            chunks.append(chunk)
        finished = perf_counter()
        if first_chunk_at is None:
            raise RuntimeError(f"Model {model} returned no text for prompt: {prompt}")
        runs.append(
            LlmRun(
                prompt=prompt,
                ttft_seconds=round(first_chunk_at - started, 3),
                total_seconds=round(finished - started, 3),
                text="".join(chunks),
            )
        )
    return LlmBenchmark(model=model, runs=tuple(runs))
```

- [ ] **Step 6: Add the benchmark CLI with fixed prompts**

Add this command to `backend/src/voxagent/cli.py`:

```python
import asyncio

import httpx

from voxagent.diagnostics.llm_benchmark import run_llm_benchmark
from voxagent.llm.ollama import OllamaClient

LLM_BENCHMARK_PROMPTS = (
    "请用两句自然中文介绍你自己，每句不超过二十个字。",
    "用户说他喜欢喝无糖咖啡。请只输出一条适合长期保存的记忆。",
    "用户要求打开记事本。请说明需要调用工具，不要声称已经完成。",
)


@app.command("benchmark-llm")
def benchmark_llm(
    model: str = typer.Option("", "--model"),
    output: Path = typer.Option(Path("benchmark.json"), "--output", dir_okay=False),
) -> None:
    if not model.strip():
        raise typer.BadParameter("--model is required")
    async def execute() -> dict[str, object]:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:11434") as http:
            result = await run_llm_benchmark(OllamaClient(http), model, LLM_BENCHMARK_PROMPTS)
            return result.to_dict()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asyncio.run(execute()), ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 7: Install Ollama, pull the exact candidates, and run benchmarks**

Run from an elevated PowerShell only for installation; model pulls do not need elevation:

```powershell
winget install --id Ollama.Ollama --exact --accept-package-agreements --accept-source-agreements
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama pull qwen3.5:4b
ollama pull qwen3:1.7b
cd backend
uv run voxagent benchmark-llm --model qwen3:4b-instruct-2507-q4_K_M --output ../benchmarks/qwen3-4b.json
uv run voxagent benchmark-llm --model qwen3.5:4b --output ../benchmarks/qwen3.5-4b.json
uv run voxagent benchmark-llm --model qwen3:1.7b --output ../benchmarks/qwen3-1.7b.json
```

Expected: three UTF-8 JSON files exist and each contains three runs with non-empty text. Record `nvidia-smi` before and during each run in the final combined report in Task 5.

- [ ] **Step 8: Run tests and commit**

```powershell
cd backend
uv run pytest tests/llm tests/diagnostics/test_llm_benchmark.py -v
uv run ruff check src tests
cd ..
git add backend benchmarks/qwen3-4b.json benchmarks/qwen3.5-4b.json benchmarks/qwen3-1.7b.json
git commit -m "feat: benchmark local Qwen candidates"
```

### Task 4: Speech model manifest, downloads, and latency probes

**Files:**
- Create: `backend/src/voxagent/speech/__init__.py`
- Create: `backend/src/voxagent/speech/model_manifest.py`
- Create: `scripts/download_speech_models.ps1`
- Create: `backend/src/voxagent/diagnostics/speech_benchmark.py`
- Create: `backend/tests/diagnostics/test_speech_benchmark.py`
- Modify: `backend/src/voxagent/cli.py`

**Interfaces:**
- Produces: immutable `SPEECH_MODELS` manifest with exact URLs and extraction folders
- Produces: `measure_call(label: str, operation: Callable[[], object]) -> TimedResult`
- Produces: an idempotent local download and import-readiness check; Plan 02 owns runtime ASR/TTS adapters and real-time-factor measurements

- [ ] **Step 1: Create and test the exact model manifest**

Create `backend/src/voxagent/speech/__init__.py` as an empty file.

Create `backend/src/voxagent/speech/model_manifest.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpeechModel:
    name: str
    archive_name: str
    url: str
    directory_name: str


SPEECH_MODELS = (
    SpeechModel(
        name="sensevoice-int8",
        archive_name="sensevoice-int8.tar.bz2",
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        directory_name="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
    ),
    SpeechModel(
        name="kokoro-int8-zh-en",
        archive_name="kokoro-int8-multi-lang-v1_1.tar.bz2",
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
        "kokoro-int8-multi-lang-v1_1.tar.bz2",
        directory_name="kokoro-int8-multi-lang-v1_1",
    ),
    SpeechModel(
        name="melo-zh-en",
        archive_name="vits-melo-tts-zh_en.tar.bz2",
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
        "vits-melo-tts-zh_en.tar.bz2",
        directory_name="vits-melo-tts-zh_en",
    ),
)
```

Add this test to `backend/tests/diagnostics/test_speech_benchmark.py`:

```python
from voxagent.speech.model_manifest import SPEECH_MODELS


def test_speech_manifest_has_unique_names_and_https_urls():
    assert len({model.name for model in SPEECH_MODELS}) == 3
    assert all(model.url.startswith("https://github.com/k2-fsa/") for model in SPEECH_MODELS)
```

- [ ] **Step 2: Add the idempotent PowerShell downloader**

Create `scripts/download_speech_models.ps1`:

```powershell
param(
    [Parameter(Mandatory = $false)]
    [string]$DataRoot = 'D:\VoxAgentData'
)

$ErrorActionPreference = 'Stop'
$modelRoot = Join-Path $DataRoot 'models\speech'
New-Item -ItemType Directory -Force -Path $modelRoot | Out-Null

$models = @(
    @{
        Name = 'sensevoice-int8'
        Archive = 'sensevoice-int8.tar.bz2'
        Directory = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
        Url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2'
    },
    @{
        Name = 'kokoro-int8-zh-en'
        Archive = 'kokoro-int8-multi-lang-v1_1.tar.bz2'
        Directory = 'kokoro-int8-multi-lang-v1_1'
        Url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-int8-multi-lang-v1_1.tar.bz2'
    },
    @{
        Name = 'melo-zh-en'
        Archive = 'vits-melo-tts-zh_en.tar.bz2'
        Directory = 'vits-melo-tts-zh_en'
        Url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-melo-tts-zh_en.tar.bz2'
    }
)

foreach ($model in $models) {
    $target = Join-Path $modelRoot $model.Directory
    if (Test-Path -LiteralPath $target) {
        Write-Host "Present: $($model.Name)"
        continue
    }
    $archive = Join-Path $modelRoot $model.Archive
    Invoke-WebRequest -Uri $model.Url -OutFile $archive
    tar.exe -xjf $archive -C $modelRoot
    Remove-Item -LiteralPath $archive
    if (-not (Test-Path -LiteralPath $target)) {
        throw "Extraction failed for $($model.Name)"
    }
}
```

- [ ] **Step 3: Write the failing deterministic timing test**

Append to `backend/tests/diagnostics/test_speech_benchmark.py`:

```python
from voxagent.diagnostics.speech_benchmark import measure_call


def test_measure_call_records_elapsed_and_result(monkeypatch):
    times = iter([5.0, 5.25])
    monkeypatch.setattr("voxagent.diagnostics.speech_benchmark.perf_counter", lambda: next(times))
    timed = measure_call("tts", lambda: 24000)
    assert timed.label == "tts"
    assert timed.elapsed_seconds == 0.25
    assert timed.result == 24000
```

- [ ] **Step 4: Implement the shared timing primitive and fixed TTS script**

Create `backend/src/voxagent/diagnostics/speech_benchmark.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

TTS_BENCHMARK_TEXTS = (
    "你好，我是声灵，很高兴陪你聊聊天。",
    "下午三点提醒我喝水，然后打开记事本。",
    "今天的 meeting 改到晚上八点，请不要忘记。",
)


@dataclass(frozen=True, slots=True)
class TimedResult(Generic[T]):
    label: str
    elapsed_seconds: float
    result: T

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def measure_call(label: str, operation: Callable[[], T]) -> TimedResult[T]:
    started = perf_counter()
    result = operation()
    finished = perf_counter()
    return TimedResult(label, round(finished - started, 3), result)
```

This phase stops at deterministic asset installation and Python runtime import. Plan 02 constructs the ASR/TTS adapters, records real-time factors, and updates the baseline with measured speech results.

- [ ] **Step 5: Install FFmpeg, download models, and run smoke benchmarks**

Run:

```powershell
winget install --id Gyan.FFmpeg --exact --accept-package-agreements --accept-source-agreements
powershell -ExecutionPolicy Bypass -File scripts/download_speech_models.ps1 -DataRoot 'D:\VoxAgentData'
cd backend
uv run pytest tests/diagnostics/test_speech_benchmark.py -v
uv run python -c "import sherpa_onnx; print(sherpa_onnx.__version__)"
```

Expected: FFmpeg resolves from a new PowerShell session, three model directories exist under `D:\VoxAgentData\models\speech`, the tests PASS, and sherpa-onnx prints a version.

- [ ] **Step 6: Commit the manifest and download tooling**

```powershell
git add scripts backend/src/voxagent/speech backend/src/voxagent/diagnostics/speech_benchmark.py backend/tests/diagnostics/test_speech_benchmark.py
git commit -m "build: add offline speech model manifest"
```

### Task 5: Deterministic default selection and combined baseline report

**Files:**
- Create: `backend/src/voxagent/diagnostics/selection.py`
- Create: `backend/tests/diagnostics/test_selection.py`
- Create: `benchmarks/target-machine-baseline.json`
- Create: `benchmarks/README.md`

**Interfaces:**
- Produces: `select_llm(candidates: tuple[CandidateMetric, ...]) -> str`
- Produces: immutable selection thresholds matching the approved design
- Produces: one committed target-machine LLM baseline artifact; Plan 02 appends measured ASR/TTS entries without changing its schema

- [ ] **Step 1: Write failing selection tests**

Create `backend/tests/diagnostics/test_selection.py`:

```python
from voxagent.diagnostics.selection import CandidateMetric, select_llm


def test_quality_model_wins_only_inside_all_limits():
    candidates = (
        CandidateMetric("qwen3:4b-instruct-2507-q4_K_M", 2.1, 4800, True),
        CandidateMetric("qwen3.5:4b", 2.6, 5300, True),
    )
    assert select_llm(candidates) == "qwen3.5:4b"


def test_default_model_wins_when_quality_exceeds_vram_limit():
    candidates = (
        CandidateMetric("qwen3:4b-instruct-2507-q4_K_M", 2.1, 4800, True),
        CandidateMetric("qwen3.5:4b", 2.6, 5500, True),
    )
    assert select_llm(candidates) == "qwen3:4b-instruct-2507-q4_K_M"
```

- [ ] **Step 2: Implement the exact selection rule**

Create `backend/src/voxagent/diagnostics/selection.py`:

```python
from dataclasses import dataclass

DEFAULT_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
QUALITY_MODEL = "qwen3.5:4b"


@dataclass(frozen=True, slots=True)
class CandidateMetric:
    model: str
    p95_ttft_seconds: float
    peak_vram_mb: int
    stable_30_minutes: bool


def select_llm(candidates: tuple[CandidateMetric, ...]) -> str:
    indexed = {candidate.model: candidate for candidate in candidates}
    quality = indexed.get(QUALITY_MODEL)
    if (
        quality is not None
        and quality.p95_ttft_seconds <= 3.0
        and quality.peak_vram_mb <= 5400
        and quality.stable_30_minutes
    ):
        return QUALITY_MODEL
    if DEFAULT_MODEL not in indexed:
        raise ValueError(f"Missing required baseline candidate: {DEFAULT_MODEL}")
    return DEFAULT_MODEL
```

- [ ] **Step 3: Run the complete diagnostic suite**

Run:

```powershell
cd backend
uv run pytest tests/diagnostics tests/llm -v
uv run ruff check src tests
```

Expected: all diagnostic and LLM tests PASS.

- [ ] **Step 4: Create the combined baseline from measured outputs**

Create `benchmarks/target-machine-baseline.json` by combining the actual Task 2–3 outputs. It must contain schema version 1; real UTC capture time; CPU, RAM, GPU, VRAM, and driver from `collect_hardware`; one non-empty LLM candidate object per benchmark artifact; verified `installed` states for SenseVoice, Kokoro, and Melo; and the model returned by `select_llm`. Each candidate object contains model ID, p50/p95 TTFT, p50/p95 total time, peak RSS, peak VRAM, run count, and stability result. Plan 02 appends non-empty `asr_candidates` and `tts_candidates` after it measures the adapters. Do not invent measurements or change the documented model identifiers.

Create `benchmarks/README.md`:

```markdown
# Target-machine benchmarks

These JSON files contain measurements from the Lenovo i5-9300H / GTX 1660 Ti 6GB / 16GB target laptop.

Raw audio, prompts containing user data, model weights, and caches are excluded. A result is accepted only when it was generated locally by the committed diagnostic commands and includes no cloud calls.
```

- [ ] **Step 5: Validate the JSON and commit the accepted baseline**

Run:

```powershell
Get-Content -Raw benchmarks/target-machine-baseline.json | ConvertFrom-Json | Out-Null
git add backend/src/voxagent/diagnostics/selection.py backend/tests/diagnostics/test_selection.py benchmarks
git commit -m "docs: record target machine model baseline"
git status --short
```

Expected: JSON parsing succeeds, commit succeeds, and the worktree is clean.

## Plan 01 Completion Gate

Do not begin Plan 02 until all conditions hold:

- `voxagent preflight --json` reports the real GPU and both drives.
- C: has at least 15GB free and at least 6GB RAM is available during tests.
- All three Ollama models produce non-empty Chinese output locally.
- SenseVoice, Kokoro, and Melo model directories exist and sherpa-onnx imports from the locked environment.
- `benchmarks/target-machine-baseline.json` contains real LLM measurements, verified speech-asset states, and an accepted default LLM.
- The standard runtime wrapper completes preflight; its pytest, Ruff, and baseline-validator commands pass.
