FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.16 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
# Runtime only: tests run on the host, so the dev group stays out of the image
RUN uv sync --frozen --no-dev --no-install-project

# Same base as the build stage, so .venv's python link still resolves. uv and its download cache stay behind.
FROM python:3.12-slim
WORKDIR /app
COPY --from=build /app/.venv .venv
# Only the engine: docs, tests, scripts and the SQL files (mounted into the db container) are never read here.
COPY app ./app
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
