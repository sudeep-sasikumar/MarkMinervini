#!/bin/sh
# Process supervisor: start scanner + dashboard.
# If EITHER process dies, exit code 1 so Docker restarts the whole container.
#
# Uses POSIX kill -0 polling instead of bash's wait -n, so this works
# correctly under dash (the /bin/sh on python:3.11-slim / Debian slim images).

set -e

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
