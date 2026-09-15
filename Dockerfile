FROM gcr.io/distroless/python3-debian13 AS builder
# uv version is pinned here and must match the version used to generate uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.12.11 /uv /uvx /bin/

WORKDIR /app
# uv is a static binary, so it runs in distroless without a shell. Use the
# image's own Python and keep the cache append-only in a temp dir.
ENV HOME=/tmp \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system
COPY . /app
RUN ["/bin/uv", "sync", "--frozen", "--no-dev", "--python", "/usr/bin/python3"]


FROM gcr.io/distroless/python3-debian13:nonroot
WORKDIR /app
COPY --from=builder --chown=nonroot:nonroot /app /app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8500

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import os, sys, urllib.request; port = os.getenv('MYIP_PORT', '8500'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["/app/.venv/bin/python", "/app/src/ip.py"]
