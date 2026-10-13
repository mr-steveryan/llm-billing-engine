FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.16 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
# Runtime only: tests run on the host, so the dev group stays out of the image
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
