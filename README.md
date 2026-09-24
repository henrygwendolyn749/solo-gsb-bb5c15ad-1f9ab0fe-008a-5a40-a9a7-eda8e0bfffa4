# 直播导播口令编排 API

基于 Python + FastAPI 的直播导播口令编排服务:接收一组带锚点依赖的导播口令,计算每条口令的起止毫秒时刻,输出按起点排序的时间线,并列出同通道半开区间重叠的冲突口令对。

## 一条命令启动

```bash
docker compose up --build
```

启动后访问:

- 交互式文档(Swagger UI,可直接发请求):<http://localhost:8000/docs>
- 备用文档(ReDoc):<http://localhost:8000/redoc>
- 健康检查:<http://localhost:8000/health>

> 容器在**启动时**创建虚拟环境并通过 `pip install -r requirements.txt` 安装依赖,随后启动 uvicorn(见 `docker/entrypoint.sh`),因此首次启动需要能访问 PyPI,日志出现 `依赖安装完成` 即就绪。
>
> 后台运行:`docker compose up --build -d`;查看日志:`docker compose logs -f`;停止:`docker compose down`。

## 必要配置

| 配置项 | 位置 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 宿主端口 | `docker-compose.yml` / 环境变量 `PORT` | `8000` | 映射到容器内 8000;可用 `PORT=9000 docker compose up` 改写 |
| 依赖清单 | `requirements.txt` | fastapi 0.115 / uvicorn 0.34 / pydantic 2.10 | 启动时由 entrypoint 安装 |

无需数据库、无需任何密钥;容器以非 root 用户运行。

本地开发(不用 Docker):

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## 接口

### `POST /compile`

请求体为口令数组,每项字段:

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string(非空) | 口令唯一标识;`START` 为保留名,不能用作口令 id |
| `channel` | string(非空) | 所属通道(冲突只在同通道内检测) |
| `anchor` | string | 锚点:`"START"`(0 时刻)或另一条口令的 `id`(允许跨通道引用) |
| `offset_ms` | int | 相对**锚点结束时刻**的偏移毫秒,**允许为负** |
| `duration_ms` | int(正数) | 持续毫秒,必须 > 0 |

编排规则:

- `START` 的结束时刻为 `0`;口令起点 = 锚点结束时刻 + `offset_ms`,结束时刻 = 起点 + `duration_ms`。
- 任意口令起点不得小于 0(负偏移本身合法,但解析后起点为负则报错)。
- `timeline` 按 `(start_ms, id)` 升序返回。
- 同通道区间采用**半开区间 `[start_ms, end_ms)`** 判重;首尾相接(`end == start`)**不算**冲突;跨通道不冲突。
- 冲突 `ids` 对内按 id 升序,`conflicts` 对间按 `(channel, 小id, 大id)` 升序。

### 请求示例

```bash
curl -X POST http://localhost:8000/compile \
  -H 'Content-Type: application/json' \
  -d '[
    {"id": "a", "channel": "cam1", "anchor": "START", "offset_ms": 0,    "duration_ms": 1000},
    {"id": "b", "channel": "cam1", "anchor": "a",     "offset_ms": -100, "duration_ms": 500},
    {"id": "c", "channel": "cam1", "anchor": "b",     "offset_ms": 0,    "duration_ms": 200},
    {"id": "d", "channel": "cam2", "anchor": "a",     "offset_ms": 500,  "duration_ms": 600},
    {"id": "e", "channel": "cam2", "anchor": "d",     "offset_ms": -100, "duration_ms": 100}
  ]'
```

响应:

```json
{
  "timeline": [
    {"id": "a", "channel": "cam1", "start_ms": 0,    "end_ms": 1000},
    {"id": "b", "channel": "cam1", "start_ms": 900,  "end_ms": 1400},
    {"id": "c", "channel": "cam1", "start_ms": 1400, "end_ms": 1600},
    {"id": "d", "channel": "cam2", "start_ms": 1500, "end_ms": 2100},
    {"id": "e", "channel": "cam2", "start_ms": 2000, "end_ms": 2100}
  ],
  "conflicts": [
    {"channel": "cam1", "ids": ["a", "b"]},
    {"channel": "cam2", "ids": ["d", "e"]}
  ]
}
```

上例中:`b` 用负偏移提前到 900ms,与 `a` 的半开区间 `[0,1000)` 重叠;`c` 与 `b` 在 1400ms 首尾相接,**不**报冲突;`e` 与 `d` 同通道重叠。

空数组合法,返回 `{"timeline":[],"conflicts":[]}`。

## 错误响应(HTTP 400)

业务错误统一返回 `{"code": ..., "message": ..., ...}`:

| code | 触发条件 |
| --- | --- |
| `DUPLICATE_ID` | 口令 `id` 重复(响应附带 `duplicates`) |
| `MISSING_ANCHOR` | 锚点既不是 `START`,也不是任何口令的 id(附带 `items`) |
| `DEPENDENCY_CYCLE` | 锚点依赖构成环,含口令锚点指向自身(附带 `cycle`) |
| `INVALID_TIME` | `duration_ms <= 0`,或解析后起点 < 0 |
| `RESERVED_ID` | 口令 id 使用了保留名 `START` |

请求体不符合 schema(缺字段、类型错误、多余字段等)返回 HTTP 422(FastAPI/pydantic 标准校验)。

错误示例:

```bash
curl -s -X POST http://localhost:8000/compile \
  -H 'Content-Type: application/json' \
  -d '[{"id":"a","channel":"ch","anchor":"ghost","offset_ms":0,"duration_ms":10}]'
# {"code":"MISSING_ANCHOR","message":"口令锚点不存在: a -> ghost","items":[{"id":"a","anchor":"ghost"}]}
```

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   └── main.py          # FastAPI 应用:依赖解析、时间线、冲突检测
├── docker/
│   └── entrypoint.sh    # 启动时安装依赖,再 exec uvicorn
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── test_api.py          # 端到端用例(pip install httpx 后 python3 test_api.py)
└── README.md
```
