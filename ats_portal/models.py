"""Versioned external contract. A mode is data, never an enum."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message
        super().__init__(message)


class Mode(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)

    @field_validator('id', 'name')
    @classmethod
    def meaningful(cls, v):
        if not v.strip() or any(ord(c) < 32 for c in v):
            raise ValueError('模式标识和名称不能为空或包含控制字符')
        return v


class Artifact(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    kind: Literal['report', 'log', 'note', 'attachment'] = 'log'

    @field_validator('name')
    @classmethod
    def safe_name(cls, v):
        if v in ('.', '..') or '/' in v or '\\' in v or any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError('文件名必须是安全的单个文件名')
        return v


class Label(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def meaningful(cls, v):
        if not v.strip() or any(ord(c) < 32 for c in v):
            raise ValueError('标签名称不能为空或包含控制字符')
        return v.strip()


class AnnotationPut(BaseModel):
    label_ids: list[str] = Field(min_length=1, max_length=50)
    note: str = Field(default='', max_length=2000)
    author: str = Field(default='', max_length=100)

    @field_validator('label_ids')
    @classmethod
    def meaningful_ids(cls, v):
        if not all(isinstance(x, str) and x for x in v):
            raise ValueError('label_ids 必须包含非空字符串')
        return list(dict.fromkeys(v))

    @field_validator('note', 'author')
    @classmethod
    def no_controls(cls, v):
        if any(ord(c) < 32 and c not in '\n\t' for c in v):
            raise ValueError('说明和作者不能包含控制字符')
        return v


class RunMetaPut(BaseModel):
    device: str = Field(default='', max_length=500)
    firmware: str = Field(default='', max_length=500)
    author: str = Field(default='', max_length=100)

    @field_validator('device', 'firmware', 'author')
    @classmethod
    def no_controls(cls, v):
        if any(ord(c) < 32 and c not in '\n\t' for c in v):
            raise ValueError('设备、固件和作者不能包含控制字符')
        return v.strip()


class Manifest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$')
    mode: Mode
    started_at: datetime
    ended_at: datetime | None = None
    execution_state: Literal['completed', 'aborted', 'unknown'] = 'unknown'
    source: dict[str, str] = Field(default_factory=dict)
    artifacts: list[Artifact] = Field(min_length=1, max_length=100)

    @field_validator('started_at', 'ended_at')
    @classmethod
    def aware(cls, v):
        if v is not None and v.utcoffset() is None:
            raise ValueError('时间必须包含时区，例如 +08:00')
        return v

    @model_validator(mode='after')
    def coherent(self):
        if self.ended_at and self.ended_at < self.started_at:
            raise ValueError('结束时间不能早于开始时间')
        names = [a.name for a in self.artifacts]
        if len(names) != len(set(names)):
            raise ValueError('文件名不能重复')
        if sum(a.kind == 'report' for a in self.artifacts) > 1:
            raise ValueError('每次运行最多包含一份结构化报告')
        if len(self.source) > 20 or any(len(k) > 80 or len(v) > 500 for k, v in self.source.items()):
            raise ValueError('source 字段过多或过长')
        return self
