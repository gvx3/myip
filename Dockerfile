FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.6.10 /uv /uvx /bin/

WORKDIR /app
#Use system python interpreter instead of uv managed python
ENV UV_PYTHON_DOWNLOADS=never
COPY . /app
#Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["python3", "/app/src/ip.py"]