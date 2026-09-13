# Reliable Allowlisted App Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every explicit calculator/notepad request enter a fresh confirmed tool call, activate an existing window when present, report only evidenced outcomes, and hide successful frontend status after 3 seconds.

**Architecture:** Add a narrow deterministic router before model invocation for explicit allowlisted app actions. Keep all routed calls inside the existing schema, policy, confirmation, repository, and LangGraph execution path; replace the blind Windows launcher with a state-aware activation adapter; expose only structured launcher outcomes. In the frontend, correlate visible activity by session, turn, and call ID instead of rendering the global last event.

**Tech Stack:** Python 3.12, Pydantic, LangGraph, SQLite, `ctypes` Win32 APIs, pytest, React 19, TypeScript 7, Vitest.

## Global Constraints

- Only `calculator` and `notepad` are eligible for deterministic routing and activation.
- Every explicit request remains an L1 action requiring a fresh one-shot confirmation.
- Do not add shell, PowerShell, arbitrary executable, PID, HWND, command-line, or general window-control inputs.
- Do not simulate Alt, inject keyboard input, or bypass Windows foreground restrictions.
- Use fake models and fake Windows adapters in automated tests; use the live Windows desktop only for the final smoke test.
- A current `session_id + turn_id + call_id` terminal event is mandatory before any success copy is emitted.
- Successful activity is visible for exactly 3 seconds; failure and attention-required activity remains until dismissal or a new turn.
- Keep the regression suite focused on the three observed failures; do not add a broad Agent evaluation dataset in this fix.

## File Structure

- Create `backend/src/voxagent/agent/app_actions.py`: recognize only explicit allowlisted application actions and construct a fixed tool call.
- Create `backend/tests/agent/test_app_actions.py`: focused intent-routing examples and false-positive guards.
- Modify `backend/src/voxagent/agent/graph.py`: route detected actions into authorization and end app-action turns with trusted tool results.
- Modify `backend/src/voxagent/agent/events.py`: carry the validated optional application outcome on completion.
- Modify `backend/src/voxagent/agent/service.py`: inject a deterministic call-ID factory into the workflow.
- Modify `backend/src/voxagent/agent/state.py`: carry the deterministic-action marker used to enforce the trusted terminal path.
- Modify `backend/tests/agent/test_agent_service.py`: prove model bypass, fresh confirmations, evidence-backed completion, and no execution after denial.
- Modify `backend/src/voxagent/conversation/events.py`: expose the optional structured application outcome in `tool.completed`.
- Modify `backend/src/voxagent/conversation/orchestrator.py`: forward the outcome from the correlated Agent event.
- Create `backend/src/voxagent/tools/windows_app_activation.py`: isolate Win32 enumeration, process identity, restore, foreground, and flash behavior.
- Modify `backend/src/voxagent/tools/app_launcher.py`: return `launched`, `focused`, or `attention_requested` from a frozen allowlist.
- Modify `backend/tests/tools/test_app_launcher.py`: exercise launcher state transitions with fakes.
- Modify `backend/tests/tools/test_builtin_tools.py`: assert tool results contain only the safe structured outcome and fixed summary.
- Modify `frontend/src/useVoiceSession.ts`: correlate active tool activity and own the 3-second expiration timer.
- Modify `frontend/src/protocol.ts`: parse the optional allowlisted application outcome without inferring it from localized text.
- Modify `frontend/src/App.tsx`: render only `activeToolActivity`, never `recentToolActivity.at(-1)`.
- Modify `frontend/src/components/ToolApprovalCard.tsx`: render outcome-specific status copy.
- Modify `frontend/src/__tests__/useVoiceSession.test.ts`: verify correlation, new-turn clearing, and timer isolation.
- Modify `frontend/src/__tests__/App.test.tsx`: verify stale historical activity is not rendered.

---

### Task 1: Deterministic allowlisted app-action routing

**Files:**
- Create: `backend/src/voxagent/agent/app_actions.py`
- Create: `backend/tests/agent/test_app_actions.py`
- Modify: `backend/src/voxagent/agent/graph.py:63-78,358-376`
- Modify: `backend/src/voxagent/agent/service.py:25-60`
- Modify: `backend/src/voxagent/agent/state.py:6-20`

**Interfaces:**
- Consumes: checkpoint message dictionaries shaped as `{"role": "user|assistant", "content": str}`.
- Produces: `detect_allowlisted_app_action(messages, call_id_factory) -> ToolCall | None` and `AgentState.deterministic_app_action: bool`.

- [ ] **Step 1: Write the failing router tests**

```python
from voxagent.agent.app_actions import detect_allowlisted_app_action


def detect(text: str, *history: dict[str, str]):
    return detect_allowlisted_app_action(
        [*history, {"role": "user", "content": text}],
        call_id_factory=lambda: "app-call-1",
    )


def test_routes_each_explicit_allowlisted_app_action() -> None:
    assert detect("打开记事本").arguments == {"app_id": "notepad"}
    assert detect("再帮我打开计算器").arguments == {"app_id": "calculator"}


def test_resolves_it_only_from_the_previous_user_app_reference() -> None:
    call = detect("再打开它", {"role": "user", "content": "打开计算器"})
    assert call is not None and call.arguments == {"app_id": "calculator"}


def test_rejects_negation_questions_and_calculation_requests() -> None:
    assert detect("不要打开计算器") is None
    assert detect("计算器有什么功能") is None
    assert detect("用计算器算一下 2+2") is None
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_app_actions.py -q`

Expected: FAIL because `voxagent.agent.app_actions` does not exist.

- [ ] **Step 3: Implement the narrow recognizer**

```python
from collections.abc import Callable, Mapping, Sequence

from voxagent.tools.schema import ToolCall

_APP_NAMES = {"计算器": "calculator", "calculator": "calculator", "记事本": "notepad", "notepad": "notepad"}
_ACTIONS = ("打开", "启动", "切换到", "调出", "回到")
_NEGATIONS = ("不要", "别", "不用")
_NON_ACTIONS = ("怎么算", "有什么功能", "已经打开")


def detect_allowlisted_app_action(
    messages: Sequence[Mapping[str, object]],
    call_id_factory: Callable[[], str],
) -> ToolCall | None:
    users = [str(item.get("content", "")) for item in messages if item.get("role") == "user"]
    if not users:
        return None
    current = users[-1].strip().lower()
    if any(word in current for word in (*_NEGATIONS, *_NON_ACTIONS)):
        return None
    app_ids = {app_id for name, app_id in _APP_NAMES.items() if name in current}
    if not app_ids and "它" in current and len(users) > 1:
        app_ids = {app_id for name, app_id in _APP_NAMES.items() if name in users[-2].lower()}
    if len(app_ids) != 1 or not any(action in current for action in _ACTIONS):
        return None
    return ToolCall(call_id=call_id_factory(), name="apps.open_allowlisted", arguments={"app_id": app_ids.pop()})
```

Inject `call_id_factory=lambda: f"app-{uuid4()}"` through `AgentService` into `AgentWorkflow`. In `route`, run the recognizer before `retrieve` and return `pending_calls=[call.model_dump(mode="json")]`, `deterministic_app_action=True`, and `next_action="authorize"`. Add the `route -> authorize` graph edge. Do not call the model for a detected action.

- [ ] **Step 4: Run focused router tests and existing graph tests**

Run: `backend\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_app_actions.py backend/tests/agent/test_agent_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/src/voxagent/agent/app_actions.py backend/src/voxagent/agent/graph.py backend/src/voxagent/agent/service.py backend/src/voxagent/agent/state.py backend/tests/agent/test_app_actions.py backend/tests/agent/test_agent_service.py
git commit -m "feat: route explicit app actions deterministically"
```

### Task 2: State-aware Windows application activation

**Files:**
- Create: `backend/src/voxagent/tools/windows_app_activation.py`
- Create: `backend/tests/tools/test_app_launcher.py`
- Modify: `backend/src/voxagent/tools/app_launcher.py:1-50`
- Modify: `backend/tests/tools/test_builtin_tools.py:36-41,225-248`

**Interfaces:**
- Produces: `AppActivationResult(outcome: Literal["launched", "focused", "attention_requested"])`.
- Produces: `AppWindowBackend.find_window(process_name) -> int | None`, `process_exists(process_name) -> bool`, `restore(hwnd) -> None`, `focus(hwnd) -> bool`, and `flash(hwnd) -> None`.
- Produces: `Launcher.open_app(app_id: str) -> AppActivationResult`.

- [ ] **Step 1: Write failing state-transition tests with a fake backend**

```python
def test_launches_once_when_target_process_is_absent() -> None:
    backend = FakeWindowBackend(process=False, hwnd=None)
    started: list[list[str]] = []
    launcher = WindowsAllowlistedLauncher(backend=backend, start=lambda argv: started.append(argv))
    assert launcher.open_app("notepad").outcome == "launched"
    assert started == [["notepad.exe"]]


def test_restores_and_focuses_existing_window_without_starting() -> None:
    backend = FakeWindowBackend(process=True, hwnd=42, minimized=True, focus_result=True)
    launcher = WindowsAllowlistedLauncher(backend=backend, start=lambda argv: pytest.fail(str(argv)))
    assert launcher.open_app("calculator").outcome == "focused"
    assert backend.calls == [("restore", 42), ("focus", 42)]


def test_flashes_when_windows_denies_foreground_focus() -> None:
    backend = FakeWindowBackend(process=True, hwnd=42, focus_result=False)
    launcher = WindowsAllowlistedLauncher(backend=backend, start=lambda argv: pytest.fail(str(argv)))
    assert launcher.open_app("calculator").outcome == "attention_requested"
    assert backend.calls[-1] == ("flash", 42)
```

Also add one bounded-wait test: when a target process exists but its window never appears, assert `AppActivationError("app_window_unavailable")` and zero process starts.

- [ ] **Step 2: Run the launcher tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend/tests/tools/test_app_launcher.py -q`

Expected: FAIL because the result and injected backend interfaces do not exist.

- [ ] **Step 3: Implement the launcher contract and Win32 adapter**

```python
@dataclass(frozen=True, slots=True)
class AppActivationResult:
    outcome: Literal["launched", "focused", "attention_requested"]


class WindowsAllowlistedLauncher:
    _SPECS = {
        "notepad": ("notepad.exe", "Notepad.exe"),
        "calculator": ("calc.exe", "CalculatorApp.exe"),
    }

    def open_app(self, app_id: str) -> AppActivationResult:
        command, process_name = self._SPECS[app_id]
        hwnd = self._backend.find_window(process_name)
        if hwnd is not None:
            if self._backend.is_minimized(hwnd):
                self._backend.restore(hwnd)
            if self._backend.focus(hwnd):
                return AppActivationResult("focused")
            self._backend.flash(hwnd)
            return AppActivationResult("attention_requested")
        if self._backend.process_exists(process_name):
            hwnd = self._wait_for_window(process_name)
            if hwnd is None:
                raise AppActivationError("app_window_unavailable")
            return self._activate(hwnd)
        self._start([command])
        return AppActivationResult("launched")
```

Implement `Win32WindowBackend` with `EnumWindows`, `EnumChildWindows`, `GetWindowThreadProcessId`, `OpenProcess`, `QueryFullProcessImageNameW`, `IsWindowVisible`, `IsIconic`, `ShowWindow(SW_RESTORE)`, `SetForegroundWindow`, and `FlashWindowEx`. Match case-insensitive executable basenames from either the top-level window PID or a descendant PID; never inspect the localized window title. Use an injected monotonic clock and wait function for an upper bound of 1 second with 50 ms polling.

- [ ] **Step 4: Return safe outcome summaries from the executor**

```python
_APP_LABELS = {"notepad": "记事本", "calculator": "计算器"}
_OUTCOME_COPY = {
    "launched": "已启动{label}。",
    "focused": "已切换到{label}。",
    "attention_requested": "{label}已在任务栏提示，请点击切换。",
}

result = launcher.open_app(app_id)
return ToolResult(
    call_id=call.call_id,
    tool_name=call.name,
    status="succeeded",
    data={"app_id": app_id, "outcome": result.outcome},
    user_summary=_OUTCOME_COPY[result.outcome].format(label=_APP_LABELS[app_id]),
    duration_ms=_duration_ms(started),
)
```

Convert `AppActivationError.code` to a failed `ToolResult` with only `app_launch_failed`, `app_window_unavailable`, or `app_activation_failed`; do not expose native exception text.

- [ ] **Step 5: Run focused launcher and built-in tool tests**

Run: `backend\.venv\Scripts\python.exe -m pytest backend/tests/tools/test_app_launcher.py backend/tests/tools/test_builtin_tools.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/src/voxagent/tools/windows_app_activation.py backend/src/voxagent/tools/app_launcher.py backend/tests/tools/test_app_launcher.py backend/tests/tools/test_builtin_tools.py
git commit -m "fix: activate existing allowlisted app windows"
```

### Task 3: Evidence-backed Agent completion

**Files:**
- Modify: `backend/src/voxagent/agent/graph.py:251-341`
- Modify: `backend/src/voxagent/agent/events.py:27-32`
- Modify: `backend/src/voxagent/conversation/events.py:235-240`
- Modify: `backend/src/voxagent/conversation/orchestrator.py:918-927`
- Modify: `backend/tests/agent/test_agent_service.py`
- Modify: `backend/tests/conversation/test_orchestrator.py`

**Interfaces:**
- Consumes: `ToolResult.data.outcome` from Task 2 and `AgentState.deterministic_app_action` from Task 1.
- Produces: app-action turns that emit one correlated `ToolCompleted(outcome=...)` or `ToolFailed`, then `TurnDone`, with no post-tool model call.

- [ ] **Step 1: Write failing service regressions**

Add tests proving all of these in one two-turn scenario:

```python
first = await collect(service.start_turn("session-a", 1, [ChatMessage("user", "打开计算器")]))
second = await collect(service.start_turn("session-a", 2, [ChatMessage("user", "再打开计算器")]))
assert isinstance(first[0], ToolApprovalRequired)
assert isinstance(second[0], ToolApprovalRequired)
assert first[0].confirmation_id != second[0].confirmation_id
assert first[0].call_id != second[0].call_id
assert model.calls == 0
```

After approving each ticket, assert the fake launcher ran twice, both requests are independently succeeded, the completion summary matches its launcher outcome, and the model call count remains zero. Add a denial case asserting launcher calls remain zero and no success `AgentTextDelta` is emitted.

- [ ] **Step 2: Run the service regressions and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_agent_service.py backend/tests/conversation/test_orchestrator.py -q`

Expected: FAIL because successful tool execution currently returns to `call_model`.

- [ ] **Step 3: End deterministic app actions from their real terminal event**

```python
is_deterministic_app = (
    state.get("deterministic_app_action", False)
    and call.name == "apps.open_allowlisted"
)
next_action = "respond" if is_deterministic_app else (
    "authorize" if state.get("pending_calls") else "call_model"
)
```

When persisting a successful request, copy only the validated outcome to audit detail:

```python
if call.name == "apps.open_allowlisted" and result.status == "succeeded":
    outcome = result.data.get("outcome")
    if outcome in {"launched", "focused", "attention_requested"}:
        detail["outcome"] = outcome
```

Rely on the orchestrator's existing `last_tool_summary` fallback only after the matching Agent event has been consumed. Do not accept model text or old history as the action result.

Add an optional structured outcome to the internal and WebSocket completion events:

```python
AppOutcome = Literal["launched", "focused", "attention_requested"]


@dataclass(frozen=True, slots=True)
class ToolCompleted:
    call_id: str
    tool_name: str
    user_summary: str
    outcome: AppOutcome | None = None
```

Populate it only when `call.name == "apps.open_allowlisted"` and the executor returned one of the three allowed values. Forward it through `conversation.events.ToolCompleted`; all other tools omit it. This makes the frontend depend on typed execution evidence, not Chinese text matching.

- [ ] **Step 4: Run the Agent and orchestrator regressions**

Run: `backend\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_agent_service.py backend/tests/conversation/test_orchestrator.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/src/voxagent/agent/events.py backend/src/voxagent/agent/graph.py backend/src/voxagent/conversation/events.py backend/src/voxagent/conversation/orchestrator.py backend/tests/agent/test_agent_service.py backend/tests/conversation/test_orchestrator.py
git commit -m "fix: require tool evidence for app action replies"
```

### Task 4: Correlated, expiring frontend tool status

**Files:**
- Modify: `frontend/src/useVoiceSession.ts:53-69,75-90,476-663,1058-1081`
- Modify: `frontend/src/protocol.ts:53-89,296-305`
- Modify: `frontend/src/App.tsx:88-95`
- Modify: `frontend/src/components/ToolApprovalCard.tsx:46-54`
- Modify: `frontend/src/__tests__/useVoiceSession.test.ts:577-636`
- Modify: `frontend/src/__tests__/App.test.tsx:109-139`

**Interfaces:**
- Produces: `ToolActivity.sessionId: string`, `ToolActivity.status: "running" | "completed" | "attention_requested" | "failed"`, `VoiceSessionController.activeToolActivity: ToolActivity | null`, and `dismissToolActivity(): void`.
- Consumes: server `session_id`, `turn_id`, `call_id`, trusted `user_summary`, and optional structured `outcome` fields.

- [ ] **Step 1: Write failing hook tests with fake timers**

```typescript
it("hides a successful tool status after three seconds without removing a newer call", () => {
  vi.useFakeTimers();
  const hook = connectedHook();
  serverMessage({ type: "tool.completed", session_id: SESSION_ID, turn_id: 4,
    call_id: "old", tool_name: "apps.open_allowlisted", user_summary: "已启动计算器。" });
  expect(hook.result.current.activeToolActivity?.callId).toBe("old");

  act(() => { vi.advanceTimersByTime(2000); });
  serverMessage({ type: "tool.started", session_id: SESSION_ID, turn_id: 5,
    call_id: "new", tool_name: "apps.open_allowlisted" });
  act(() => { vi.advanceTimersByTime(1000); });
  expect(hook.result.current.activeToolActivity?.callId).toBe("new");
  vi.useRealTimers();
});
```

Add separate assertions that a completion is gone at 3000 ms, failure remains past 3000 ms, `submitText` hides prior activity immediately, and an event from a stale session cannot replace the current activity.

Add a component assertion that `failed` and `attention_requested` expose a `关闭工具状态` button, the button calls `dismissToolActivity`, and ordinary success has no close button because it expires automatically.

- [ ] **Step 2: Run the hook test and verify RED**

Run:

```powershell
Push-Location frontend
node_modules\.bin\vitest.cmd run src/__tests__/useVoiceSession.test.ts
Pop-Location
```

Expected: FAIL because `activeToolActivity` and expiry ownership do not exist.

- [ ] **Step 3: Implement the correlated activity lifecycle**

```typescript
export interface ToolActivity {
  sessionId: string;
  turnId: number;
  callId: string;
  toolName: string;
  status: "running" | "completed" | "attention_requested" | "failed";
  summary?: string;
  errorCode?: string;
}

type AppOutcome = "launched" | "focused" | "attention_requested";

// `tool.completed` carries `outcome?: AppOutcome`; the parser validates the
// three literals before returning the event.

const [activeToolActivity, setActiveToolActivity] = useState<ToolActivity | null>(null);

useEffect(() => {
  if (activeToolActivity?.status !== "completed") return;
  const key = `${activeToolActivity.sessionId}:${activeToolActivity.turnId}:${activeToolActivity.callId}`;
  const timeout = window.setTimeout(() => {
    setActiveToolActivity((current) => current
      && `${current.sessionId}:${current.turnId}:${current.callId}` === key ? null : current);
  }, 3000);
  return () => window.clearTimeout(timeout);
}, [activeToolActivity]);

const dismissToolActivity = useCallback(() => setActiveToolActivity(null), []);
```

On every tool event, first require `event.session_id === sessionIdRef.current`; then replace only the record with the same session/turn/call or make the incoming call active. On `submitText`, `asr.final`, `vad.started`, session reset, and local-data reset, set `activeToolActivity` to null. Preserve `recentToolActivity` only as bounded internal history if existing tests or diagnostics need it; the main UI must not read it.

Map `event.outcome === "attention_requested"` to the matching activity status; `launched` and `focused` remain successful `completed` activity with the trusted summary. Reject any unknown outcome in `parseServerEventJson`. Never infer an outcome from localized summary or model text.

- [ ] **Step 4: Render only the active record and outcome-specific copy**

```tsx
{!controller.pendingToolApproval && (
  <ToolActivityStatus
    activity={controller.activeToolActivity ?? undefined}
    onDismiss={controller.dismissToolActivity}
  />
)}
```

Use `activity.summary` for completed/attention copy, `正在执行` for running, and `已拒绝或失败` for failed. Render the close button only for failure and attention-required status. Do not show the generic permanent `已完成` label after the activity expires.

```tsx
const dismissible = activity.status === "failed" || activity.status === "attention_requested";
return <div className="tool-activity" role="status">
  <span>{toolLabel(activity.toolName)}</span>
  <strong>{activity.summary ?? status}</strong>
  {dismissible && <button type="button" onClick={onDismiss} aria-label="关闭工具状态">关闭</button>}
</div>;
```

- [ ] **Step 5: Run focused frontend tests**

Run:

```powershell
Push-Location frontend
node_modules\.bin\vitest.cmd run src/__tests__/useVoiceSession.test.ts src/__tests__/App.test.tsx
Pop-Location
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/protocol.ts frontend/src/useVoiceSession.ts frontend/src/App.tsx frontend/src/components/ToolApprovalCard.tsx frontend/src/__tests__/useVoiceSession.test.ts frontend/src/__tests__/App.test.tsx
git commit -m "fix: expire correlated tool completion status"
```

### Task 5: Focused integration and Windows smoke verification

**Files:**
- Modify only if verification exposes a defect in the files already listed above.

**Interfaces:**
- Consumes: completed behavior from Tasks 1-4.
- Produces: verified calculator/notepad launch and repeat-activation behavior with clean automated gates.

- [ ] **Step 1: Run the focused backend suite**

Run: `backend\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_app_actions.py backend/tests/agent/test_agent_service.py backend/tests/conversation/test_orchestrator.py backend/tests/tools/test_app_launcher.py backend/tests/tools/test_builtin_tools.py -q`

Expected: PASS with zero failures.

- [ ] **Step 2: Run backend lint**

Run: `backend\.venv\Scripts\ruff.exe check backend/src backend/tests`

Expected: `All checks passed!`

- [ ] **Step 3: Run focused frontend tests, typecheck, and build**

```powershell
Push-Location frontend
node_modules\.bin\vitest.cmd run src/__tests__/useVoiceSession.test.ts src/__tests__/App.test.tsx
node_modules\.bin\tsc.cmd --noEmit
node_modules\.bin\vite.cmd build
Pop-Location
```

Expected: tests PASS and both build commands exit 0.

- [ ] **Step 4: Run the repository verification gate**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1 -Scope All`

Expected: final line `Verification passed.`

- [ ] **Step 5: Restart the backend and frontend using the README commands**

Confirm ports `8765`, `5173`, and `11434` are listening and `GET http://127.0.0.1:8765/health` reports ready before opening the frontend.

- [ ] **Step 6: Perform the Windows behavior smoke test**

1. Submit `打开记事本`, approve once, and verify a Notepad window exists.
2. Submit `打开计算器`, approve once, and verify a Calculator window exists.
3. Return to the browser, submit `再帮我打开计算器`, and approve the fresh confirmation.
4. Verify no second Calculator window appears and the existing window is focused; if Windows denies focus, verify the taskbar flashes and the UI says it needs a click.
5. Verify each success status disappears after 3 seconds.
6. Verify the audit endpoint contains separate request/call/confirmation records for both Calculator turns.
