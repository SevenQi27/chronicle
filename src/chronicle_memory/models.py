"""Boundary models. Internal dates are always timezone-aware UTC values."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须带时区")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(Model):
    id: Text = Field(description="宿主消息或工具结果的稳定 ID；重试使用相同 ID")
    content: Text
    role: Literal["user", "tool", "assistant"] = "user"
    occurred_at: AwareDatetime = Field(description="原消息时间，用于解释昨天、下周等相对时间")
    conversation_id: Text = "default"


class ExperienceTime(Model):
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    precision: Literal["exact", "day", "range", "approximate", "unknown"] = "unknown"
    description: str = ""

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end and (not self.start or self.end < self.start):
            raise ValueError("经历结束时间必须不早于开始时间")
        if self.precision in ("exact", "day", "range") and self.start is None:
            raise ValueError("明确时间精度需要 start；无法确定时使用 approximate 或 unknown")
        if self.precision == "unknown" and (self.start or self.end):
            raise ValueError("unknown 不应填造一个精确时间")
        return self


class Entity(Model):
    id: Text = Field(description="分区内稳定标识，已有同一个人或主题时复用")
    name: Text
    category: Text = Field(default="topic", description="开放类别，如人、地点、主题、物品，不限领域")
    aliases: list[Text] = Field(default_factory=list, max_length=20)
    operation: Literal['mention','correct'] = Field(default='mention',description='mention 复用规范名并追加别名；correct 明确更正规范名、类别和完整别名列表，不改写历史经历正文')

    @model_validator(mode='after')
    def complete_correction(self):
        if self.operation=='correct' and not {'category','aliases'}.issubset(self.model_fields_set):
            raise ValueError('实体 correct 必须明确提交 category 和完整 aliases；空列表表示清除全部别名')
        return self


class Experience(Model):
    id: Text = Field(description="经历的稳定 ID，例如一次交流或一段经历的标识")
    title: Text
    narrative: Text = Field(description="保留背景、过程和原话含义的完整经历叙述；不可编造感受和因果")
    when: ExperienceTime = Field(default_factory=ExperienceTime)
    entities: list[Entity] = Field(default_factory=list, max_length=30)
    source_ids: list[Text] = Field(default_factory=list, description="额外的真实来源 ID；当前 source 自动关联")
    replaces: Text | None = Field(default=None, description="扩展或纠正同一经历时引用最新版本 record_id")
    revision_kind: Literal["update", "correct"] = "update"


class Understanding(Model):
    id: Text = Field(description="认识的稳定 ID")
    title: Text
    statement: Text
    basis: Literal["explicit", "inferred"] = Field(description="明确表达或根据支撑经历归纳")
    supports: list[Text] = Field(min_length=1, description="当前分区内支撑经历的 record_id 或经历 ID")
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    replaces: Text | None = Field(default=None, description="更新时填写同一认识的最新 record_id")
    revision_kind: Literal["update", "correct"] = "update"

    @model_validator(mode="after")
    def valid_interval(self):
        if self.valid_from is not None and self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("认识的 valid_to 必须晚于 valid_from")
        return self


class WriteContext(Model):
    origin: Text | None = None
    workspace: Text | None = None


class MemoryFilters(WriteContext):
    conversation_id: Text | None = None
    source_id: Text | None = None


class Query(Model):
    query: Text
    alternatives: list[Text] = Field(default_factory=list,max_length=3,description='调用方提供的不同表达；原始查询始终保留，不调用额外模型')
    filters: MemoryFilters | None = None
    subject: str | None = None
    at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    limit: int = Field(default=8, ge=1, le=50)
    include_inactive: bool = False
    interpret_time: bool = Field(default=False, description="让模型解析问题中的时间；默认由 Agent 直接提供 at/known_at")


class DetailQuery(Model):
    event_ids: list[Text] = Field(min_length=1, max_length=50)
    at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    source_mode: Literal['full','summary'] = Field(default='full',description='full 返回全部来源正文；summary 保留全部来源标识/角色/时间/长度，省略来源正文，记忆正文仍完整')
    include_withdrawn: bool = False
