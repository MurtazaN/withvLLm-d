# syntax=docker/dockerfile:1.6
# =============================================================================
# SOC-Claw — application image.
#
# Plain Python base. vLLM is NOT bundled here; it runs on the host (or as a
# separate compose service in a follow-up). Everything routing-related is
# read from env at runtime — see soc-claw/utils.py:get_client.
# =============================================================================

FROM python:3.11-slim

WORKDIR /app

# Deps layer first so source-only changes don't bust it.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source.
COPY soc-claw/ /app/

# Non-root runtime user.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/benchmark/results \
    && chown -R app:app /app
USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    SOC_CLAW_MODEL=nvidia/Nemotron-Mini-4B-Instruct \
    BENCHMARK_OUTPUT_DIR=/app/benchmark/results

EXPOSE 7860

CMD ["python", "ui/server.py"]
