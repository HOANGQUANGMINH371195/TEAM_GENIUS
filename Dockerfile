# syntax=docker/dockerfile:1
# ---- Stage 1: Build ----
FROM python:3.11-slim-bookworm AS builder

WORKDIR /app
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

COPY requirements/runtime.lock .
RUN python -m venv "${VIRTUAL_ENV}" \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --require-hashes -r runtime.lock \
    && rm -rf "${VIRTUAL_ENV}/bin"/pip* "${VIRTUAL_ENV}/bin"/easy_install* \
        "${VIRTUAL_ENV}/lib/python3.11/site-packages"/pip* \
        "${VIRTUAL_ENV}/lib/python3.11/site-packages"/setuptools* \
        "${VIRTUAL_ENV}/lib/python3.11/site-packages"/wheel*

# ---- Stage 2: Production ----
# Keep the runtime on the same CPython minor as the locked wheels, but refresh
# Debian security metadata during the image build.  Build tools are removed
# from the final layer and the process runs as the dedicated non-root user.
FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get dist-upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
        /usr/local/lib/python3.11/site-packages/pip* \
        /usr/local/lib/python3.11/site-packages/setuptools* \
        /usr/local/lib/python3.11/site-packages/wheel* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

WORKDIR /app
ENV PYTHONPATH="/opt/venv/lib/python3.11/site-packages" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

COPY --from=builder --chown=10001:10001 \
    /opt/venv/lib/python3.11/site-packages /opt/venv/lib/python3.11/site-packages

# Copy only online runtime code; corpus/eval/frontend are separate artifacts.
COPY --chown=10001:10001 src ./src

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD ["/usr/local/bin/python3.11", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=5)"]

# Supabase schema is applied separately through Supabase SQL migrations.

ENTRYPOINT ["/usr/local/bin/python3.11"]
CMD ["-m", "src.runtime_entrypoint"]
