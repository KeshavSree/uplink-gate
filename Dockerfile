# The gate service: API, telemetry ingest, compile workers and (once built) the web UI.
FROM python:3.12-slim AS service
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first, so editing the source does not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra server --no-install-project

COPY gate ./gate
COPY cli ./cli
COPY api ./api
COPY sequences ./sequences
RUN uv sync --frozen --no-dev --extra server

ENV PATH="/app/.venv/bin:${PATH}" \
    GATE_HOME=/var/lib/gate
EXPOSE 8000
CMD ["gate", "serve", "--host", "0.0.0.0", "--port", "8000"]
