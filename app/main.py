"""FastAPI 入口：直播导播口令编排 API。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .compiler import CompileError, compile_commands
from .schemas import CommandItem, CompileResponse, ErrorBody

app = FastAPI(
    title="直播导播口令编排 API",
    version="1.0.0",
    description=(
        "接收导播口令数组，解析锚点依赖得到每条口令的起止毫秒时间，"
        "并列出同通道半开区间重叠的冲突 id 对。"
    ),
)


@app.exception_handler(CompileError)
async def compile_error_handler(_request: Request, exc: CompileError) -> JSONResponse:
    """业务编排错误统一返回 422 + 结构化错误码。"""
    return JSONResponse(status_code=422, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """请求体结构错误同样使用统一错误格式。"""
    return JSONResponse(
        status_code=422,
        content={
            "code": "INVALID_REQUEST",
            "message": "请求体不符合口令数组结构",
            # errors 上下文中可能含异常对象，经 jsonable_encoder 保证可序列化
            "details": {"errors": jsonable_encoder(exc.errors())},
        },
    )


@app.get("/health", tags=["meta"], summary="健康检查")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/compile",
    response_model=CompileResponse,
    tags=["compile"],
    summary="编排口令：解析时间并检测通道冲突",
    responses={422: {"model": ErrorBody, "description": "请求非法或编排失败"}},
)
async def compile(items: list[CommandItem]) -> dict:
    # 核心逻辑不依赖 Pydantic，直接喂入 dict 以便独立测试与复用
    return compile_commands([item.model_dump() for item in items])
