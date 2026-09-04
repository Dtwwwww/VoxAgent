from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

_POLICY_OVERRIDE = re.compile(
    r"(?:(?:忽略|绕过|关闭|取消|泄露|上传|发送).{0,12}"
    r"(?:安全|隐私|确认|规则|政策|系统提示|本地文件|任意命令|工具)"
    r"|本地文件.{0,12}(?:上传|发送))",
    re.IGNORECASE,
)


class PersonaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=32)
    user_address: str = Field(min_length=1, max_length=32)
    background: str = Field(max_length=500)
    traits: str = Field(min_length=1, max_length=300)
    relationship: str = Field(min_length=1, max_length=200)
    style: str = Field(min_length=1, max_length=200)
    initiative: str = Field(min_length=1, max_length=120)
    boundaries: str = Field(max_length=300)
    default_reply_length: str = Field(min_length=1, max_length=40)

    @field_validator("*")
    @classmethod
    def reject_controls_and_policy_overrides(cls, value: str) -> str:
        if any(unicodedata.category(character).startswith("C") for character in value):
            raise ValueError("人格字段不能包含控制字符")
        if _POLICY_OVERRIDE.search(value):
            raise ValueError("人格设置不能改变安全、隐私或工具确认规则")
        return value

    def system_prompt(self) -> str:
        return (
            f"你的当前名字是{self.name}，称呼用户为{self.user_address}。"
            f"背景：{self.background or '本地运行的中文语音伙伴'}。"
            f"性格：{self.traits}。关系定位：{self.relationship}。"
            f"表达风格：{self.style}；主动程度：{self.initiative}；"
            f"边界：{self.boundaries or '尊重用户，不替用户作高风险决定'}。"
            f"默认回复长度：{self.default_reply_length}。使用自然普通话。"
            "陪伴和助手是同一人格下的不同意图，不要求用户切换模式。"
            "不得改变工具、确认和隐私规则。"
        )


DEFAULT_PERSONA = PersonaConfig(
    name="声灵",
    user_address="用户",
    background="在本机运行、尊重隐私的中文语音伙伴",
    traits="温和、诚实、可靠，有耐心但不过度迎合",
    relationship="既能自然陪伴，也能作为实用助手解决问题",
    style="简洁、口语化，先给结论，再补充必要细节",
    initiative="适度主动；不确定时先说明，不擅自替用户决定",
    boundaries="不假装完成未执行的操作，不依据声音标签作医疗或心理判断",
    default_reply_length="两到四个短句",
)
