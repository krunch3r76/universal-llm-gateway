#!/usr/bin/env bash
# Check which models are loaded on each edge

echo "==================================================================="
echo "CHECKING MODEL DISTRIBUTION ACROSS EDGES"
echo "==================================================================="
echo

MODEL_ID="${1:-phi-3-5-mini-instruct-q8-0-16384}"

echo "Looking for model: $MODEL_ID"
echo

# Check localhost edge container
echo "--- Edge: localhost (Docker container) ---"
if docker ps --filter "name=edge-localhost" --format "{{.Names}}" | grep -q edge-localhost; then
    echo "Container: RUNNING"
    echo
    echo "Loaded models (from recent logs):"
    docker logs edge-localhost 2>&1 | grep -E "Loading model|Model loaded|Worker.*started|model_id.*${MODEL_ID}" | tail -20
    echo
    echo "Active worker sockets:"
    ls -la /tmp/universal-protocol/worker-*.sock 2>/dev/null | grep -E "worker.*${MODEL_ID}|worker" || echo "  (none found for $MODEL_ID)"
else
    echo "Container: NOT RUNNING"
fi

echo
echo "--- Edge: jupiter (Remote) ---"
REMOTE_HOST="${REMOTE_HOST:-user@remote-gpu-node}"
if ssh "$REMOTE_HOST" "docker ps --filter 'name=edge-jupiter' --format '{{.Names}}'" 2>/dev/null | grep -q edge-jupiter; then
    echo "Container: RUNNING"
    echo
    echo "Loaded models (from recent logs):"
    ssh "$REMOTE_HOST" "docker logs edge-jupiter 2>&1 | grep -E 'Loading model|Model loaded|Worker.*started|model_id.*${MODEL_ID}' | tail -20" 2>/dev/null
    echo
    echo "Active worker sockets:"
    ssh "$REMOTE_HOST" "ls -la /tmp/universal-protocol/worker-*.sock 2>/dev/null | grep -E 'worker.*${MODEL_ID}|worker' || echo '  (none found for $MODEL_ID)'" 2>/dev/null
else
    echo "Container: NOT RUNNING or UNREACHABLE"
fi

echo
echo "==================================================================="
echo "ANALYSIS"
echo "==================================================================="
echo
echo "If $MODEL_ID appears in logs on BOTH edges,"
echo "that confirms the sticky routing violation."
echo
echo "Worker sockets indicate which models are currently loaded."
