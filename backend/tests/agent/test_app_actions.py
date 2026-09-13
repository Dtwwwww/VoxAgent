from voxagent.agent.app_actions import detect_allowlisted_app_action


def detect(text: str, *history: dict[str, str]):
    return detect_allowlisted_app_action(
        [*history, {"role": "user", "content": text}],
        call_id_factory=lambda: "app-call-1",
    )


def test_routes_each_explicit_allowlisted_app_action() -> None:
    assert detect("打开记事本").arguments == {"app_id": "notepad"}
    assert detect("再帮我打开计算器").arguments == {"app_id": "calculator"}
    assert detect("切回计算器").arguments == {"app_id": "calculator"}
    assert detect("切换一下计算器").arguments == {"app_id": "calculator"}
    assert detect("唤起计算器").arguments == {"app_id": "calculator"}
    assert detect("请再帮我打开计算器").arguments == {"app_id": "calculator"}
    assert detect("麻烦你再打开计算器").arguments == {"app_id": "calculator"}
    assert detect("把计算器打开").arguments == {"app_id": "calculator"}


def test_resolves_it_only_from_the_previous_user_app_reference() -> None:
    call = detect("再打开它", {"role": "user", "content": "打开计算器"})
    assert call is not None and call.arguments == {"app_id": "calculator"}


def test_rejects_negation_questions_and_calculation_requests() -> None:
    assert detect("不要打开计算器") is None
    assert detect("我不想打开计算器") is None
    assert detect("不许打开计算器") is None
    assert detect("怎么打开计算器？") is None
    assert detect("计算器打开了吗？") is None
    assert detect("计算器有什么功能") is None
    assert detect("用计算器算一下 2+2") is None
