# Electron Desktop Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the Web voice companion as a secure Windows desktop application that starts its private Python backend, manages models/settings, survives failures, and installs without placing large data on C:.

**Architecture:** Electron owns the application lifecycle and is the only process allowed to generate the session secret. A narrow context-isolated preload bridge gives the React renderer non-secret operations; the main process injects authenticated requests and starts a packaged Python service on a random localhost port. Models and user data remain in `D:\VoxAgentData`; only program files live under the normal per-user install location.

**Tech Stack:** Electron 44, electron-builder 26, React 19, TypeScript 7, Node 24 build runtime, Python 3.12, PyInstaller, Windows Credential Manager, electron-log, Vitest, pytest.

## Global Constraints

- Complete Plans 01–04 first and preserve their backend protocol, database, and model IDs.
- The renderer has `nodeIntegration=false`, `contextIsolation=true`, `sandbox=true`, and no direct session-token access.
- The backend binds `127.0.0.1` on an OS-assigned port and accepts only Electron-generated credentials.
- The token must not appear in Local Storage, Session Storage, renderer logs, command-line arguments, URLs, crash dumps, or exported settings.
- Content Security Policy permits only packaged local assets plus the exact runtime localhost origin; no remote scripts or eval.
- The app never runs elevated and never adds itself to startup without a separate explicit choice.
- Models, database, logs, downloads, and exports default to `D:\VoxAgentData`.
- Install and update flows must not delete or migrate user data automatically.
- Cloud use is disabled by default and requires both a configured provider and per-request visual confirmation.

---

## Planned File Structure

```text
desktop/
  package.json
  tsconfig.json
  electron-builder.yml
  src/main.ts
  src/preload.ts
  src/backendProcess.ts
  src/ipc.ts
  src/security.ts
  src/settings.ts
  src/notifications.ts
  tests/backendProcess.test.ts
  tests/security.test.ts
  tests/settings.test.ts
backend/
  voxagent.spec
  src/voxagent/bootstrap.py
  src/voxagent/api/electron_auth.py
  src/voxagent/cloud/gateway.py
  src/voxagent/cloud/credentials.py
  tests/test_bootstrap.py
  tests/cloud/test_gateway.py
frontend/src/desktopBridge.ts
scripts/build_backend.ps1
scripts/build_desktop.ps1
```

### Task 1: Packageable backend bootstrap contract

**Files:**
- Create: `backend/src/voxagent/bootstrap.py`
- Create: `backend/src/voxagent/api/electron_auth.py`
- Create: `backend/tests/test_bootstrap.py`
- Create: `backend/voxagent.spec`
- Create: `scripts/build_backend.ps1`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes environment: `VOXAGENT_DATA_ROOT`, `VOXAGENT_AUTH_PIPE`, `VOXAGENT_PARENT_PID`
- Produces one private named-pipe handshake containing `{port, token_hash, pid, protocol_version}`
- Produces executable: `dist/backend/voxagent-backend.exe`

- [ ] **Step 1: Test secure bootstrap validation**

Prove startup fails when data root, pipe name, or parent PID is missing; refuses a non-local data path; exits when the parent process disappears; and writes no bearer token to stdout/stderr. Patch socket binding so tests verify host `127.0.0.1` and port `0`.

- [ ] **Step 2: Implement named-pipe secret transfer**

Electron creates a random 32-byte token and a user-scoped named pipe with a random name. It sends the token through the pipe after validating the backend PID. The backend returns only the SHA-256 token hash plus selected port through the same pipe. The clear token exists only in Electron main memory and backend process memory.

- [ ] **Step 3: Implement parent-death monitoring and graceful shutdown**

Poll the supplied parent PID every two seconds. On parent exit, stop accepting sessions, cancel active voice/tool tasks, checkpoint SQLite WAL, and exit within five seconds. Handle `CTRL_CLOSE_EVENT` the same way.

- [ ] **Step 4: Create the PyInstaller spec**

Bundle Python modules, sherpa-onnx runtime libraries, and migrations; do not bundle model weights, database files, benchmark fixtures, or logs. Build one directory rather than one file to reduce startup extraction overhead.

- [ ] **Step 5: Build, smoke-test, and commit**

```powershell
cd backend
uv add --dev "pyinstaller>=6.15,<7"
uv run pytest tests/test_bootstrap.py -v
cd ..
powershell -ExecutionPolicy Bypass -File scripts/build_backend.ps1
dist/backend/voxagent-backend.exe --version
git add backend scripts/build_backend.ps1
git commit -m "build: package private local backend"
```

Expected: the executable prints only the VoxAgent version and exits 0; normal start without the required pipe exits nonzero.

### Task 2: Electron lifecycle and least-privilege preload bridge

**Files:**
- Create: `desktop/package.json`
- Create: `desktop/tsconfig.json`
- Create: `desktop/src/main.ts`
- Create: `desktop/src/preload.ts`
- Create: `desktop/src/backendProcess.ts`
- Create: `desktop/src/ipc.ts`
- Create: `desktop/tests/backendProcess.test.ts`
- Create: `frontend/src/desktopBridge.ts`

**Interfaces:**
- Produces preload API: `getRuntimeInfo`, `startVoice`, `stopVoice`, `sendAudio`, `subscribe`, `invokeApi`, `chooseFile`, `chooseDirectory`
- Does not produce: shell, filesystem, raw IPC, raw token, or arbitrary URL methods

- [ ] **Step 1: Test backend process lifecycle**

Mock Electron and child process APIs. Assert one backend starts after `app.whenReady`, restart uses exponential delays of 1/2/4 seconds with a maximum of three attempts, app quit terminates the child, and stdout/stderr redaction removes token-like values and query strings.

- [ ] **Step 2: Implement the main-process backend controller**

Resolve the packaged executable from `process.resourcesPath`, use a random named pipe, pass only non-secret environment variables, wait up to 20 seconds for handshake/health, and kill the child on failed authentication. In development, require an explicit `VOXAGENT_DEV_BACKEND` executable path rather than invoking a shell command.

- [ ] **Step 3: Implement typed IPC channels**

Hard-code channel names and validate payloads in main and preload. `invokeApi` accepts only an enum of known API operations and typed JSON payloads; main attaches the bearer token. File/directory pickers return paths only to main, which forwards validated selections to the backend without exposing absolute paths to unrelated renderer code.

- [ ] **Step 4: Adapt the React session hook**

Use the preload bridge when `window.voxagent` exists and retain direct localhost transport only for Vite development. Production renderer code must never construct a backend URL or read a token.

- [ ] **Step 5: Verify and commit**

```powershell
cd desktop
pnpm install
pnpm test -- --run
pnpm exec tsc --noEmit
cd ../frontend
pnpm test -- --run
pnpm exec tsc --noEmit
cd ..
git add desktop frontend/src/desktopBridge.ts frontend/src/useVoiceSession.ts
git commit -m "feat: host voice client in secure Electron shell"
```

### Task 3: Electron navigation, CSP, and permission hardening

**Files:**
- Create: `desktop/src/security.ts`
- Create: `desktop/tests/security.test.ts`
- Modify: `desktop/src/main.ts`

**Interfaces:**
- Produces: `configureSecurity(session, appOrigin, backendOrigin)`
- Produces: deterministic CSP and permission decisions

- [ ] **Step 1: Write attack-surface tests**

Assert navigation away from the packaged app is denied, `window.open` is denied, WebView attachment is denied, certificate errors fail closed, permission requests other than microphone are denied, and microphone permission is allowed only for the exact app origin after a recent user gesture.

- [ ] **Step 2: Install runtime guards before creating a window**

Register `will-navigate`, `setWindowOpenHandler`, `will-attach-webview`, permission request/check handlers, and certificate-error handling. Remove application menus that expose DevTools in production. Keep DevTools available behind a development build flag only.

- [ ] **Step 3: Apply a restrictive CSP**

Use `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; media-src 'self' blob:; connect-src 'self' BACKEND_ORIGIN; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`. Replace only `BACKEND_ORIGIN` in main memory after handshake.

- [ ] **Step 4: Run Electronegativity and commit**

```powershell
cd desktop
pnpm add -D "@doyensec/electronegativity>=1.10,<2"
pnpm test -- --run
pnpm exec tsc --noEmit
pnpm exec electronegativity -i .
cd ..
git add desktop
git commit -m "security: harden Electron renderer boundary"
```

Expected: no high-severity Electron findings remain; accepted low-severity findings require a rationale in `desktop/SECURITY.md`.

### Task 4: Settings, model readiness, and storage management

**Files:**
- Create: `desktop/src/settings.ts`
- Create: `desktop/tests/settings.test.ts`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/settings/SettingsPanel.tsx`
- Create: `frontend/src/settings/ModelStatus.tsx`
- Create: `frontend/src/settings/__tests__/SettingsPanel.test.tsx`
- Modify: `backend/src/voxagent/api/app.py`

**Interfaces:**
- Produces settings: endpoint profile, voice, speed, selected LLM, approved search roots, optional startup, log retention
- Produces: `GET /v1/models/status`, `POST /v1/models/pull`, `POST /v1/models/cancel`
- Produces: `GET /v1/storage`

- [ ] **Step 1: Test typed settings and migration**

Use a versioned JSON file under Electron `userData` for non-sensitive UI settings. Validate every field, migrate version 1 deterministically, preserve unknown future files by backing them up, and fall back to defaults after corruption while surfacing a warning.

- [ ] **Step 2: Implement model/status APIs**

Report exact model IDs, local presence, size, verification state, and currently loaded status. Pull through Ollama's localhost API or the fixed speech download service; never concatenate a shell command. Stream download progress and support cancellation.

- [ ] **Step 3: Enforce storage preflight in the UI**

Before a download, require C: free space of at least 15 GB and data-drive headroom equal to download size plus 10 GB. Default the model/data location to `D:\VoxAgentData`. Changing it requires a new empty local folder and performs an explicit copy-with-hash operation; do not silently move the live database. At runtime, low memory first reduces LLM context from 8192 to 4096, then disables optional temporary subtitles, then recommends the 1.7B model. Never switch to cloud automatically.

- [ ] **Step 4: Implement settings UI and verify**

The panel explains endpoint profiles in Chinese, with `自然（约1.35秒；话没说完可延长至2秒）` selected by default. It shows model/storage readiness before enabling Start Conversation. Test cancellation, insufficient storage, corrupt settings, and successful save.

```powershell
cd desktop
pnpm test -- --run
cd ../frontend
pnpm test -- --run
pnpm exec tsc --noEmit
pnpm build
cd ../backend
uv run pytest tests/api -v
```

- [ ] **Step 5: Commit**

```powershell
cd ..
git add backend desktop frontend
git commit -m "feat: add local model and storage settings"
```

### Task 5: Optional per-request cloud gateway

**Files:**
- Create: `backend/src/voxagent/cloud/gateway.py`
- Create: `backend/src/voxagent/cloud/credentials.py`
- Create: `backend/tests/cloud/test_gateway.py`
- Modify: `frontend/src/settings/SettingsPanel.tsx`
- Modify: `frontend/src/tools/ConfirmationCard.tsx`

**Interfaces:**
- Produces: disabled-by-default cloud provider configuration
- Produces: `CloudGateway.complete(request, approval) -> CloudResult`
- Produces approval summary containing provider, exact data categories, and estimated text size

- [ ] **Step 1: Test local-only default and approval binding**

With no provider, all turns remain local. With a provider configured, a cloud request without an unexpired approval fails closed. Bind approval to turn ID, provider ID, SHA-256 of the redacted payload, and 60-second expiry. A second request requires a second approval.

- [ ] **Step 2: Store credentials in Windows Credential Manager**

Use a user-scoped generic credential named `VoxAgent/<provider-id>`. Never store API keys in SQLite, settings JSON, exports, logs, renderer memory, or environment variables. Expose only configured/not-configured state to the renderer.

- [ ] **Step 3: Implement a single OpenAI-compatible HTTPS adapter**

Require a user-entered HTTPS base URL and pinned provider display name. Block localhost/private-network destinations to prevent confusing this path with the local model. Send only the current approved redacted request; exclude memories, knowledge chunks, file paths, tool audit, and conversation history unless each category appears in the approval summary.

- [ ] **Step 4: Add a separate cloud confirmation card**

The card says which provider receives which categories and offers `仅本次允许` or `取消`. Do not provide “always allow.” The spoken assistant may offer cloud help but cannot approve it.

- [ ] **Step 5: Verify network isolation and commit**

```powershell
cd backend
uv run pytest tests/cloud/test_gateway.py -v
uv run ruff check src tests
cd ../frontend
pnpm test -- --run
pnpm exec tsc --noEmit
cd ..
git add backend frontend
git commit -m "feat: gate optional cloud calls per request"
```

### Task 6: Windows notifications, logging, and recovery

**Files:**
- Create: `desktop/src/notifications.ts`
- Modify: `desktop/src/main.ts`
- Create: `backend/src/voxagent/logging.py`
- Create: `backend/tests/test_logging.py`
- Create: `frontend/src/diagnostics/DiagnosticsPanel.tsx`

**Interfaces:**
- Consumes: `reminder.due`
- Produces native Windows notifications
- Produces: redacted rotating logs and a user-generated diagnostic ZIP

- [ ] **Step 1: Test notification delivery rules**

Only due reminders create notifications. Clicking one focuses the existing window. Notification bodies contain title and due time but no conversation or document text. If the app was closed at due time, show pending reminders at next start and mark them delivered atomically.

- [ ] **Step 2: Implement structured redacted logging**

Use event names, durations, IDs, and status codes; exclude transcripts, memory text, document text, tokens, API keys, binary audio, and absolute paths. Rotate at 5 MB with five files and delete files older than 14 days.

- [ ] **Step 3: Implement recoverable failure states**

Surface separate Chinese errors for backend crash, Ollama unavailable, missing model, microphone denial, database lock, and speech-model load failure. Offer bounded actions: retry, open settings, reveal diagnostics folder, or restart backend. Never loop restarts indefinitely.

- [ ] **Step 4: Create privacy-safe diagnostics export**

Export application/build versions, hardware snapshot, model statuses, redacted recent logs, and database schema version. Require a preview checklist and user click. Do not include database contents, settings secrets, document lists, audio, or tokens.

- [ ] **Step 5: Verify and commit**

```powershell
cd backend
uv run pytest tests/test_logging.py -v
cd ../desktop
pnpm test -- --run
pnpm exec tsc --noEmit
cd ../frontend
pnpm test -- --run
pnpm build
cd ..
git add backend desktop frontend
git commit -m "feat: add desktop recovery and diagnostics"
```

### Task 7: Signed-off Windows installer build

**Files:**
- Create: `desktop/electron-builder.yml`
- Create: `scripts/build_desktop.ps1`
- Create: `docs/release/windows-installation.md`
- Modify: `README.md`

**Interfaces:**
- Produces: `dist/VoxAgent-Setup-x64.exe`
- Produces: unpacked test build under `dist/win-unpacked`

- [ ] **Step 1: Configure per-user NSIS packaging**

Set `requestedExecutionLevel=user`, x64 only, no automatic launch at login, no bundled model weights, and no database/data-root removal on uninstall. Include frontend dist, Electron main/preload, and packaged backend. Add application ID `ai.voxagent.desktop` and Chinese product name `声灵 VoxAgent`.

- [ ] **Step 2: Add reproducible build orchestration**

The script runs backend tests/lint, frontend tests/type-check/build, desktop tests/type-check, PyInstaller, Electron builder unpacked output, then NSIS. It stops on the first nonzero exit code and records SHA-256 for the installer.

- [ ] **Step 3: Test install, upgrade, and uninstall in a disposable Windows user profile**

Verify non-admin install, first-run data-root prompt, model readiness, one voice turn, app restart, same-version repair, upgrade over the previous build, and uninstall. Confirm `D:\VoxAgentData` survives uninstall and the user receives an explicit manual-removal instruction.

- [ ] **Step 4: Build and commit release documentation**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_desktop.ps1
Get-FileHash -Algorithm SHA256 dist/VoxAgent-Setup-x64.exe
git add desktop scripts/build_desktop.ps1 docs/release/windows-installation.md README.md
git commit -m "build: package VoxAgent Windows desktop app"
git status --short
```

## Plan 05 Completion Gate

- The installed application starts and authenticates one private localhost backend without exposing its token to the renderer.
- Electron navigation, permissions, preload, and CSP pass security tests.
- Runtime data and models default to D:, while user data survives upgrade and uninstall.
- Model download, storage checks, settings migration, restart recovery, and diagnostic export work.
- Cloud is disabled by default and every cloud request requires bound, one-time approval.
- Due reminders produce privacy-safe Windows notifications.
- A non-admin x64 installer completes smoke, upgrade, and uninstall testing.
- All backend, frontend, and desktop verification commands pass.
