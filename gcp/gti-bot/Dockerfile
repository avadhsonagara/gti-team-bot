# =============================================================================
# GTI Teams Bot (Agentic) — GCP Cloud Run container image
#
# Async-native: uvicorn serves main:api directly (see main.py's __main__
# block), no WSGI/Cloud Functions bridging layer involved.
#
# Build:  docker build -t gti-team-bot .
# Run:    docker run --rm -p 8080:8080 \
#           -e CLIENT_ID=dummy -e CLIENT_SECRET=dummy -e TENANT_ID=dummy \
#           -e GTI_API_KEY=dummy gti-team-bot
# Test:   curl http://localhost:8080/health
# =============================================================================
FROM python:3.11-slim

# PYTHONUNBUFFERED keeps stdout/stderr unbuffered so Cloud Logging sees lines
# as they're written; PYTHONDONTWRITEBYTECODE skips .pyc files in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first so this layer stays cached across builds unless
# requirements.txt actually changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then copy the application code.
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Cloud Run injects PORT at runtime; default here matches `docker run` without -e PORT.
ENV PORT=8080
EXPOSE 8080

# main.py's __main__ block reads settings.port (from the PORT env var) and
# calls uvicorn.run(...) itself — single source of truth for how the app starts,
# whether run via `python main.py` locally or in this container.
CMD ["python", "main.py"]
