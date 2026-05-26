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

# Application code
COPY . .

# Create data and logs directories for SQLite persistence
RUN mkdir -p /app/data /app/logs

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
