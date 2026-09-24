"""请求 / 响应的 Pydantic 模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommandItem(BaseModel):
    """单条导播口令。

    ``anchor`` 为 ``START`` 时表示以时间原点（0 ms）为锚；也可以填另一条
    口令的 id，此时 ``offset_ms`` 相对该锚点口令的 **结束时刻** 计算。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="口令唯一标识；不能为空，也不能是保留字 START")
    channel: str = Field(..., description="通道标识；只有同一通道内的口令才会产生冲突")
    anchor: str = Field(
        default="START",
        description="锚点：START（时间原点）或另一条口令的 id",
    )
    offset_ms: int = Field(
        default=0,
        description="相对锚点结束时刻的偏移（毫秒），允许为负",
    )
    duration_ms: int = Field(..., description="持续时长（毫秒），必须为正数")


class TimelineEntry(BaseModel):
    id: str
    channel: str
    anchor: str
    offset_ms: int
    duration_ms: int
    start_ms: int = Field(..., description="解析后的起点（毫秒，>= 0）")
    end_ms: int = Field(..., description="解析后的结束时刻（毫秒）")


class ConflictPair(BaseModel):
    channel: str = Field(..., description="冲突所在通道")
    a: str = Field(..., description="冲突对中 id 较小者")
    b: str = Field(..., description="冲突对中 id 较大者")
    a_interval: list[int] = Field(..., description="[a 的起点, a 的结束时刻)")
    b_interval: list[int] = Field(..., description="[b 的起点, b 的结束时刻)")


class CompileResponse(BaseModel):
    timeline: list[TimelineEntry] = Field(
        ..., description="按 (start_ms, id) 升序排列的时间线"
    )
    conflicts: list[ConflictPair] = Field(
        ..., description="同通道半开区间重叠的冲突 id 对，按 (a, b, channel) 排序"
    )


class ErrorBody(BaseModel):
    code: Literal[
        "INVALID_REQUEST",
        "DUPLICATE_ID",
        "INVALID_ID",
        "INVALID_CHANNEL",
        "INVALID_DURATION",
        "MISSING_ANCHOR",
        "DEPENDENCY_CYCLE",
        "NEGATIVE_START",
    ]
    message: str
    details: dict | None = None
