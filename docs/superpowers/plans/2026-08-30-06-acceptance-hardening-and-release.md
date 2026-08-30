# Acceptance, Hardening, and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove VoxAgent is private, stable, responsive, recoverable, and legally distributable on the target laptop before calling version 1 complete.

**Architecture:** A release harness runs unit/integration/security suites, observes outbound networking, drives deterministic voice/tool scenarios, and records machine-readable evidence. Manual checks cover naturalness, accessibility, long-running thermals, install/upgrade behavior, and user-understandable privacy controls. Release is blocked unless every required artifact has a passing status and traceable build SHA.

**Tech Stack:** pytest, Vitest, Playwright, Ruff, TypeScript, Electron security checks, PowerShell, Windows Resource Monitor/netstat, nvidia-smi, FFmpeg/ffprobe, pip-licenses, license-checker-rseidelsohn, CycloneDX SBOM.

## Global Constraints

- Complete Plans 01–05; this plan fixes release-blocking defects but introduces no new product features.
- Test on the inspected Lenovo i5-9300H, 16 GB RAM, GTX 1660 Ti 6 GB, Windows 11 machine.
- Free at least 15 GB on C: before acceptance and keep runtime assets under `D:\VoxAgentData`.
- Close unrelated memory-heavy programs until at least 6 GB RAM is available at test start.
- Never commit real transcripts, recordings, credentials, document contents, database copies, or absolute personal paths.
- Every report includes build commit, UTC timestamp, hardware snapshot ID, configuration profile, pass/fail, and failure reason.
- A failed required gate blocks release; do not waive privacy, destructive-action, token-exposure, or stale-audio failures.
- GitHub projects are references unless copied code is explicitly recorded with source commit and license.

---

## Planned File Structure

```text
qa/
  README.md
  scenarios/voice_turns.json
  scenarios/tool_calls.json
  scripts/run_all.ps1
  scripts/network_audit.ps1
  scripts/soak_test.ps1
  scripts/collect_metrics.ps1
  reports/.gitkeep
backend/tests/security/
  test_auth_boundaries.py
  test_prompt_injection.py
  test_sensitive_logging.py
frontend/e2e/
  first_run.spec.ts
  voice_controls.spec.ts
  memory_tools.spec.ts
desktop/e2e/
  lifecycle.spec.ts
docs/release/
  privacy-verification.md
  open-source-notices.md
  release-checklist.md
  rollback.md
```

### Task 1: One-command verification harness

**Files:**
- Create: `qa/README.md`
- Create: `qa/scripts/run_all.ps1`
- Create: `qa/reports/.gitkeep`
- Modify: `backend/pyproject.toml`
- Modify: `frontend/package.json`
- Modify: `desktop/package.json`

**Interfaces:**
- Produces: `qa/reports/automated-<build-sha>.json`
- Returns exit 0 only when all required suites pass

- [ ] **Step 1: Define suite order and failure behavior**

Run backend `pytest` then Ruff, frontend Vitest then TypeScript then production build, desktop Vitest then TypeScript then Electronegativity, PyInstaller build, Electron unpacked build, and smoke launch. Capture each command, exit code, duration, and tool version. Stop product packaging on failure but continue independent test suites so one run reveals all defects.

- [ ] **Step 2: Implement safe PowerShell orchestration**

Resolve the repository root from `$PSScriptRoot`; never depend on current directory. Invoke each executable with argument arrays rather than constructed command strings. Write JSON through `ConvertTo-Json` and `Set-Content -Encoding utf8` only within the validated `qa/reports` directory.

- [ ] **Step 3: Add coverage floors**

Require 85% branch coverage for `tools/policy.py`, `tools/confirmation.py`, `tools/path_policy.py`, `cloud/gateway.py`, and Electron security/IPC modules; require 75% overall line coverage for backend, frontend, and desktop. Exclude generated protocol types and packaging output only.

- [ ] **Step 4: Run and commit the harness**

```powershell
powershell -ExecutionPolicy Bypass -File qa/scripts/run_all.ps1
git add qa backend/pyproject.toml frontend/package.json desktop/package.json
git commit -m "test: add unified release verification harness"
```

Expected: a JSON report exists for the current commit and all required suite statuses are `passed`.

### Task 2: Authentication, injection, and sensitive-data security suite

**Files:**
- Create: `backend/tests/security/test_auth_boundaries.py`
- Create: `backend/tests/security/test_prompt_injection.py`
- Create: `backend/tests/security/test_sensitive_logging.py`
- Create: `docs/release/privacy-verification.md`

**Interfaces:**
- Tests every HTTP, WebSocket, IPC, tool, memory, export, and cloud trust boundary
- Produces a completed privacy-verification record

- [ ] **Step 1: Test authentication boundaries**

Enumerate every `/v1` route and assert no token, wrong token, expired token, replay from another Electron PID, malformed body, oversized body, and cross-origin request fail safely. Assert health/version endpoints expose no token, port history, model path, data path, user name, or source path.

- [ ] **Step 2: Test prompt and document injection**

Feed Chinese/English attempts to invent tools, override policy, extract memory, reveal system prompts, open outside-root files, exfiltrate documents, and authorize cloud. Assert retrieved content remains quoted data, the closed registry rejects unknown calls, and confirmation cannot be produced by model text.

- [ ] **Step 3: Test redaction across every output channel**

Seed canary API keys, passwords, resident-ID-like strings, private-key headers, absolute paths, and a fake bearer token. Exercise failures, exports, logs, diagnostics ZIP, audit API, crash handling, Web events, and notifications. Byte-search all produced artifacts and fail if any canary appears.

- [ ] **Step 4: Test malformed audio and resource exhaustion**

Send oversized frames, wrong sample rates, binary floods, repeated socket opens, never-ending speech, huge LLM deltas, and 50 queued TTS chunks. Assert bounded queues, timeouts, one active session, controlled errors, and process recovery without stale tasks.

- [ ] **Step 5: Run and document**

```powershell
cd backend
uv run pytest tests/security -v
uv run ruff check src tests
cd ..
git add backend/tests/security docs/release/privacy-verification.md
git commit -m "security: verify local trust boundaries"
```

### Task 3: Outbound network audit

**Files:**
- Create: `qa/scripts/network_audit.ps1`
- Create: `qa/scenarios/voice_turns.json`
- Create: `backend/tests/security/test_network_policy.py`

**Interfaces:**
- Produces: `qa/reports/network-local-only-<build-sha>.json`
- Produces: `qa/reports/network-cloud-approved-<build-sha>.json`

- [ ] **Step 1: Define allowed destinations**

In local-only mode allow loopback TCP only. During explicit model download allow the exact configured Ollama/model-source HTTPS hosts. During one approved cloud test allow the configured provider host for that single request. Deny and record every other remote endpoint.

- [ ] **Step 2: Implement process-tree observation**

Start the unpacked app, collect Electron/backend/Ollama child PIDs, sample `Get-NetTCPConnection` every 100 ms, map remote addresses and ports, and record process ID plus executable hash. Ignore listening sockets; classify only established outbound connections.

- [ ] **Step 3: Exercise deterministic local scenarios**

Run ten voice turns, five memory operations, one document import/query/delete, all seven tools with confirmations, diagnostic export, and app restart. Cloud remains unconfigured. Fail if any non-loopback established connection appears.

- [ ] **Step 4: Exercise one approved and one denied cloud call**

Use a test provider endpoint with no personal content. Verify the denied call opens no connection; the approved call reaches only the configured host, sends only the previewed payload hash, and cannot be repeated with the same approval.

- [ ] **Step 5: Commit scripts and sanitized reports**

```powershell
powershell -ExecutionPolicy Bypass -File qa/scripts/network_audit.ps1 -Mode LocalOnly
powershell -ExecutionPolicy Bypass -File qa/scripts/network_audit.ps1 -Mode CloudApprovalTest
git add qa backend/tests/security/test_network_policy.py
git commit -m "security: audit outbound network behavior"
```

### Task 4: Voice quality, endpoint, and barge-in acceptance

**Files:**
- Modify: `qa/scenarios/voice_turns.json`
- Create: `qa/scripts/collect_metrics.ps1`
- Create: `qa/README.md`

**Interfaces:**
- Produces: `qa/reports/voice-quality-<build-sha>.json`
- Measures: endpoint delay, ASR word/character accuracy, LLM TTFT, TTS first audio, end-to-end first audio, barge-in stop delay

- [ ] **Step 1: Record a privacy-safe fixed corpus**

Create 30 Mandarin utterances without personal facts: ten complete short turns, ten with 0.9–1.2 second internal pauses, and ten with fillers/incomplete endings. Record locally at 16 kHz mono and store only SHA-256 plus expected text in Git; keep WAV files under ignored `qa/private-fixtures`.

- [ ] **Step 2: Measure endpoint behavior**

From the Start Conversation click, microphone capture and listening indication must become active within one second. Natural mode must not commit any internal pause at 0.9 seconds. Complete turns should commit near 1.35 seconds with tolerance ±0.2 seconds. Filler/incomplete endings should extend to 2.0 seconds with tolerance ±0.2 seconds. Fast/patient profiles must measure near 0.8/2.0 seconds.

- [ ] **Step 3: Measure ASR and response latency**

Require Mandarin character error rate at most 12% in a quiet room, p95 ASR real-time factor at most 0.7, p95 LLM TTFT at most 3.0 seconds, p95 TTS first audio at most 1.2 seconds, and p95 endpoint-to-first-audio at most 4.0 seconds.

- [ ] **Step 4: Run 20 barge-ins**

Interrupt at the beginning, middle, and end of spoken replies. Require all browser audio sources to stop within 200 ms, no stale chunk to play, no cancelled answer to enter history as complete, and the new turn to reach ASR.

- [ ] **Step 5: Complete a blinded voice review**

Score pronunciation, intelligibility, rhythm, and naturalness for 20 outputs on a 1–5 scale. Each category mean must be at least 3.5 and no safety-critical instruction may be mispronounced enough to change meaning. Record scores and randomized sample IDs, not generated audio.

- [ ] **Step 6: Commit sanitized evidence**

```powershell
powershell -ExecutionPolicy Bypass -File qa/scripts/collect_metrics.ps1 -Suite Voice
git add qa/scenarios/voice_turns.json qa/README.md qa/reports/voice-quality-*.json
git commit -m "test: validate Mandarin voice experience"
```

### Task 5: Safe-tool and data-lifecycle acceptance

**Files:**
- Create: `qa/scenarios/tool_calls.json`
- Create: `frontend/e2e/memory_tools.spec.ts`
- Create: `docs/release/rollback.md`

**Interfaces:**
- Produces: `qa/reports/tools-data-<build-sha>.json`

- [ ] **Step 1: Build a 50-case tool matrix**

Include valid L0/L1/L2 calls, ambiguous app names, outside-root paths, junction escapes, expired/replayed/modified tickets, denied confirmations, reminder timezone edges, invented tools, shell injection strings, and executor failures. Expected outcomes are explicit tool status and audit event type.

- [ ] **Step 2: Exercise confirmations end to end**

Use Playwright against the packaged renderer. Prove only button clicks approve actions, new turns expire cards, standing permission applies only to the selected app, L2 never offers standing permission, and result speech matches the real executor result.

- [ ] **Step 3: Exercise memory/document lifecycle**

Create, edit, deduplicate, retrieve, reject, and delete memories. Import every supported document type, verify source excerpt attribution, export data, and perform full reset. After reset, inspect SQLite and data directories to prove no user rows, vectors, cached exports, or copied sources remain.

- [ ] **Step 4: Verify backup and rollback procedure**

Copy the closed SQLite database plus WAL/SHM only after backend shutdown, hash the copy, upgrade the app, and restore it only with the newer app stopped. Document schema compatibility, model re-download behavior, settings rollback, and how to preserve `D:\VoxAgentData` during reinstall.

- [ ] **Step 5: Commit**

```powershell
git add qa/scenarios/tool_calls.json frontend/e2e/memory_tools.spec.ts docs/release/rollback.md qa/reports/tools-data-*.json
git commit -m "test: validate safe tools and data lifecycle"
```

### Task 6: Thirty-minute soak, thermal, and recovery test

**Files:**
- Create: `qa/scripts/soak_test.ps1`
- Create: `desktop/e2e/lifecycle.spec.ts`

**Interfaces:**
- Produces: `qa/reports/soak-<build-sha>.json`
- Samples every five seconds: RAM, CPU, GPU utilization, VRAM, temperature, queue depth, process restarts

- [ ] **Step 1: Define the mixed workload**

Over 30 minutes execute one voice turn per minute, five barge-ins, ten memory retrievals, five document queries, five file searches, two confirmed app opens, two reminders, window minimize/restore, microphone disconnect/reconnect, and one controlled Ollama restart.

- [ ] **Step 2: Set objective stability thresholds**

Peak VRAM must stay at or below 5.4 GB; backend RSS growth from minute 5 to minute 30 must stay below 300 MB; renderer RSS growth below 200 MB; no unbounded queue; no more than one controlled backend restart; GPU temperature below the hardware thermal limit and without sustained throttling; all cancelled turns release tasks within five seconds.

- [ ] **Step 3: Test crash and dependency recovery**

Terminate backend, stop Ollama, remove microphone access, temporarily lock the database, and make one speech asset unavailable in separate runs. Verify bounded Chinese error states, at most three backend restart attempts, no data corruption, and successful manual recovery.

- [ ] **Step 4: Run and commit the sanitized report**

```powershell
powershell -ExecutionPolicy Bypass -File qa/scripts/soak_test.ps1 -Minutes 30
git add qa/scripts/soak_test.ps1 desktop/e2e/lifecycle.spec.ts qa/reports/soak-*.json
git commit -m "test: prove target laptop stability"
```

### Task 7: Accessibility and first-run usability

**Files:**
- Create: `frontend/e2e/first_run.spec.ts`
- Create: `frontend/e2e/voice_controls.spec.ts`
- Modify: `docs/release/release-checklist.md`

- [ ] **Step 1: Test keyboard and screen-reader semantics**

All controls must be reachable in logical tab order, have Chinese accessible names, show visible focus, and work without a mouse except OS file dialogs. Status changes use polite live regions; confirmation/error changes use assertive announcements only when action is required.

- [ ] **Step 2: Test scale and contrast**

Verify 1280×720 at 100%, 150%, and 200% Windows/browser scale with no hidden Start/Stop or Confirm/Deny controls. Text and controls meet WCAG AA contrast; state is never conveyed by color alone.

- [ ] **Step 3: Run a clean-user first-run walkthrough**

The user can choose D: storage, see the C: cleanup requirement, install/pull models, understand microphone use, complete a first voice turn, find endpoint settings, inspect memory, and discover that cloud is off. Time the flow and record every unclear phrase as a release issue.

- [ ] **Step 4: Verify and commit**

```powershell
cd frontend
pnpm exec playwright test e2e/first_run.spec.ts e2e/voice_controls.spec.ts
cd ..
git add frontend/e2e docs/release/release-checklist.md
git commit -m "test: verify accessible first-run experience"
```

### Task 8: Open-source provenance, SBOM, and final release gate

**Files:**
- Create: `docs/release/open-source-notices.md`
- Create: `docs/release/release-checklist.md`
- Create: `docs/release/third-party-source-lock.json`
- Create: `dist/sbom-python.json`
- Create: `dist/sbom-node.json`

**Interfaces:**
- Produces dependency licenses, source provenance, two SBOMs, installer SHA-256, and signed checklist

- [ ] **Step 1: Record GitHub reference decisions**

Record Open-LLM-VTuber, Pipecat, TEN Framework, and Leon with repository URL, inspected commit, license, concept studied, and `copied_code=false` unless a later implementation deliberately copied code. For copied code, record exact source file/commit, local destination, copyright notice, and license obligations.

- [ ] **Step 2: Generate dependency inventories**

Use locked Python/Node dependency graphs. Fail on unknown licenses or licenses incompatible with distribution. Include model names, source URLs, licenses, hashes, and whether model files are downloaded after install rather than redistributed.

- [ ] **Step 3: Generate CycloneDX SBOMs and notices**

Create Python and Node SBOMs from lockfiles, then produce human-readable notices for application, models, and bundled native libraries. Do not include data-root contents.

- [ ] **Step 4: Execute the complete release checklist**

Require passing artifacts from automated verification, privacy/security, network audit, voice quality, tool/data lifecycle, soak, accessibility, installer upgrade/uninstall, licenses, and SBOM. Record installer SHA-256 and Git commit. Any missing or failing required artifact sets overall status to `blocked`.

- [ ] **Step 5: Build the release candidate and commit evidence**

```powershell
powershell -ExecutionPolicy Bypass -File qa/scripts/run_all.ps1
powershell -ExecutionPolicy Bypass -File scripts/build_desktop.ps1
Get-FileHash -Algorithm SHA256 dist/VoxAgent-Setup-x64.exe
git add docs/release qa dist/sbom-python.json dist/sbom-node.json
git commit -m "release: approve VoxAgent v1 candidate"
git status --short
```

Expected: the checklist says `approved`, every referenced report matches the release commit/build, and the only untracked/ignored large files are local models, recordings, user data, and build cache.

## Plan 06 Completion Gate

- All automated suites and coverage floors pass from one command.
- Local-only scenarios establish no non-loopback outbound connections.
- Canary secrets are absent from logs, APIs, exports, crashes, notifications, and diagnostics.
- Natural endpointing, ASR, response latency, TTS, and 20 barge-ins meet numeric gates.
- All 50 safe-tool cases and full data deletion/restore checks pass.
- Thirty-minute target-hardware soak stays within RAM/VRAM/thermal limits and recovers from injected failures.
- First run is keyboard accessible and understandable at 200% scale.
- Licenses, GitHub provenance, model terms, SBOMs, installer hash, rollback instructions, and release checklist are complete.
- The release candidate is approved only when every required gate passes.
