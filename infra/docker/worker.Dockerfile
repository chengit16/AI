FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/apps/worker/src

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.10.7 \
    && uv sync --frozen --no-dev --no-install-project

COPY apps/worker/src ./apps/worker/src

CMD ["python", "-m", "ai_platform_worker.health"]
