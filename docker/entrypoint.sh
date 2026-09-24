#!/bin/sh
# 容器启动时创建虚拟环境并安装依赖,随后执行容器主命令(CMD)。
set -eu

echo "[entrypoint] 安装依赖到 ${VIRTUAL_ENV:-/opt/venv} ..."
python -m venv "$VIRTUAL_ENV"
pip install -r /app/requirements.txt

echo "[entrypoint] 依赖安装完成,启动服务: $*"
exec "$@"
