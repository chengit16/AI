FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/apps/api/src

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.10.7 \
    && uv sync --frozen --no-dev --no-install-project

COPY apps/api/src ./apps/api/src

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "uvicorn", "ai_platform_api.main:app", "--app-dir", "apps/api/src", "--host", "0.0.0.0", "--port", "8000"]
