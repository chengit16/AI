FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/apps/worker/src:/app/packages/backend/src

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.10.7 \
    && uv sync --frozen --no-dev --no-install-project

COPY apps/worker/src ./apps/worker/src
COPY packages/backend/src ./packages/backend/src
COPY contracts/observability ./contracts/observability

CMD ["uv", "run", "--no-sync", "celery", "-A", "ai_platform_worker.app.celery_app:celery_app", "worker", "--loglevel=INFO"]
