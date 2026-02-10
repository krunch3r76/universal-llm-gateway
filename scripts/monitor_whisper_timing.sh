#!/bin/bash
# Monitor Whisper streaming performance timing

echo "🔍 Monitoring Whisper Timing Metrics"
echo "====================================="
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Find the worker log for whisper-large-v3
WORKER_LOG="/tmp/llm_gateway/worker-logs/whisper-large-v3.log"
GATEWAY_LOG="/tmp/logs/universal-llm-gateway/gateway.log"

if [ ! -f "$WORKER_LOG" ]; then
    echo "⚠️  Worker log not found: $WORKER_LOG"
    echo "Falling back to Gateway logs..."
    tail -f "$GATEWAY_LOG" 2>/dev/null | grep --line-buffered "⏱️"
else
    echo "📊 Watching: $WORKER_LOG"
    echo ""
    tail -f "$WORKER_LOG" | grep --line-buffered "⏱️"
fi

