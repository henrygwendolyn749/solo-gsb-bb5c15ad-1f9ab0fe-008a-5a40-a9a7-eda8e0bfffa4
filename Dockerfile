FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# requirements 单独拷贝,便于利用构建缓存
COPY requirements.txt /app/requirements.txt
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && useradd -m -u 10001 appuser

COPY app /app/app

RUN mkdir -p /opt/venv && chown -R appuser:appuser /opt/venv /app
USER appuser

EXPOSE 8000

# 容器启动时安装依赖(安装到 /opt/venv),随后启动 uvicorn
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
