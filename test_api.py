"""端到端验证编译 API(无需启动服务,使用 FastAPI TestClient)。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {name}")
    else:
        failed += 1
        print(f"FAIL: {name} {detail}")


def post(commands):
    return client.post("/compile", json=commands)


# 1) 基本链路 + 负偏移 + 跨通道 + 同通道冲突 + 首尾相接不冲突
payload = [
    {"id": "a", "channel": "cam1", "anchor": "START", "offset_ms": 0, "duration_ms": 1000},
    # b 锚点为 a(结束 1000),负偏移起点 900,与 a 重叠
    {"id": "b", "channel": "cam1", "anchor": "a", "offset_ms": -100, "duration_ms": 500},
    # c 锚点为 b(结束 1400),起点 1400,首尾相接不算冲突
    {"id": "c", "channel": "cam1", "anchor": "b", "offset_ms": 0, "duration_ms": 200},
    # d 在 cam2,锚点 a:1000+500=1500
    {"id": "d", "channel": "cam2", "anchor": "a", "offset_ms": 500, "duration_ms": 600},
    # e 与 d 同通道:2100-100=2000,与 d 重叠;负偏移合法
    {"id": "e", "channel": "cam2", "anchor": "d", "offset_ms": -100, "duration_ms": 100},
]
r = post(payload)
check("basic status 200", r.status_code == 200, r.text)
if r.status_code == 200:
    data = r.json()
    check(
        "timeline sorted by (start_ms, id)",
        [(t["id"], t["start_ms"], t["end_ms"]) for t in data["timeline"]]
        == [
            ("a", 0, 1000),
            ("b", 900, 1400),
            ("c", 1400, 1600),
            ("d", 1500, 2100),
            ("e", 2000, 2100),
        ],
        data["timeline"],
    )
    check(
        "conflicts: a-b and d-e only, c touching b is fine",
        [(c["channel"], c["ids"]) for c in data["conflicts"]]
        == [("cam1", ["a", "b"]), ("cam2", ["d", "e"])],
        data["conflicts"],
    )

# 2) 空数组
r = post([])
check("empty list -> 200 empty", r.status_code == 200 and r.json() == {"timeline": [], "conflicts": []}, r.text)

# 3) 同起点按 id 排序;同通道完全包含产生一对冲突
r = post([
    {"id": "z", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": 100},
    {"id": "a", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": 100},
])
check("tie start -> id order", [t["id"] for t in r.json()["timeline"]] == ["a", "z"], r.text)
check("same interval -> one conflict sorted pair", r.json()["conflicts"] == [{"channel": "ch", "ids": ["a", "z"]}], r.text)

# 4) 重复 id
r = post([
    {"id": "x", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": 10},
    {"id": "x", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": 10},
])
check("duplicate id -> 400", r.status_code == 400 and r.json()["code"] == "DUPLICATE_ID", r.text)

# 5) 缺失锚点
r = post([
    {"id": "x", "channel": "ch", "anchor": "ghost", "offset_ms": 0, "duration_ms": 10},
])
check("missing anchor -> 400", r.status_code == 400 and r.json()["code"] == "MISSING_ANCHOR", r.text)

# 6) 依赖环(多节点)
r = post([
    {"id": "a", "channel": "ch", "anchor": "c", "offset_ms": 0, "duration_ms": 10},
    {"id": "b", "channel": "ch", "anchor": "a", "offset_ms": 0, "duration_ms": 10},
    {"id": "c", "channel": "ch", "anchor": "b", "offset_ms": 0, "duration_ms": 10},
])
check("cycle a->b->c->a -> 400", r.status_code == 400 and r.json()["code"] == "DEPENDENCY_CYCLE", r.text)
check("cycle detail lists sorted nodes", r.json().get("cycle") == ["a", "b", "c"], r.text)

# 7) 自环(self anchor)按依赖环处理
r = post([
    {"id": "self", "channel": "ch", "anchor": "self", "offset_ms": 0, "duration_ms": 10},
])
check("self anchor -> DEPENDENCY_CYCLE", r.status_code == 400 and r.json()["code"] == "DEPENDENCY_CYCLE", r.text)

# 8) 负起点(START 负偏移)
r = post([
    {"id": "a", "channel": "ch", "anchor": "START", "offset_ms": -1, "duration_ms": 10},
])
check("negative start -> 400 INVALID_TIME", r.status_code == 400 and r.json()["code"] == "INVALID_TIME", r.text)

# 9) 负起点(经依赖传递):b 起点 5,偏移 -10 -> -5
r = post([
    {"id": "a", "channel": "ch1", "anchor": "START", "offset_ms": 0, "duration_ms": 5},
    {"id": "b", "channel": "ch2", "anchor": "a", "offset_ms": -10, "duration_ms": 10},
])
check("negative chained start -> 400", r.status_code == 400 and r.json()["code"] == "INVALID_TIME", r.text)

# 10) duration 非正
for bad in (0, -5):
    r = post([{"id": "a", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": bad}])
    check(f"duration {bad} -> 400 INVALID_TIME", r.status_code == 400 and r.json()["code"] == "INVALID_TIME", r.text)

# 11) 校验顺序:重复 id 优先于其他错误
r = post([
    {"id": "x", "channel": "ch", "anchor": "nope", "offset_ms": 0, "duration_ms": 0},
    {"id": "x", "channel": "ch", "anchor": "nope", "offset_ms": 0, "duration_ms": 0},
])
check("duplicate checked first", r.status_code == 400 and r.json()["code"] == "DUPLICATE_ID", r.text)

# 12) 保留 id START 不可用作口令 id
r = post([{"id": "START", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": 10}])
check("reserved id START -> 400", r.status_code == 400 and r.json()["code"] == "RESERVED_ID", r.text)

# 13) 深层链(无环,不递归爆栈)
n = 5000
chain = [{"id": "n0", "channel": "ch", "anchor": "START", "offset_ms": 1, "duration_ms": 1}]
chain += [
    {"id": f"n{i}", "channel": "ch", "anchor": f"n{i-1}", "offset_ms": 0, "duration_ms": 1}
    for i in range(1, n)
]
r = post(chain)
last = r.json()["timeline"][-1] if r.status_code == 200 else {}
check("deep 5000 chain resolves", r.status_code == 200 and last["id"] == f"n{n-1}" and last["start_ms"] == n, r.text[:300])

# 14) 大环检测(5000 节点)
cyc = [
    {"id": f"n{i}", "channel": "ch", "anchor": f"n{(i+1) % n}", "offset_ms": 0, "duration_ms": 1}
    for i in range(n)
]
r = post(cyc)
check("deep 5000 cycle -> 400", r.status_code == 400 and r.json()["code"] == "DEPENDENCY_CYCLE", r.text[:200])

# 15) 请求体类型错误由 pydantic 422 处理
r = client.post("/compile", json=[{"id": "a"}])
check("schema error -> 422", r.status_code == 422, r.text)

# 16) 未知字段拒绝
r = post([{"id": "a", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": 10, "extra": 1}])
check("unknown field -> 422", r.status_code == 422, r.text)

# 17) 冲突排序:对间按 (channel, a, b),对内按 id
r = post([
    {"id": "b", "channel": "z", "anchor": "START", "offset_ms": 0, "duration_ms": 100},
    {"id": "a", "channel": "z", "anchor": "START", "offset_ms": 0, "duration_ms": 100},
    {"id": "y", "channel": "a", "anchor": "START", "offset_ms": 0, "duration_ms": 100},
    {"id": "x", "channel": "a", "anchor": "START", "offset_ms": 0, "duration_ms": 100},
])
check(
    "conflicts ordered by channel then ids",
    [(c["channel"], c["ids"]) for c in r.json()["conflicts"]]
    == [("a", ["x", "y"]), ("z", ["a", "b"])],
    r.text,
)

# 18) 三个同区间重叠 -> 3 对
r = post([
    {"id": "p", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": 10},
    {"id": "q", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": 10},
    {"id": "r", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": 10},
])
check(
    "three overlapping -> all 3 pairs",
    [c["ids"] for c in r.json()["conflicts"]] == [["p", "q"], ["p", "r"], ["q", "r"]],
    r.text,
)

# 19) 通道相同、完全包含在内部的区间也算重叠
r = post([
    {"id": "outer", "channel": "ch", "anchor": "START", "offset_ms": 0, "duration_ms": 100},
    {"id": "inner", "channel": "ch", "anchor": "START", "offset_ms": 50, "duration_ms": 10},
])
check("nested intervals conflict", r.json()["conflicts"] == [{"channel": "ch", "ids": ["inner", "outer"]}], r.text)

# 20) 健康检查
r = client.get("/health")
check("health ok", r.status_code == 200 and r.json() == {"status": "ok"}, r.text)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
