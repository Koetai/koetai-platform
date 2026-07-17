# Koetai Platform — one image, both modes (see KOETAI_MODE in .env.example).
#
# Pinned to 3.12: lightrdf ships no wheel for 3.13 and its source build (maturin)
# fails, so 3.13 breaks the image. Shape inference needs lightrdf, so pin the
# Python rather than drop the dependency. The host venvs run 3.13 with a
# lightrdf built before this became an issue — don't take those as evidence 3.13
# works from a clean install.
FROM python:3.12-slim

# curl: healthcheck. The RDF toolchain (Jena, rudof, rdf-config) is NOT bundled:
# it is optional, large, and only powers shapes/reasoning/diagrams. Point
# JENA_BIN / RUDOF_BIN at mounted binaries if you want those features.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Writable state lives here so the image stays read-only and a volume survives
# rebuilds. Defaults line up with docker-compose.yml.
ENV KOETAI_DB_PATH=/data/koetai.db \
    UPLOAD_DIR=/data/uploads \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data/uploads

EXPOSE 3002

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:3002/health || exit 1

# --workers 1 is deliberate, not a placeholder. services/job_runner.py runs a
# per-process daemon thread that polls the jobs table; a second worker would
# start a second runner and the two would race to claim the same upload job.
# Scale with threads instead.
CMD ["gunicorn", "--bind", "0.0.0.0:3002", "--workers", "1", "--threads", "8", \
     "--timeout", "300", "app:app"]
