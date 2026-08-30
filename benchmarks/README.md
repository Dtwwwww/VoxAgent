# Target-machine benchmarks

These JSON files contain measurements from the Lenovo i5-9300H / GTX 1660 Ti 6GB / 16GB target laptop.

Raw audio, prompts containing user data, model weights, and caches are excluded. Timing results come from committed local CLI benchmark artifacts; RSS and VRAM results come from recorded local resource probes. No cloud calls are included.

`target-machine-baseline.json` combines the three raw local Ollama artifacts. Its p50 is the three-run median and its p95 is nearest-rank p95 (the maximum for three samples). Every candidate carries timing and probe provenance plus a reference to the shared final machine snapshot. The snapshot is a final reference, not a claim that all runs were simultaneous. RSS and VRAM values come from separate local resource probes of the actual `llama-server` runner at 8192 context. The quality-model soak failure is documented in the sanitized `qwen3.5-soak-summary.json`; raw soak samples were not retained, so it records auditable observation bounds rather than a recomputable sample series. Plan 02 will append measured ASR and TTS candidates without changing the schema.
