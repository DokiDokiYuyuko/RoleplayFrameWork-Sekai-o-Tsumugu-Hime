"""Reusable expression preferences, resolved separately for each speaker."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ResponseStyle(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    schema_version: int = 1
    revision: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=20000)
    enabled: bool = True

    @field_validator("name", "content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("名称和风格正文不能为空")
        return value


def resolve_response_style(settings, meta, actor_id: str) -> dict | None:
    overrides = meta.response_style_overrides
    source = "角色覆盖" if actor_id in overrides else "分支默认"
    style_id = overrides.get(actor_id, meta.response_style_id)
    style = next((s for s in getattr(settings, "response_styles", [])
                  if s.id == style_id and s.enabled), None)
    if style is None:
        return None
    return {**style.model_dump(mode="json"), "source": source}


def response_style_instruction(style: dict | None) -> str:
    if style is None:
        return ""
    return (
        f"[回应风格：{style['name']}]\n{style['content']}\n"
        "风格只调整表达，不改变角色身份、已确认事实、可见范围和玩家控制权。"
    )
