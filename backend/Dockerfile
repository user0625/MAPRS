FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv  && \
    uv sync --frozen --no-dev --index-url https://pypi.tuna.tsinghua.edu.cn/simple
# RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev --index-url https://pypi.tuna.tsinghua.edu.cn/simple
COPY backend backend
COPY alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app
CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.api.main:app --host 0.0.0.0 --port 8000"]
