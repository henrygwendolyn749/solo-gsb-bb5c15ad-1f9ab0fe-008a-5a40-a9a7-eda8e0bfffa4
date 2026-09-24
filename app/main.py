"""直播导播口令编排 API.

POST /compile 接收一组导播口令,依据锚点依赖计算每条口令的起止毫秒时刻,
按 (起点, id) 排序输出时间线,并检测同通道半开区间重叠冲突。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

START_ANCHOR = "START"

# 校验阶段错误码
ERR_DUPLICATE_ID = "DUPLICATE_ID"
ERR_MISSING_ANCHOR = "MISSING_ANCHOR"
ERR_DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
ERR_INVALID_TIME = "INVALID_TIME"
ERR_RESERVED_ID = "RESERVED_ID"


class CompileError(Exception):
    """编排过程中的领域错误,统一映射为 HTTP 400。"""

    def __init__(self, code: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


class Command(BaseModel):
    """单条导播口令。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="口令唯一 id")
    channel: str = Field(..., min_length=1, description="所属通道")
    anchor: str = Field(
        ...,
        description="锚点:固定字符串 START(0 时刻),或另一条口令的 id",
    )
    offset_ms: int = Field(
        ...,
        description="相对锚点结束时刻的偏移毫秒,允许为负",
    )
    duration_ms: int = Field(..., description="持续毫秒,必须为正数")


class TimelineItem(BaseModel):
    id: str
    channel: str
    start_ms: int
    end_ms: int


class ConflictPair(BaseModel):
    channel: str
    ids: list[str] = Field(..., description="同通道半开区间重叠的一对口令 id,按 id 升序")


class CompileResponse(BaseModel):
    timeline: list[TimelineItem] = Field(
        ..., description="按 start_ms 升序、id 升序排列的完整时间线"
    )
    conflicts: list[ConflictPair] = Field(
        ..., description="同通道区间重叠的口令 id 对;对间按 (channel, a, b) 排序"
    )


app = FastAPI(
    title="直播导播口令编排 API",
    version="1.0.0",
    description="解析口令的锚点依赖,计算起止毫秒并检测同通道区间冲突。",
)


@app.exception_handler(CompileError)
async def compile_error_handler(_request, exc: CompileError) -> JSONResponse:
    payload: dict[str, object] = {"code": exc.code, "message": exc.message}
    payload.update(exc.extra)
    return JSONResponse(status_code=400, content=payload)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _resolve_starts(commands: list[Command]) -> dict[str, int]:
    """依据锚点依赖计算每条口令的起点毫秒。

    START 的结束时刻为 0;口令 i 的起点 = anchor 的结束时刻 + offset_ms。
    采用迭代 DFS,同时检测依赖环;起点小于 0 报 INVALID_TIME。
    """
    by_id = {cmd.id: cmd for cmd in commands}

    # state: 0 未访问, 1 在当前依赖链上, 2 已完成
    state: dict[str, int] = {cmd.id: 0 for cmd in commands}
    starts: dict[str, int] = {}

    for root in commands:
        if state[root.id] == 2:
            continue
        # 栈帧: (当前口令, 已解析起点后是否已收尾)
        # frame[1] 为 False 时表示刚入栈,待检查锚点;True 时锚点已就绪可回填
        stack: list[tuple[Command, bool]] = [(root, False)]
        while stack:
            cmd, resolved = stack[-1]

            if not resolved:
                state[cmd.id] = 1
                if cmd.anchor == START_ANCHOR:
                    anchor_end = 0
                else:
                    dep = by_id[cmd.anchor]  # 锚点存在性已在调用前校验
                    dep_state = state[dep.id]
                    if dep_state == 1:
                        # 找到回边:栈中帧均处于访问中,从依赖目标处截取即为环
                        on_stack = [c.id for c, _resolved in stack]
                        cycle = on_stack[on_stack.index(dep.id):]
                        raise CompileError(
                            ERR_DEPENDENCY_CYCLE,
                            f"口令依赖存在环: {' -> '.join(cycle)} -> {dep.id}",
                            cycle=sorted(cycle),
                        )
                    if dep_state == 2:
                        anchor_end = starts[dep.id] + dep.duration_ms
                    else:
                        stack.append((dep, False))
                        continue

                start = anchor_end + cmd.offset_ms
                if start < 0:
                    raise CompileError(
                        ERR_INVALID_TIME,
                        f"口令 {cmd.id} 的起点 {start}ms 小于 0",
                        id=cmd.id,
                        start_ms=start,
                    )
                starts[cmd.id] = start
                stack[-1] = (cmd, True)
            else:
                state[cmd.id] = 2
                stack.pop()
                # 父帧下次循环会发现依赖已完成(dep_state == 2)并继续解析

    return starts


def _find_conflicts(
    by_id: dict[str, Command], starts: dict[str, int]
) -> list[ConflictPair]:
    """检测同通道半开区间 [start, end) 重叠;首尾相接(end == start)不算冲突。"""
    by_channel: dict[str, list[Command]] = {}
    for cmd in by_id.values():
        by_channel.setdefault(cmd.channel, []).append(cmd)

    conflicts: list[ConflictPair] = []
    for channel, cmds in by_channel.items():
        intervals = [(starts[c.id], starts[c.id] + c.duration_ms, c.id) for c in cmds]
        intervals.sort(key=lambda x: (x[0], x[2]))
        for i, (s1, e1, id1) in enumerate(intervals):
            for s2, _e2, id2 in intervals[i + 1:]:
                if s2 >= e1:
                    break  # 已按起点排序,后续均不再重叠(含首尾相接)
                pair = sorted([id1, id2])
                conflicts.append(ConflictPair(channel=channel, ids=pair))

    conflicts.sort(key=lambda c: (c.channel, c.ids[0], c.ids[1]))
    return conflicts


@app.post("/compile", response_model=CompileResponse)
def compile_commands(commands: list[Command]) -> CompileResponse:
    if not commands:
        return CompileResponse(timeline=[], conflicts=[])

    # 1. 重复 id
    seen: set[str] = set()
    duplicates: list[str] = []
    for cmd in commands:
        if cmd.id in seen and cmd.id not in duplicates:
            duplicates.append(cmd.id)
        seen.add(cmd.id)
    if duplicates:
        raise CompileError(
            ERR_DUPLICATE_ID,
            f"口令 id 重复: {', '.join(sorted(duplicates))}",
            duplicates=sorted(duplicates),
        )

    by_id = {cmd.id: cmd for cmd in commands}

    # 2. 保留 id
    if START_ANCHOR in by_id:
        raise CompileError(
            ERR_RESERVED_ID,
            f"id {START_ANCHOR!r} 为保留锚点名,不得作为口令 id",
            ids=[START_ANCHOR],
        )

    # 3. 非法时间(duration 必须为正,在依赖解析前统一校验)
    bad_duration = sorted(c.id for c in commands if c.duration_ms <= 0)
    if bad_duration:
        raise CompileError(
            ERR_INVALID_TIME,
            f"口令 duration_ms 必须为正数: {', '.join(bad_duration)}",
            ids=bad_duration,
        )

    # 4. 缺失锚点(含锚点指向自己以外的不存在 id;指向自己由环检测处理)
    missing: list[tuple[str, str]] = []
    for cmd in commands:
        if cmd.anchor != START_ANCHOR and cmd.anchor not in by_id:
            missing.append((cmd.id, cmd.anchor))
    if missing:
        detail = ", ".join(f"{cid} -> {anchor}" for cid, anchor in sorted(missing))
        raise CompileError(
            ERR_MISSING_ANCHOR,
            f"口令锚点不存在: {detail}",
            items=[{"id": cid, "anchor": anchor} for cid, anchor in sorted(missing)],
        )

    # 5. 解析依赖(含依赖环、负起点检测)
    starts = _resolve_starts(commands)

    # 6. 组装时间线,按 (start_ms, id) 排序
    timeline = [
        TimelineItem(
            id=cmd.id,
            channel=cmd.channel,
            start_ms=starts[cmd.id],
            end_ms=starts[cmd.id] + cmd.duration_ms,
        )
        for cmd in commands
    ]
    timeline.sort(key=lambda item: (item.start_ms, item.id))

    # 7. 同通道半开区间冲突
    conflicts = _find_conflicts(by_id, starts)

    return CompileResponse(timeline=timeline, conflicts=conflicts)
