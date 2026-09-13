import re
from collections.abc import Callable, Mapping, Sequence

from voxagent.tools.schema import ToolCall

_APP_NAMES = {
    "计算器": "calculator",
    "calculator": "calculator",
    "记事本": "notepad",
    "notepad": "notepad",
}
_ACTIONS = ("打开", "启动", "切换到", "切回", "切换一下", "调出", "回到", "唤起")
_NEGATIONS = ("不想", "不许", "不要", "别", "不用")
_NON_ACTIONS = ("怎么算", "有什么功能", "已经打开")
_ACTION_PATTERN = "|".join(map(re.escape, _ACTIONS))
_APP_PATTERN = "|".join(map(re.escape, _APP_NAMES))
_PREFIX_PATTERN = r"(?:(?:请|麻烦)(?:你)?\s*)?(?:再\s*)?(?:(?:帮我|给我)\s*)?"
_NEGATION_RE = re.compile(rf"(?:{'|'.join(map(re.escape, _NEGATIONS))})\s*(?:{_ACTION_PATTERN})")
_QUESTION_RE = re.compile(
    rf"(?:怎么|如何)(?:才能|可以)?\s*(?:{_ACTION_PATTERN})|"
    rf"(?:{_APP_PATTERN})\s*(?:{_ACTION_PATTERN})(?:了)?吗[?？]?$"
)
_COMMAND_RE = re.compile(
    rf"^(?:{_PREFIX_PATTERN}(?:{_ACTION_PATTERN})(?:一下)?\s*"
    rf"(?P<target>{_APP_PATTERN}|它)|"
    rf"{_PREFIX_PATTERN}把\s*(?P<object>{_APP_PATTERN})\s*(?:{_ACTION_PATTERN})(?:一下)?)"
    rf"(?:吧)?[!！。]*$"
)


def detect_allowlisted_app_action(
    messages: Sequence[Mapping[str, object]],
    call_id_factory: Callable[[], str],
) -> ToolCall | None:
    users = [str(item.get("content", "")) for item in messages if item.get("role") == "user"]
    if not users:
        return None
    current = users[-1].strip().lower()
    if (
        any(word in current for word in _NON_ACTIONS)
        or _NEGATION_RE.search(current)
        or _QUESTION_RE.search(current)
    ):
        return None
    command = _COMMAND_RE.fullmatch(current)
    if command is None:
        return None
    target = command.group("target") or command.group("object")
    app_ids = {_APP_NAMES[target]} if target != "它" else set()
    if target == "它" and len(users) > 1:
        app_ids = {app_id for name, app_id in _APP_NAMES.items() if name in users[-2].lower()}
    if len(app_ids) != 1:
        return None
    return ToolCall(
        call_id=call_id_factory(),
        name="apps.open_allowlisted",
        arguments={"app_id": app_ids.pop()},
    )
