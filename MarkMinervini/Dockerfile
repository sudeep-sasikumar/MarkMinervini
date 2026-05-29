FROM python:3.11-slim

# Build-time metadata — injected by GitHub Actions CI.
# Visible as environment variables inside the container and on the dashboard.
# If missing (local build), defaults to "dev".
ARG GIT_COMMIT=dev
ARG BUILD_DATE=unknown
ENV GIT_COMMIT=$GIT_COMMIT
ENV BUILD_DATE=$BUILD_DATE

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    curl sqlite3 gcc \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code — always rebuilt fresh (no-cache: true in GHA workflow).
COPY . .

# Create database subdirectory and logs for persistence.
# Volume is mounted at /app/data/db/ (NOT /app/data/) so Python source files
# in /app/data/*.py are never shadowed by stale volume contents.
RUN mkdir -p /app/data/db /app/logs

# Self-healing source backup — immune to volume shadowing.
# /app/data_bak/ is NEVER volume-mounted, so it always holds the current
# image's Python files.  docker-entrypoint.sh copies them back to /app/data/
# at every container start, overriding any stale files a named volume may
# have deposited there (a known Hostinger deployment quirk).
RUN mkdir -p /app/data_bak && \
    find /app/data -maxdepth 1 -name "*.py" -exec cp {} /app/data_bak/ \; && \
    echo "Backed up $(ls /app/data_bak/*.py 2>/dev/null | wc -l) Python files to /app/data_bak/"

# Copy the process supervisor script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Expose Streamlit dashboard port
EXPOSE 8501

# Health check against Streamlit
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Use supervisor script: container exits if EITHER process dies (triggers Docker restart)
ENTRYPOINT ["/docker-entrypoint.sh"]
