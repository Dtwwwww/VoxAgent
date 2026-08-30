# VoxAgent（声灵）

Local-first Windows voice companion for a GTX 1660 Ti / 16GB target machine.

## Repository layout

- `backend/`: Python local service and diagnostics
- `frontend/`: React/Electron client, introduced by Plan 02 and Plan 05
- `benchmarks/`: committed machine-readable benchmark summaries
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

This creates only the selected runtime directories, exports `OLLAMA_MODELS`, `UV_CACHE_DIR`,
speech/model roots and temporary paths, then runs the C: plus selected-data-drive preflight.
It sets `OLLAMA_NO_CLOUD=1` and `OLLAMA_HOST=127.0.0.1:11434`. With no `-Command`, it does not
start or stop Ollama, download a model, or run an application.

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
Push-Location backend
uv run voxagent validate-baseline --baseline ../benchmarks/target-machine-baseline.json --json
uv run voxagent probe-resources --model qwen3:4b-instruct-2507-q4_K_M `
  --mode observe --duration-seconds 60 --output ../benchmarks/new-probe.json
Pop-Location
```

`probe-resources --mode soak` repeatedly calls only the loopback Ollama endpoint while sampling.
Memory-pressure limits stop the harness itself; the command never terminates Ollama or any other
process. Review and sanitize new raw evidence before committing it.
