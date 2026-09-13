from collections.abc import Callable, Mapping, Sequence

from voxagent.tools.schema import ToolCall

_APP_NAMES = {
    "计算器": "calculator",
    "calculator": "calculator",
    "记事本": "notepad",
    "notepad": "notepad",
}
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
    return ToolCall(
        call_id=call_id_factory(),
        name="apps.open_allowlisted",
        arguments={"app_id": app_ids.pop()},
    )
