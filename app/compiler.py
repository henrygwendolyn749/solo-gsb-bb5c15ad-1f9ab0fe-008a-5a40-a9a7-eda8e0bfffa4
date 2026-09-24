"""口令编排核心逻辑（纯标准库实现，不依赖 Web 框架）。

规则概述
--------
* 每条口令包含：唯一 ``id``、``channel``（通道）、``anchor``（锚点，``START``
  或另一条口令的 id）、相对锚点 **结束时刻** 的 ``offset_ms``、正数
  ``duration_ms``。
* 虚拟锚点 ``START`` 的起点与结束时刻均为 0；offset 允许为负，但口令起点
  不得小于 0。
* 起点解析后按 ``(start_ms, id)`` 排序输出；同通道的半开区间
  ``[start_ms, end_ms)`` 相互重叠时产生冲突对，首尾相接不算冲突。
"""

from __future__ import annotations

from typing import Any

START = "START"

# 遍历依赖图时的节点状态
_VISITING = 0
_DONE = 1


class CompileError(Exception):
    """编排失败的业务异常，由 HTTP 层统一转换为 422 响应。"""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return body


def compile_commands(items: list[dict[str, Any]]) -> dict[str, Any]:
    """编译口令数组，返回 ``timeline`` 与 ``conflicts``。

    :raises CompileError: 重复 id、非法 id/通道/时长、缺失锚点、依赖环、
        起点为负时抛出，``code`` 可用于程序化区分错误类型。
    """
    by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    duplicates: list[str] = []

    # 1) 建立 id 索引并检测重复 id
    for item in items:
        cid = item["id"]
        if cid in by_id:
            if cid not in duplicates:
                duplicates.append(cid)
            continue
        by_id[cid] = item
        ordered_ids.append(cid)

    if duplicates:
        raise CompileError(
            "DUPLICATE_ID",
            f"存在重复的口令 id: {', '.join(sorted(duplicates))}",
            {"duplicates": sorted(duplicates)},
        )

    # 2) 字段级合法性校验
    invalid_ids = [cid for cid in ordered_ids if cid == "" or cid == START]
    if invalid_ids:
        raise CompileError(
            "INVALID_ID",
            "口令 id 不能为空，也不能使用保留锚点名 START",
            {"ids": invalid_ids},
        )

    empty_channels = [cid for cid in ordered_ids if by_id[cid]["channel"] == ""]
    if empty_channels:
        raise CompileError(
            "INVALID_CHANNEL",
            "通道名不能为空",
            {"ids": sorted(empty_channels)},
        )

    bad_duration = [
        cid for cid in ordered_ids if by_id[cid]["duration_ms"] <= 0
    ]
    if bad_duration:
        raise CompileError(
            "INVALID_DURATION",
            "duration_ms 必须为正数",
            {"ids": bad_duration},
        )

    # 锚点缺省为 START
    for item in by_id.values():
        item.setdefault("anchor", START)
        item.setdefault("offset_ms", 0)

    # 3) 锚点必须存在（START 为虚拟锚点）
    missing = [
        cid
        for cid in ordered_ids
        if by_id[cid]["anchor"] != START and by_id[cid]["anchor"] not in by_id
    ]
    if missing:
        raise CompileError(
            "MISSING_ANCHOR",
            "存在指向未知锚点的口令",
            {
                "ids": sorted(missing),
                "anchors": sorted(
                    {
                        by_id[cid]["anchor"]
                        for cid in missing
                        if by_id[cid]["anchor"] not in by_id
                    }
                ),
            },
        )

    # 4) 解析依赖图：检测环 + 计算起点/结束时刻（迭代 DFS，避免长链递归深度问题）
    starts, ends = _resolve_times(ordered_ids, by_id)

    # 5) 起点不得小于 0（按输入顺序给出确定性报错）
    negative = [cid for cid in ordered_ids if starts[cid] < 0]
    if negative:
        cid = negative[0]
        raise CompileError(
            "NEGATIVE_START",
            f"口令 {cid} 的起点为 {starts[cid]} ms，起点不得小于 0",
            {
                "id": cid,
                "start_ms": starts[cid],
                "anchor": by_id[cid]["anchor"],
                "offset_ms": by_id[cid]["offset_ms"],
            },
        )

    # 6) 时间线：按 (起点, id) 排序
    timeline = [
        {
            "id": cid,
            "channel": by_id[cid]["channel"],
            "anchor": by_id[cid]["anchor"],
            "offset_ms": by_id[cid]["offset_ms"],
            "duration_ms": by_id[cid]["duration_ms"],
            "start_ms": starts[cid],
            "end_ms": ends[cid],
        }
        for cid in sorted(ordered_ids, key=lambda x: (starts[x], x))
    ]

    # 7) 同通道半开区间重叠冲突
    conflicts = _find_conflicts(ordered_ids, by_id, starts, ends)

    return {"timeline": timeline, "conflicts": conflicts}


def _resolve_times(
    ordered_ids: list[str], by_id: dict[str, dict[str, Any]]
) -> tuple[dict[str, int], dict[str, int]]:
    """迭代 DFS 解析每条口令的 start_ms / end_ms，并检测依赖环。"""
    state: dict[str, int] = {}
    starts: dict[str, int] = {START: 0}
    ends: dict[str, int] = {START: 0}
    path: list[str] = []  # 当前 DFS 路径，用于还原环上的 id 序列

    def cycle_error(target: str) -> CompileError:
        # path 中 target 一定存在：VISITING 节点必然在当前路径上
        idx = path.index(target)
        cycle = path[idx:] + [target]
        return CompileError(
            "DEPENDENCY_CYCLE",
            f"检测到依赖环: {' -> '.join(cycle)}",
            {"cycle": cycle},
        )

    for root in ordered_ids:
        if state.get(root) == _DONE:
            continue
        todo: list[tuple[str, str]] = [("enter", root)]
        while todo:
            kind, node = todo.pop()

            if kind == "enter":
                status = state.get(node)
                if status == _DONE:
                    continue
                if status == _VISITING:
                    # 从更深层依赖绕回了路径上的节点
                    raise cycle_error(node)

                state[node] = _VISITING
                path.append(node)

                anchor = by_id[node]["anchor"]
                if anchor == START or state.get(anchor) == _DONE:
                    _place(node, by_id, starts, ends, state, path)
                else:
                    if state.get(anchor) == _VISITING:
                        # 直接指向当前路径上的祖先
                        raise cycle_error(anchor)
                    # 先解析锚点子树，退出本节点时再落时间
                    todo.append(("exit", node))
                    todo.append(("enter", anchor))
            else:  # exit：锚点子树已全部解析完毕
                _place(node, by_id, starts, ends, state, path)

    return starts, ends


def _place(
    node: str,
    by_id: dict[str, dict[str, Any]],
    starts: dict[str, int],
    ends: dict[str, int],
    state: dict[str, int],
    path: list[str],
) -> None:
    anchor_end = ends[by_id[node]["anchor"]]
    start = anchor_end + by_id[node]["offset_ms"]
    starts[node] = start
    ends[node] = start + by_id[node]["duration_ms"]
    state[node] = _DONE
    path.pop()


def _find_conflicts(
    ordered_ids: list[str],
    by_id: dict[str, dict[str, Any]],
    starts: dict[str, int],
    ends: dict[str, int],
) -> list[dict[str, Any]]:
    """返回同通道内半开区间重叠的 id 对（对内、对间均按 id 排序）。"""
    channels: dict[str, list[str]] = {}
    for cid in ordered_ids:
        channels.setdefault(by_id[cid]["channel"], []).append(cid)

    conflicts: list[dict[str, Any]] = []
    for channel, cids in channels.items():
        cids.sort(key=lambda cid: (starts[cid], cid))
        for i, left in enumerate(cids):
            for right in cids[i + 1 :]:
                if starts[right] >= ends[left]:
                    break  # 后续起点更晚；相等即首尾相接，不算冲突
                a, b = sorted((left, right))
                conflicts.append(
                    {
                        "channel": channel,
                        "a": a,
                        "b": b,
                        "a_interval": [starts[a], ends[a]],
                        "b_interval": [starts[b], ends[b]],
                    }
                )

    conflicts.sort(key=lambda c: (c["a"], c["b"], c["channel"]))
    return conflicts
