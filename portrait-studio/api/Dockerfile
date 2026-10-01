# syntax=docker/dockerfile:1
# =============================================================================
# portrait-studio API image: builds the web UI (multi-stage) and serves it together with the API on one port.
#   docker compose build api        (context = portrait-studio/, see docker-compose.yml)
# =============================================================================
ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.12-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.17

# ---- web: pnpm build -> /web/dist ------------------------------------------------
FROM ${NODE_IMAGE} AS web
WORKDIR /web
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
COPY web/package.json web/pnpm-lock.yaml web/.npmrc ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
COPY docs/api/openapi.json /docs/api/openapi.json
RUN pnpm build

# ---- python deps -------------------------------------------------------------------
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY api/pyproject.toml api/uv.lock api/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime ------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH \
    DATA_DIR=/data WORKFLOWS_DIR=/app/workflows WEB_DIST_DIR=/app/web/dist \
    COMPLIANCE_PATH=/app/docs/COMPLIANCE.md FACE_MODEL_ROOT=/models/insightface \
    HOST=0.0.0.0 PORT=8000
# opencv-python-headless still needs libglib; libgomp for onnxruntime
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*
# uid/gid default to 1000 = the WSL2 host user that owns the bind-mounted DATA_DIR (docker-compose.yml passes the
# same COMFY_UID/COMFY_GID the comfyui service uses) — otherwise the app user cannot write under /data at runtime.
# setup_user.sh renames any account the base image already has at that id instead of failing on "already exists".
ARG API_UID=1000
ARG API_GID=1000
COPY docker/setup_user.sh /tmp/setup_user.sh
RUN bash /tmp/setup_user.sh app ${API_UID} ${API_GID} /app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY api/app ./app
COPY workflows ./workflows
COPY docs/COMPLIANCE.md ./docs/COMPLIANCE.md
COPY --from=web /web/dist ./web/dist
RUN mkdir -p /data /models/insightface && chown -R ${API_UID}:${API_GID} /data /models
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8000/api/health >/dev/null || exit 1
# The container listens on 0.0.0.0 inside its network namespace; compose publishes 127.0.0.1:8000 only.
CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST} --port ${PORT}"]
