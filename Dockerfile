FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.16 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project

COPY . .
CMD ["uv", "run", "--frozen", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
