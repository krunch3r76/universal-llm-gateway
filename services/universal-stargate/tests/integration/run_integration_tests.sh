#!/bin/bash
# Run integration tests for real-time model switching

set -e

echo "🚀 Real-Time Resource Orchestration Integration Tests"
echo "=================================================="

# Check if services are running
if ! curl -s http://localhost:9998/health > /dev/null; then
    echo "❌ Gateway not running on port 9998"
    echo "Please start: cd /mnt/torus/projects/universal-llm-gateway/services/_universal-llm-gateway && ./scripts/start-gateway.sh release"
    exit 1
fi

if ! curl -s http://localhost:9999/health > /dev/null; then
    echo "❌ Stargate not running on port 9999"
    echo "Please start: cd /mnt/torus/projects/universal-llm-gateway/services/universal-stargate && ./scripts/start-stargate.sh release"
    exit 1
fi

echo "✅ Services are running"
echo

# Run the integration test
cd /mnt/torus/projects/universal-llm-gateway
export PYTHONPATH=/mnt/torus/projects/universal-llm-gateway/libs:$PYTHONPATH

python services/universal-stargate/tests/integration/test_realtime_model_switching.py

