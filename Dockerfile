FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

# Copy application code
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
COPY config/ config/

CMD ["uv", "run", "python", "-m", "betfair_trading.main"]
