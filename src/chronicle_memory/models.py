"""Boundary models. Internal dates are always timezone-aware UTC values."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须带时区")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def state_key(subject: str, key: str) -> str:
    return 'state:v2:'+json.dumps([subject,key],ensure_ascii=False,separators=(',',':'))


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(Model):
    id: Text = Field(description="宿主消息或工具结果的稳定 ID；重试使用相同 ID")
    content: Text
    role: Literal["user", "tool", "assistant"] = "user"
    occurred_at: AwareDatetime = Field(description="原消息时间，用于解释昨天、下周等相对时间")
    conversation_id: Text = "default"


class MemoryChange(Model):
    subject: Text = Field(description="稳定主体名；优先复用已有主体")
    key: Text = Field(description="稳定属性或计划 ID，例如 owner、release-v1；同一计划更新时保持不变")
    kind: Literal["fact", "rule", "plan"]
    text: Text = Field(description="独立可理解的简短描述")
    value: JsonValue
    effective_at: AwareDatetime = Field(description="事实/规则生效时间；计划的创建或本次修改时间")
    time_basis: Literal["explicit", "observed"] = "explicit"
    expires_at: AwareDatetime | None = None
    due_at: AwareDatetime | None = Field(default=None, description="计划预定发生时间，不代表届时已经完成")
    review_at: AwareDatetime | None = Field(default=None, description="有明确依据的下次检查时间")
    plan_status: Literal["open", "completed", "cancelled"] = "open"
    operation: Literal["set", "correct"] = "set"
    corrects: str | None = Field(default=None, description="纠错必须指向被纠正记录最新版本的 event_id")

    @field_validator("effective_at", "expires_at", "due_at", "review_at")
    @classmethod
    def normalize_time(cls, value):
        return value.astimezone(timezone.utc) if value is not None else None

    @model_validator(mode="after")
    def check_semantics(self):
        if (self.operation == "correct") != bool(self.corrects):
            raise ValueError("correct 必须指定 corrects；set 不允许指定 corrects")
        if self.expires_at is not None and self.expires_at <= self.effective_at:
            raise ValueError("expires_at 必须晚于 effective_at")
        if self.kind == "plan":
            if self.expires_at is not None:
                raise ValueError("计划到期应检查结果，不能通过 expires_at 自动失效")
            if self.plan_status == "open" and self.due_at is None and self.review_at is None:
                raise ValueError("需要跟踪的计划至少提供 due_at 或 review_at")
        elif self.due_at is not None or self.plan_status != "open":
            raise ValueError("只有 plan 可以设置 due_at 或完成/取消状态")
        if self.review_at is not None and self.review_at < self.effective_at:
            raise ValueError("review_at 不能早于 effective_at")
        return self


class ReviewCandidate(Model):
    text: Text
    reason: Text


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
    id: Text = Field(description="同一项认识的稳定 ID")
    title: Text
    statement: Text = Field(description="从经历中形成的认识，不限于偏好、事实或经验")
    basis: Literal["explicit", "inferred"] = Field(description="explicit 为用户明确表达；inferred 为系统归纳，不能冒充原话")
    supports: list[Text] = Field(min_length=1, max_length=30, description="支撑它的经历版本 record_id，或本次写入的经历 id")
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    review_at: AwareDatetime | None = None
    replaces: Text | None = None
    revision_kind: Literal["update", "correct"] = "update"

    @model_validator(mode="after")
    def valid_interval(self):
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("认识的 valid_to 必须晚于 valid_from")
        return self


class Association(Model):
    id: Text
    from_id: Text = Field(description="经历或认识的稳定 ID 或版本 record_id")
    to_id: Text
    kind: Literal["related_to", "continues", "contradicts", "caused_by", "revises"]
    explanation: Text
    basis: Literal["explicit", "inferred"]
    evidence_ids: list[Text] = Field(min_length=1, max_length=20, description="依据的经历版本或原始来源 ID")
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    replaces: Text | None = Field(default=None, description="更正关联时引用最新 association_version_id")

    @model_validator(mode="after")
    def valid_interval(self):
        if self.from_id == self.to_id:
            raise ValueError("关联的两端应为不同记忆")
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("关联 valid_to 必须晚于 valid_from")
        return self


class Extraction(Model):
    memories: list[MemoryChange] = Field(default_factory=list, max_length=40)
    review: list[ReviewCandidate] = Field(default_factory=list, max_length=40)
    experiences: list[Experience] = Field(default_factory=list, max_length=20)
    understandings: list[Understanding] = Field(default_factory=list, max_length=20)
    associations: list[Association] = Field(default_factory=list, max_length=40)


class WriteContext(Model):
    origin: Text | None = None
    workspace: Text | None = None


class MemoryFilters(WriteContext):
    conversation_id: Text | None = None
    source_id: Text | None = None


class WriteRequest(Extraction):
    source: Source
    context: WriteContext | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not any((self.memories,self.experiences,self.understandings,self.associations,self.review)):
            raise ValueError("至少提供一段经历、一项认识、一个关联或待确认内容")
        return self

    def extraction(self):
        return Extraction.model_validate(self.model_dump(exclude={"source","context"}))


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


class NeighborsQuery(Model):
    record_id: Text
    at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    limit: int = Field(default=12, ge=1, le=30)
    filters: MemoryFilters | None = None


class HistoryQuery(Model):
    memory_id: str | None = None
    entity_id: str | None = None
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    limit: int = Field(default=30, ge=1, le=100)
    filters: MemoryFilters | None = None
    include_withdrawn: bool = False

    @model_validator(mode="after")
    def valid_range(self):
        if self.start and self.end and self.end <= self.start:
            raise ValueError("历史时间范围 end 必须晚于 start")
        return self


class QueryTime(Model):
    at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    include_inactive: bool = False
    clarification: str | None = None


class StateQuery(Model):
    subject: Text
    key: Text
    at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None


class TimelineQuery(Model):
    subject: Text
    key: Text
    known_at: AwareDatetime | None = None


class DetailQuery(Model):
    event_ids: list[Text] = Field(min_length=1, max_length=50)
    at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    source_mode: Literal['full','summary'] = Field(default='full',description='full 返回全部来源正文；summary 保留全部来源标识/角色/时间/长度，省略来源正文，记忆正文仍完整')
    include_withdrawn: bool = False


class PageQuery(Model):
    limit: int = Field(default=50,ge=1,le=100)
    offset: int = Field(default=0,ge=0)


class ReviewsQuery(PageQuery):
    kind: Literal['all','candidate','understanding'] = 'all'
    at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None


class ReviewResolution(Extraction):
    source: Source

    def extraction(self):
        return Extraction.model_validate(self.model_dump(exclude={'source','review_id'}))


class ResolveReviewTool(ReviewResolution):
    review_id: Text


class VisibilityRequest(Model):
    memory_ids: list[Text] = Field(min_length=1,max_length=50)
    action: Literal['withdraw','restore']
    reason: str = ''


class ForgetRequest(Model):
    memory_ids: list[Text] = Field(min_length=1,max_length=50)
    confirmation: str | None = Field(default=None,description='省略时仅预览；用户确认范围后提交预览 token')


class BackupRequest(Model):
    action: Literal['export','validate','restore']
    path: Text
    confirmation: str | None = None
