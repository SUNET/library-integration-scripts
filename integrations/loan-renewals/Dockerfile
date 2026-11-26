# Build from repository root:
#   docker build -f integrations/loan-renewals/Dockerfile -t loan-renewals:0.1.0 .

# --- Stage 1: Builder (uses uv to resolve & install non-editable wheels) ---
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /build

# Copy local path dependency and integration project preserving relative layout
COPY packages/folio-client ./packages/folio-client
COPY integrations/loan-renewals ./integrations/loan-renewals

WORKDIR /build/integrations/loan-renewals
RUN uv sync --frozen --no-dev --no-editable


# --- Stage 2: Minimal runtime (no uv, no build tools, non-root user) ---
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --shell /bin/sh appuser

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /build/integrations/loan-renewals/tenants.toml /app/tenants.toml

RUN chown -R appuser:appuser /app
USER appuser

CMD ["loan-renewals", "--help"]
