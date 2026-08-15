FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/apps/api/src:/app/packages/backend/src

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.10.7 \
    && uv sync --frozen --no-dev --no-install-project

COPY apps/api/src ./apps/api/src
COPY packages/backend/src ./packages/backend/src
COPY alembic.ini ./alembic.ini
COPY infra/migrations ./infra/migrations
COPY contracts/errors ./contracts/errors
COPY contracts/authorization ./contracts/authorization
COPY contracts/observability ./contracts/observability
COPY contracts/fixtures/release-manifest.v1.valid.json ./contracts/fixtures/release-manifest.v1.valid.json
COPY contracts/release ./contracts/release

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "uvicorn", "ai_platform_api.main:app", "--app-dir", "apps/api/src", "--host", "0.0.0.0", "--port", "8000"]
