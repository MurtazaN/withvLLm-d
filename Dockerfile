# syntax=docker/dockerfile:1.6
# =============================================================================
# Blue Lantern — application image.
#
# Two-stage build:
#   1. frontend-build (node:20-slim) — vendors fonts + builds Tailwind CSS.
#      Output goes to /build/static; node_modules never reach the runtime image.
#   2. runtime (python:3.11-slim) — installs Python deps with uv from a
#      frozen lockfile, copies the blue_lantern source, and pulls the built
#      static assets from stage 1.
#
# Self-hosted CSS + fonts mean no runtime dependency on cdn.tailwindcss.com
# or fonts.googleapis.com — needed because FastAPI Guard's default COEP
# headers block cross-origin scripts.
# =============================================================================

# ─────────────────── Stage 1: frontend assets ───────────────────
FROM node:20-slim AS frontend-build

WORKDIR /build

# Install npm deps first so this layer caches across source changes.
COPY src/blue_lantern/frontend/styles/package.json ./styles/
RUN cd styles && npm install --no-audit --no-fund --loglevel=error

# Templates are needed at build time so Tailwind can scan them for classes.
COPY src/blue_lantern/frontend/templates/ ./templates/
COPY src/blue_lantern/frontend/styles/ ./styles/

# Build CSS + copy fonts into /build/static/.
RUN cd styles && npm run build

# ─────────────────── Stage 2: Python runtime ───────────────────
FROM python:3.11-slim

# uv from its official image — small static binary, no Python deps to manage.
COPY --from=ghcr.io/astral-sh/uv:0.5.5 /uv /usr/local/bin/uv

WORKDIR /app

# Deps layer (cache-stable across source changes).
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-hashes --no-emit-project --format requirements-txt > /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Application source — preserve src-layout so editable install resolves
# packages = ["src/blue_lantern"] from pyproject.toml.
COPY src/ /app/src/

# Built frontend assets from stage 1 (tailwind.css + fonts).
COPY --from=frontend-build /build/static /app/src/blue_lantern/frontend/static

# Install the package (so `python -m blue_lantern.backend.server` works without
# sys.path tricks; --no-deps because they're already installed) and set up
# the non-root runtime user, in one layer.
RUN uv pip install --system --no-cache --no-deps -e . \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /app/src/blue_lantern/benchmark/results \
    && chown -R app:app /app
USER app

ENV PYTHONUNBUFFERED=1 \
    BENCHMARK_OUTPUT_DIR=/app/src/blue_lantern/benchmark/results

EXPOSE 7860

CMD ["python", "-m", "blue_lantern.backend.server"]
