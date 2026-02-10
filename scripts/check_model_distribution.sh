#!/bin/bash
#
# Check which gateways have a specific model loaded.
#
# Usage: ./scripts/check_model_distribution.sh MODEL_ID [STARGATE_URL]
#
# This helps diagnose sticky routing violations where a sticky model
# is incorrectly loaded on multiple gateways.

set -euo pipefail

MODEL_ID="${1:-}"
STARGATE_URL="${2:-http://localhost:9999}"

if [ -z "$MODEL_ID" ]; then
    echo "Usage: $0 MODEL_ID [STARGATE_URL]"
    echo ""
    echo "Example:"
    echo "  $0 phi-3-5-mini-instruct-q8-0-16384"
    echo ""
    exit 1
fi

echo "=========================================="
echo "Model Distribution Check"
echo "=========================================="
echo ""
echo "Model: $MODEL_ID"
echo "Stargate: $STARGATE_URL"
echo ""

# Check if model is available
echo "Checking model availability..."
MODELS_RESPONSE=$(curl -s "${STARGATE_URL}/v1/models" 2>/dev/null || echo '{"error": "failed"}')

if echo "$MODELS_RESPONSE" | jq -e '.error' >/dev/null 2>&1; then
    echo "❌ Failed to connect to Stargate at $STARGATE_URL"
    echo "   Make sure Stargate is running"
    exit 1
fi

MODEL_FOUND=$(echo "$MODELS_RESPONSE" | jq -r --arg model "$MODEL_ID" '.data[] | select(.id == $model) | .id' 2>/dev/null || echo "")

if [ -z "$MODEL_FOUND" ]; then
    echo "❌ Model '$MODEL_ID' not found in available models"
    echo ""
    echo "Available models:"
    echo "$MODELS_RESPONSE" | jq -r '.data[].id' | sed 's/^/  - /'
    exit 1
fi

echo "✓ Model found in available models"
echo ""

# Try to determine which gateway(s) have the model by making test requests
echo "Attempting to detect gateway distribution..."
echo "(Making 10 concurrent requests and checking X-Gateway-ID header)"
echo ""

TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# Make 10 concurrent requests and capture headers
for i in {1..10}; do
    (
        curl -s -D "${TEMP_DIR}/headers_${i}.txt" \
            -X POST "${STARGATE_URL}/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d "{
                \"model\": \"${MODEL_ID}\",
                \"messages\": [{\"role\": \"user\", \"content\": \"Hi\"}],
                \"max_tokens\": 5
            }" > "${TEMP_DIR}/response_${i}.json" 2>&1
    ) &
done

wait

# Extract gateway IDs from responses
GATEWAY_IDS=()
for i in {1..10}; do
    if [ -f "${TEMP_DIR}/headers_${i}.txt" ]; then
        GW_ID=$(grep -i "^X-Gateway-ID:" "${TEMP_DIR}/headers_${i}.txt" 2>/dev/null | cut -d: -f2- | tr -d ' \r' || echo "")
        if [ -z "$GW_ID" ]; then
            # Try X-Worker-ID
            GW_ID=$(grep -i "^X-Worker-ID:" "${TEMP_DIR}/headers_${i}.txt" 2>/dev/null | cut -d: -f2- | tr -d ' \r' || echo "")
        fi
        if [ -z "$GW_ID" ] && [ -f "${TEMP_DIR}/response_${i}.json" ]; then
            # Try system_fingerprint from response body
            GW_ID=$(jq -r '.system_fingerprint // empty' "${TEMP_DIR}/response_${i}.json" 2>/dev/null || echo "")
        fi
        if [ -n "$GW_ID" ]; then
            GATEWAY_IDS+=("$GW_ID")
        fi
    fi
done

if [ ${#GATEWAY_IDS[@]} -eq 0 ]; then
    echo "⚠️  Could not determine gateway IDs from responses"
    echo "   Gateway information not available in response headers/body"
    echo ""
    echo "Manual check recommended:"
    echo "  1. Check Gateway logs: /tmp/logs/universal-llm-gateway/"
    echo "  2. List loaded models on each Gateway instance"
    echo ""
    exit 0
fi

# Count unique gateways
UNIQUE_GATEWAYS=($(printf '%s\n' "${GATEWAY_IDS[@]}" | sort -u))
NUM_GATEWAYS=${#UNIQUE_GATEWAYS[@]}

echo "Gateway distribution:"
for gw in "${UNIQUE_GATEWAYS[@]}"; do
    count=$(printf '%s\n' "${GATEWAY_IDS[@]}" | grep -c "^${gw}$" || echo 0)
    echo "  $gw: $count requests"
done
echo ""

if [ "$NUM_GATEWAYS" -eq 1 ]; then
    echo "✅ GOOD: All requests went to single gateway"
    echo ""
    echo "This is correct for sticky models (default behavior)."
    echo "Batching should work effectively."
elif [ "$NUM_GATEWAYS" -gt 1 ]; then
    echo "❌ STICKY ROUTING VIOLATION DETECTED"
    echo ""
    echo "Requests distributed across $NUM_GATEWAYS gateways."
    echo "For sticky models, this prevents effective batching."
    echo ""
    echo "To fix:"
    echo "  1. Stop services:"
    echo "     pkill -f 'universal-'; rm -f /tmp/universal-protocol/*.sock /tmp/process_ipc/*.sock"
    echo ""
    echo "  2. Verify config has model_routing.default_sticky: true"
    echo "     grep -A2 'model_routing:' config/stargate_*.yaml"
    echo ""
    echo "  3. If running multiple Gateway instances, ensure only ONE has capacity"
    echo "     for this model (e.g., by device/VRAM constraints)"
    echo ""
    echo "  4. Restart services with corrected configuration"
    echo ""
    exit 1
fi
