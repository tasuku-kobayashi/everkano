# syntax=docker/dockerfile:1
# =============================================================================
# everkano API（FastAPI）— 本番/ステージング用イメージ（H5: Vercel ではなく Fly.io 等で動かす）
#
# ビルドコンテキストは「リポジトリのルート」（packages/personas と packages/prompts を同梱するため）:
#   docker build -f apps/api/Dockerfile -t everkano-api .
# Docker Hub のレート制限を避けたい場合はミラーを指定できる:
#   docker build -f apps/api/Dockerfile --build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.12-slim -t everkano-api .
# =============================================================================
ARG PYTHON_IMAGE=python:3.12-slim

# ---- builder: 依存関係を /opt/venv にインストール --------------------------------
FROM ${PYTHON_IMAGE} AS builder
ARG UV_VERSION=0.8.17
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
# TLS を中継するプロキシ配下でビルドする場合は、その CA 証明書を BuildKit secret で渡せる（任意）:
#   docker build --secret id=extra_ca,src=/path/to/ca-bundle.crt -f apps/api/Dockerfile .
# secret はイメージのレイヤーには残らない。
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install "uv==${UV_VERSION}"
WORKDIR /build
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --no-install-project

# ---- runtime ---------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
# FORWARDED_ALLOW_IPS: X-Forwarded-For / X-Forwarded-Proto を信頼するプロキシのIP（uvicorn --proxy-headers）。
#   既定は 127.0.0.1（信頼しない）。Fly.io では fly.toml で "*" にし、ログ用のクライアントIPは
#   偽装できない CLIENT_IP_HEADER=Fly-Client-IP から取得する（"*" の X-Forwarded-For 先頭は偽装可能なため）。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PERSONAS_DIR=/srv/everkano/personas \
    PROMPTS_DIR=/srv/everkano/prompts \
    FORWARDED_ALLOW_IPS="127.0.0.1"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY apps/api/app ./app
# 運用スクリプト（EMBEDDING_MODE / EMBEDDING_MODEL 切り替え後の記憶の再埋め込み）
COPY apps/api/scripts/reembed_memories.py ./scripts/reembed_memories.py
# ペルソナ YAML とプロンプトテンプレート（起動時に検証される）
COPY packages/personas /srv/everkano/personas
COPY packages/prompts/templates /srv/everkano/prompts

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

# レート制限はプロセス内メモリのため 1 プロセスで起動する（スケールはマシン数で行う）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log", "--timeout-graceful-shutdown", "20"]
