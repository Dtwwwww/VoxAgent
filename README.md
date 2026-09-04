# VoxAgent（声灵）

Local-first Windows voice companion for a GTX 1660 Ti / 16GB target machine.

## Repository layout

- `backend/`: Python local service and diagnostics
- `frontend/`: React/Electron client, introduced by Plan 02 and Plan 05
- `benchmarks/`: committed machine-readable benchmark summaries
- [`docs/plan-01/README.md`](docs/plan-01/README.md): Plan 1 manual startup and model commands
- [`docs/plan-02/README.md`](docs/plan-02/README.md): Plan 2 Web text and voice startup guide
- `docs/superpowers/specs/`: approved product design
- `docs/superpowers/plans/`: ordered implementation plans

Runtime models, caches, user data, and logs default to `D:\VoxAgentData` and are never
committed. Override the root with `-DataRoot` or `VOXAGENT_DATA_ROOT`; the selected root is
the single source for Ollama models, speech models, uv cache, application data, logs, and test
temporary files.

## Standard local runtime entry

From PowerShell at the repository root:

```powershell
& .\scripts\voxagent_runtime.ps1
```

This creates only the selected runtime directories, constructs `OLLAMA_MODELS`, `UV_CACHE_DIR`,
speech/model roots and temporary paths for a supplied `-Command`, then runs the C: plus
selected-data-drive preflight. With no `-Command`, it reports those child-process values but does
not persist them in the calling shell. It never starts, stops, or reconfigures Ollama and does not
download a model.

`OLLAMA_NO_CLOUD=1` and `OLLAMA_HOST=127.0.0.1:11434` apply to wrapper child processes. They do
not retroactively change an already-running Ollama server. Commands that contact Ollama must add
`-RequireVerifiedOllama`; the wrapper then blocks unless port 11434 is loopback-only, API version
and the exact API tag inventory/digests match the committed baseline and selected-root manifests.
The server environment must be readable, contain `OLLAMA_NO_CLOUD=1`, and set `OLLAMA_MODELS`
exactly to the selected root's `models\ollama` directory after Windows path normalization. Missing
environment data is explicitly `unverified`, not assumed safe.

Run repository commands through the same prepared environment:

```powershell
& .\scripts\voxagent_runtime.ps1 -Command {
    Push-Location backend
    uv run --extra dev --extra speech pytest -q
    Pop-Location
}
```

To use another drive, pass (for example) `-DataRoot 'E:\VoxAgentData'`. Preflight then checks
C: and E: only. Speech downloads use the same gate and root:

```powershell
& .\scripts\download_speech_models.ps1 -DataRoot 'D:\VoxAgentData'
```

The download script verifies fixed SHA-256 checksums before publishing staged assets. Existing
assets are skipped only when their required files and JSON completion marker match the expected
version and archive hash.

## Baseline verification and resource probes

```powershell
& .\scripts\voxagent_runtime.ps1 -Command {
    Push-Location backend
    uv run voxagent validate-baseline --baseline ../benchmarks/target-machine-baseline.json --json
    Pop-Location
}
& .\scripts\voxagent_runtime.ps1 -RequireVerifiedOllama -Command {
    Push-Location backend
    uv run voxagent probe-resources --model qwen3:4b-instruct-2507-q4_K_M `
      --manifest-sha256 0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0 `
      --model-blob-digest sha256:85e4a5b7b8ef0e48af0e8658f5aaab9c2324c76c1641493f4d1e25fce54b18b9 `
      --mode observe --duration-seconds 60 --output ../benchmarks/new-probe.json
    Pop-Location
}
```

`probe-resources --mode soak` repeatedly calls only the loopback Ollama endpoint while sampling.
Memory-pressure limits stop the harness itself; the command never terminates Ollama or any other
process. The immutable manifest/model-blob identity maps the probe to one runner PID; missing or
ambiguous attribution fails. WDDM `[N/A]` per-PID VRAM is never recorded as zero: the harness may
use whole-device `memory.used` only when that runner is the sole compute process, recording
`unique_compute_process_total_gpu`; otherwise the strict probe exits nonzero as unavailable.
Review and sanitize new raw evidence before committing it.

The same verification gate is mandatory for a live timing run:

```powershell
& .\scripts\voxagent_runtime.ps1 -RequireVerifiedOllama -Command {
    Push-Location backend
    uv run voxagent benchmark-llm --model qwen3:4b-instruct-2507-q4_K_M `
      --output ../benchmarks/new-qwen3-4b.json
    Pop-Location
}
```
