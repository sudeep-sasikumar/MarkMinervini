#!/bin/sh
# Process supervisor: start scanner + dashboard.
# If EITHER process dies, exit with code 1 so Docker restarts the container.

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

# Wait for the first process to exit (success or failure)
wait -n $MAIN_PID $DASH_PID
EXIT_CODE=$?

# One process died — kill the other and exit so Docker can restart
echo "A process exited (code=$EXIT_CODE). Shutting down container for restart."
kill $MAIN_PID $DASH_PID 2>/dev/null || true
exit 1
