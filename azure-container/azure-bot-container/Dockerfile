# =============================================================================
# GTI Teams Bot (Agentic) — Azure Container Apps image
# =============================================================================
# Plain uvicorn, no gunicorn: a gunicorn worker's own default 30s heartbeat
# timeout would SIGKILL a worker mid-GTI-call (the whole point of this image
# is holding a request open for several minutes). Uvicorn has no equivalent
# self-imposed timeout, and it doesn't need one here — every I/O call in
# app/ is natively async (httpx, azure-sdk .aio clients), so a multi-minute
# GTI call awaits instead of blocking, and the event loop keeps serving every
# other concurrent request the whole time.
#
# slim-bookworm (not bare "slim"): pins the Debian release the image is
# built on so a Debian codename bump (e.g. to trixie) can't silently change
# what's underneath between builds, while still floating the 3.12.x patch
# level so a rebuild picks up upstream security fixes automatically.

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 1000 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --chown here (rather than a separate `chown -R` after) so these files land
# owned by appuser directly, without an extra layer rewriting every file.
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser main.py .

USER appuser

# Container Apps routes traffic to whatever port the app's ingress
# `--target-port` names — this image listens on 8000; set the Container App's
# target port to match (see README.md).
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
