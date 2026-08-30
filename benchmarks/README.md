# Target-machine benchmarks

These JSON files contain measurements from the Lenovo i5-9300H / GTX 1660 Ti 6GB / 16GB target laptop.

Raw audio, prompts containing user data, model weights, and caches are excluded. A result is accepted only when it was generated locally by the committed diagnostic commands and includes no cloud calls.

`target-machine-baseline.json` combines the three raw local Ollama artifacts. Its p50 is the three-run median and its p95 is nearest-rank p95 (the maximum for three samples). RSS and VRAM values come from separate local resource probes of the actual `llama-server` runner at 8192 context. Plan 02 will append measured ASR and TTS candidates without changing the schema.
