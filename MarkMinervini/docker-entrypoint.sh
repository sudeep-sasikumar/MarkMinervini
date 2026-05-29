#!/bin/sh
# Process supervisor: start scanner + dashboard.
# If EITHER process dies, exit code 1 so Docker restarts the whole container.
#
# Uses POSIX kill -0 polling instead of bash's wait -n, so this works
# correctly under dash (the /bin/sh on python:3.11-slim / Debian slim images).

set -e

# ---------------------------------------------------------------------------
# Self-healing source restore
# ---------------------------------------------------------------------------
# /app/data_bak/ is baked into the image (never volume-mounted) and always
# contains the latest Python source files from the build.  If a named volume
# is mounted at /app/data/ (e.g. by an old Hostinger docker-compose.yml that
# hasn't been updated), stale .py files in that volume would shadow the fresh
# code in the image.  Copying from the backup here — before any Python process
# starts — guarantees the container ALWAYS runs the current image's code,
# regardless of whatever the host's docker-compose.yml specifies.
if [ -d /app/data_bak ] && ls /app/data_bak/*.py >/dev/null 2>&1; then
    cp -f /app/data_bak/*.py /app/data/
    echo "Source restore: copied fresh data/*.py from image backup (/app/data_bak/)"
else
    echo "Source restore: /app/data_bak/ not found or empty — skipping"
fi

echo "Starting Minervini SEPA scanner..."
python main.py &
MAIN_PID=$!

echo "Starting Streamlit dashboard..."
streamlit run dashboard.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false &
DASH_PID=$!

echo "Both processes started. main.py PID=$MAIN_PID | dashboard PID=$DASH_PID"

# Poll every 5 seconds; exit as soon as either process dies.
while true; do
    if ! kill -0 "$MAIN_PID" 2>/dev/null; then
        echo "ERROR: Scanner process (PID $MAIN_PID) exited. Stopping dashboard and exiting."
        kill "$DASH_PID" 2>/dev/null || true
        exit 1
    fi

    if ! kill -0 "$DASH_PID" 2>/dev/null; then
        echo "ERROR: Dashboard process (PID $DASH_PID) exited. Stopping scanner and exiting."
        kill "$MAIN_PID" 2>/dev/null || true
        exit 1
    fi

    sleep 5
done
