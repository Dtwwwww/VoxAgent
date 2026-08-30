# Safe Local Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let VoxAgent search local files, open allowlisted apps/files, and manage local reminders through explicit typed tools without exposing arbitrary shell execution.

**Architecture:** The LLM may propose only Pydantic-validated tool calls registered in a closed registry. A policy engine assigns each call an execution level: L0 read-only, L1 visible reversible action, or L2 persistent state change. Confirmation tickets are bound to the exact turn, tool name, canonical arguments, and expiry; executors never consume raw model text.

**Tech Stack:** Python 3.12, Pydantic, SQLite, pathlib, Windows `os.startfile`, Start Menu shortcut discovery, FastAPI, React, pytest, Vitest.

## Global Constraints

- Complete Plans 01–03 first and reuse their session token, database, audit table, and turn IDs.
- Do not implement arbitrary shell, PowerShell, registry editing, software installation, file write/move/delete, browser automation, email, or messaging.
- Search roots default to the current user's Documents, Desktop, and Downloads folders and must be editable in settings.
- Filesystem traversal must not cross an unapproved root through symlinks, junctions, UNC paths, or `..` segments.
- L0 tools execute without confirmation and cannot change external state.
- L1 tools require one visible confirmation unless the exact app has a user-created standing allow rule.
- L2 reminder create/update/delete always requires confirmation; standing allow rules do not bypass L2.
- Confirmation tickets expire after 60 seconds and after any new user turn.
- Every proposal, denial, confirmation, execution, failure, and cancellation writes a redacted audit event.
- The assistant reports success only from an executor result, never from its own prediction.

---

## Planned File Structure

```text
backend/src/voxagent/
  tools/models.py
  tools/registry.py
  tools/policy.py
  tools/confirmation.py
  tools/path_policy.py
  tools/search_files.py
  tools/open_target.py
  tools/reminders.py
  tools/service.py
  api/tools.py
backend/tests/tools/
  test_registry.py
  test_policy.py
  test_confirmation.py
  test_path_policy.py
  test_search_files.py
  test_open_target.py
  test_reminders.py
  test_service.py
backend/tests/api/test_tools_api.py
frontend/src/tools/ConfirmationCard.tsx
frontend/src/tools/ToolHistory.tsx
frontend/src/tools/__tests__/ConfirmationCard.test.tsx
```

### Task 1: Closed tool registry and typed call envelope

**Files:**
- Create: `backend/src/voxagent/tools/models.py`
- Create: `backend/src/voxagent/tools/registry.py`
- Create: `backend/tests/tools/test_registry.py`

**Interfaces:**
- Produces: `ToolCall(call_id, turn_id, name, arguments)`
- Produces: `ToolResult(call_id, status, spoken_summary, detail)`
- Produces: `ToolRegistry.register(spec, executor)` and `execute(call)`
- Registers exactly: `search_files`, `open_app`, `open_file`, `create_reminder`, `list_reminders`, `update_reminder`, `delete_reminder`

- [ ] **Step 1: Write registry rejection tests**

Prove unknown names, duplicate registrations, extra argument fields, invalid UUIDs, oversized strings, and executor type mismatches are rejected before execution. Prove arguments are parsed into the registered Pydantic model and no raw dictionary reaches an executor.

- [ ] **Step 2: Define strict argument schemas**

Use `ConfigDict(extra="forbid", str_strip_whitespace=True)`. Query/title fields are 1–200 characters; path fields are 1–1,024; search limit is 1–50; reminder IDs are UUIDs; datetimes require timezone offsets. Tool status is `proposed`, `awaiting_confirmation`, `running`, `succeeded`, `failed`, `denied`, or `cancelled`.

- [ ] **Step 3: Implement the closed registry**

Reject registration after `registry.freeze()`. Provide `llm_schema()` that returns only tool names, descriptions, and JSON schemas; do not include Python module names or filesystem policy details.

- [ ] **Step 4: Verify and commit**

```powershell
cd backend
uv run pytest tests/tools/test_registry.py -v
uv run ruff check src tests
cd ..
git add backend/src/voxagent/tools backend/tests/tools/test_registry.py
git commit -m "feat: define closed typed tool registry"
```

### Task 2: Canonical path policy

**Files:**
- Create: `backend/src/voxagent/tools/path_policy.py`
- Create: `backend/tests/tools/test_path_policy.py`

**Interfaces:**
- Produces: `PathPolicy(allowed_roots).resolve_existing(candidate) -> Path`
- Produces: `PathPolicy.relative_display(path) -> str`
- Produces: `PathDenied(reason_code, safe_message)`

- [ ] **Step 1: Write Windows path-escape tests**

Use temporary folders and Windows-only marked tests to cover normal descendants, sibling-prefix confusion, `..`, symlinks, directory junctions, UNC paths, device paths, alternate data streams, nonexistent targets, and case-insensitive drive letters. A denied error must not echo a secret path into spoken text.

- [ ] **Step 2: Implement canonical resolution**

Resolve the target and each configured root, reject non-local paths and any path component with an alternate data stream, then use `Path.is_relative_to(root)` on resolved paths. For search traversal, inspect reparse-point attributes before descending and skip reparse directories even when their final target is allowed.

- [ ] **Step 3: Add default-root discovery**

Read `FOLDERID_Documents`, `FOLDERID_Desktop`, and `FOLDERID_Downloads` through the Windows Known Folder API; do not construct English folder names. Persist user-approved roots as resolved absolute paths in SQLite settings.

- [ ] **Step 4: Verify and commit**

```powershell
cd backend
uv run pytest tests/tools/test_path_policy.py -v
uv run ruff check src tests
cd ..
git add backend
git commit -m "feat: enforce local path boundaries"
```

### Task 3: L0 file search and reminder listing

**Files:**
- Create: `backend/src/voxagent/tools/search_files.py`
- Create: `backend/tests/tools/test_search_files.py`
- Create: `backend/src/voxagent/tools/reminders.py`
- Create: `backend/tests/tools/test_reminders.py`

**Interfaces:**
- Produces: `search_files(args, path_policy) -> SearchFilesResult`
- Produces: `ReminderRepository.list(status, limit) -> tuple[Reminder, ...]`

- [ ] **Step 1: Test bounded search**

Require filename-only matching by default, optional content matching only for UTF-8/TXT/MD files below 1 MB, a maximum traversal of 20,000 entries, a two-second deadline, deterministic modified-time/name ordering, and at most 50 results. Skip hidden/system/reparse entries and inaccessible paths without failing the whole call.

- [ ] **Step 2: Implement cooperative cancellation**

Accept a `threading.Event` and check it before every directory and file batch. Return `truncated=True` plus a safe reason when a count or time limit is reached. Results expose display name, approved-root-relative path, size, and modified time; absolute paths remain backend-only.

- [ ] **Step 3: Add reminder schema and read-only listing**

Add migration 002 table `reminders(id, title, due_at_utc, timezone, status, created_at_utc, updated_at_utc)`. Status is `pending`, `completed`, or `cancelled`. Listing filters by status and returns no more than 100 rows.

- [ ] **Step 4: Verify and commit**

```powershell
cd backend
uv run pytest tests/tools/test_search_files.py tests/tools/test_reminders.py -v
uv run ruff check src tests
cd ..
git add backend
git commit -m "feat: add read-only local search tools"
```

### Task 4: L1 app/file opening with turn-bound confirmation

**Files:**
- Create: `backend/src/voxagent/tools/confirmation.py`
- Create: `backend/src/voxagent/tools/open_target.py`
- Create: `backend/tests/tools/test_confirmation.py`
- Create: `backend/tests/tools/test_open_target.py`

**Interfaces:**
- Produces: `ConfirmationService.issue(call, level, summary) -> ConfirmationTicket`
- Produces: `ConfirmationService.consume(ticket_id, call) -> None`
- Produces: `AppCatalog.refresh() -> tuple[InstalledApp, ...]`
- Produces: `open_app(args, catalog)` and `open_file(args, path_policy)`

- [ ] **Step 1: Test confirmation binding and replay resistance**

Canonicalize arguments as sorted compact JSON and hash `turn_id + tool_name + arguments + expires_at` with SHA-256. Test modified arguments, wrong turn, expiry, second consumption, and a newly started turn all invalidate the ticket. Never persist a valid ticket secret after consumption.

- [ ] **Step 2: Build a safe app catalog**

Index Start Menu `.lnk` entries and an explicit built-in allowlist for Notepad, Calculator, and File Explorer. Store stable app IDs and display names. Do not accept executable paths from the LLM. Resolve duplicate spoken names by returning choices, not by guessing.

- [ ] **Step 3: Implement visible open actions**

`open_app` accepts only a catalog app ID. `open_file` accepts a prior `search_files` result ID stored for the active turn; it cannot accept a new absolute path. Use `os.startfile` only after policy and confirmation checks. Return `succeeded` once Windows accepts the launch; phrase the summary as “已请求 Windows 打开…”, not proof that the UI fully loaded.

- [ ] **Step 4: Add optional standing app rules**

A user may select “以后允许打开此应用” after a successful L1 confirmation. Bind the rule to the stable app ID, show it in settings, and support deletion. Never create standing rules for files or L2 tools.

- [ ] **Step 5: Verify and commit**

```powershell
cd backend
uv run pytest tests/tools/test_confirmation.py tests/tools/test_open_target.py -v
uv run ruff check src tests
cd ..
git add backend
git commit -m "feat: add confirmed app and file opening"
```

### Task 5: L2 reminder mutation and due-event delivery

**Files:**
- Modify: `backend/src/voxagent/tools/reminders.py`
- Modify: `backend/tests/tools/test_reminders.py`
- Create: `backend/src/voxagent/tools/service.py`
- Create: `backend/tests/tools/test_service.py`

**Interfaces:**
- Produces: create/update/delete reminder executors
- Produces: `ReminderScheduler.next_due(now_utc) -> tuple[Reminder, ...]`
- Produces: server event `reminder.due`

- [ ] **Step 1: Test timezone and mutation rules**

The user timezone defaults to `Asia/Shanghai`. Reject nonexistent local times, resolve ambiguous times only after confirmation text names the chosen offset, reject dates more than five years ahead, and require non-empty titles. Every mutation requires a fresh L2 ticket.

- [ ] **Step 2: Implement idempotent mutations**

Create with caller-provided UUID; a duplicate identical request returns the existing reminder. Updates use `updated_at_utc` optimistic concurrency. Delete is a status transition to `cancelled`, preserving audit history.

- [ ] **Step 3: Implement local due-event polling**

Poll SQLite every 15 seconds only while the app runs. Mark a reminder delivered in the same transaction that creates its audit event. Emit `reminder.due` to the active WebSocket; when no session is active, Plan 05 turns it into a Windows notification at next app start or due time.

- [ ] **Step 4: Verify and commit**

```powershell
cd backend
uv run pytest tests/tools/test_reminders.py tests/tools/test_service.py -v
uv run ruff check src tests
cd ..
git add backend
git commit -m "feat: add confirmed local reminders"
```

### Task 6: Tool proposal integration and secure API

**Files:**
- Modify: `backend/src/voxagent/conversation/orchestrator.py`
- Create: `backend/src/voxagent/api/tools.py`
- Create: `backend/tests/api/test_tools_api.py`

**Interfaces:**
- Extends: assistant events `tool.proposed`, `tool.running`, `tool.result`
- Produces: `POST /v1/tools/{call_id}/confirm`
- Produces: `POST /v1/tools/{call_id}/deny`
- Produces: `GET /v1/tools/audit`

- [ ] **Step 1: Test model-output isolation**

Provide adversarial LLM text that contains shell commands, invented tool names, extra fields, paths outside roots, and claims of success. Assert only a valid registry call becomes a proposal, invalid content becomes a normal safe reply, and no executor runs before policy approval.

- [ ] **Step 2: Add a two-pass tool loop**

First ask Ollama for either spoken content or one registered call using its native tools format. Validate and policy-check the call. L0 executes directly; L1/L2 emits a confirmation card and pauses that tool call. After a real result, send the structured result back to the model once to produce the spoken summary. Limit to one tool call per user turn for version one.

- [ ] **Step 3: Implement authenticated confirmation routes**

Use the Plan 02 bearer session token, check active turn ownership, consume the confirmation ticket atomically, and return 409 for expired/stale calls. The audit route paginates redacted summaries and never returns argument hashes, absolute paths, or secret-filter matches.

- [ ] **Step 4: Verify and commit**

```powershell
cd backend
uv run pytest tests/tools tests/api/test_tools_api.py tests/conversation/test_orchestrator.py -v
uv run ruff check src tests
cd ..
git add backend
git commit -m "feat: connect safe tools to voice turns"
```

### Task 7: Web confirmation and history interface

**Files:**
- Create: `frontend/src/tools/ConfirmationCard.tsx`
- Create: `frontend/src/tools/ToolHistory.tsx`
- Create: `frontend/src/tools/__tests__/ConfirmationCard.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/protocol.ts`

- [ ] **Step 1: Test explicit user decisions**

Require the card to show action level, plain-language action, target display name, expiry countdown, Confirm, and Deny. Confirm disables both buttons immediately. L2 cards never show a standing-permission checkbox. A new `vad.started` event marks the old card expired.

- [ ] **Step 2: Implement spoken and visual confirmation together**

The assistant speaks a short proposal but never interprets ambient “yes” as approval in version one; only the Web Confirm button authorizes the call. Keep keyboard focus on the card and support Enter only while the Confirm button is focused.

- [ ] **Step 3: Add redacted tool history**

Show timestamp, tool display name, outcome, and safe summary. Provide filters by outcome and date. Do not show canonical arguments or absolute local paths.

- [ ] **Step 4: Verify all client gates**

```powershell
cd frontend
pnpm test -- --run
pnpm exec tsc --noEmit
pnpm build
```

- [ ] **Step 5: Commit**

```powershell
cd ..
git add frontend
git commit -m "feat: add explicit tool confirmation UI"
git status --short
```

## Plan 04 Completion Gate

- Only seven registered tools can be proposed; arbitrary commands and raw executable paths are impossible through the registry.
- Search is read-only, bounded, cancellable, and confined to approved Windows Known Folders.
- App/file opening requires a valid L1 confirmation unless the exact app has a standing rule.
- Reminder mutation always requires a fresh L2 confirmation.
- Tickets cannot be replayed, modified, reused across turns, or consumed after 60 seconds.
- Every tool outcome is auditable without exposing sensitive arguments.
- The assistant never reports success without a real executor result.
- Backend and frontend verification commands pass.
