# Target-machine benchmarks

These JSON files contain measurements from the Lenovo i5-9300H / GTX 1660 Ti 6GB / 16GB target laptop.

Raw audio, prompts containing user data, model weights, and caches are excluded. Timing results come from committed local CLI benchmark artifacts; RSS and VRAM results come from committed, sanitized raw resource probes. Process IDs, command lines, remoting metadata, and absolute paths were removed from those probes without changing timestamps or measurements. No cloud calls are included. The qwen3.5 timing source retained before its soak is archived byte-for-byte under `archive/`; the current normalized timing JSON adds only `schema_version=1` and `run-1`/`run-2`/`run-3` identifiers. Its prompts, latency values, and response text are unchanged, and the validator reconstructs and compares that conversion.

`target-machine-baseline.json` combines the three raw local Ollama artifacts. Its p50 is the three-run median and its p95 is nearest-rank p95 (the maximum for three samples). Every candidate carries timing and probe provenance, the read-only Ollama tag digest/local-manifest hash, and a reference to the shared final machine snapshot. The snapshot is a final reference, not a claim that all runs were simultaneous. The retained historical RSS/VRAM probes disclose that their target PIDs were not preserved; future schema-2 probes strictly map the immutable model blob to one `llama-server` PID and reject missing or ambiguous attribution.

The quality-model soak failure is documented in `qwen3.5-soak-summary.json`. Raw soak samples were not retained, so the file honestly records observation bounds rather than claiming a recomputable series. The committed harness can reproduce future fixed probes or 30-minute soaks, but it cannot recreate telemetry that was never retained.

Validate all byte hashes, timing aggregates, probe aggregates/intervals, and the final selection:

```powershell
cd backend
uv run voxagent validate-baseline --baseline ../benchmarks/target-machine-baseline.json --json
```

The validator exits nonzero on any mismatch. `voxagent probe-resources --help` documents the observation/soak harness; current schema 3 records immutable target identity, target PID, GPU attribution, sampling configuration, raw samples, sampler errors, stop reason, and memory-pressure thresholds. Historical schema-2 evidence remains unchanged. On Windows WDDM, per-PID `[N/A]` can fall back to whole-device memory only for a unique compute PID and is labeled `unique_compute_process_total_gpu`; missing, ambiguous, or failed GPU telemetry is never converted to zero and makes a strict probe fail. Run live probes through `scripts\voxagent_runtime.ps1 -RequireVerifiedOllama -Command { ... }`; unlike local-only validation, the live path blocks an unverifiable existing Ollama service. Plan 02 will append measured ASR and TTS candidates without changing the existing LLM evidence.
