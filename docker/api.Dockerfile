# syntax=docker/dockerfile:1.7
#
# One image, three roles. `api`, `worker` and `beat` all run this image with
# different commands, so a metric computed in a scheduled report is computed by
# byte-identical code to the one rendered on screen.
#
# Two stages so no build toolchain survives into the runtime layer.

# --------------------------------------------------------------------------
# Stage 1 — build the wheel environment
# --------------------------------------------------------------------------
FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Dependency layer first: pyproject alone changes far less often than source,
# so ordinary code edits reuse this layer.
COPY api/pyproject.toml api/README.md ./
COPY api/src ./src

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install .

# --------------------------------------------------------------------------
# Stage 2 — runtime
# --------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root from here on (BRD §8 security: containers non-root, slim base).
RUN groupadd --system --gid 10001 lnd \
 && useradd  --system --uid 10001 --gid lnd --create-home --home-dir /home/lnd lnd

COPY --from=build /opt/venv /opt/venv

WORKDIR /srv

# Alembic config and migrations ship with the image so the one-shot migration
# container is the same artefact as the running service.
COPY --chown=lnd:lnd api/alembic.ini ./alembic.ini
COPY --chown=lnd:lnd api/alembic     ./alembic
COPY --chown=lnd:lnd api/src         ./src

USER lnd

EXPOSE 8000

# urllib rather than curl: the runtime layer stays free of extra packages.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health/live',timeout=4).status==200 else 1)"]

CMD ["gunicorn", "lnd.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--timeout", "60", \
     "--graceful-timeout", "30"]
