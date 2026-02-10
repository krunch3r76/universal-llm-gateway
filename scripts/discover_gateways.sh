#!/bin/bash
#
# Discover all running Gateway instances (local and federated).
#
# Checks common Gateway ports and Unix sockets to find active instances.

set -euo pipefail

STARGATE_URL="${1:-http://localhost:9999}"

echo "=========================================="
echo "Gateway Discovery"
echo "=========================================="
echo ""

GATEWAYS=()

# Check localhost gateway (standard port)
echo "Checking localhost:9998..."
if curl -s -f "http://localhost:9998/health" >/dev/null 2>&1; then
    echo "  ✓ Found: http://localhost:9998"
    GATEWAYS+=("http://localhost:9998")
else
    echo "  ✗ Not responding"
fi

# Check alternative ports (for multi-instance)
for port in 9997 9996 9995; do
    echo "Checking localhost:${port}..."
    if curl -s -f "http://localhost:${port}/health" >/dev/null 2>&1; then
        echo "  ✓ Found: http://localhost:${port}"
        GATEWAYS+=("http://localhost:${port}")
    else
        echo "  ✗ Not responding"
    fi
done

# Check for Unix socket gateways
echo ""
echo "Checking Unix sockets..."
for socket in /tmp/universal-protocol/*.sock /sockets/*.sock; do
    if [ -S "$socket" ] 2>/dev/null; then
        echo "  Found socket: $socket"
        # Note: Cannot easily curl Unix sockets, just list them
    fi
done

# Try to get federated gateways from Stargate
echo ""
echo "Querying Stargate for federated gateways..."
FEDERATION_INFO=$(curl -s "${STARGATE_URL}/api/v1/federation/status" 2>/dev/null || echo '{}')

if echo "$FEDERATION_INFO" | jq -e '.remotes' >/dev/null 2>&1; then
    echo "  Federated mode detected!"
    
    # Extract remote gateway URLs if available
    REMOTE_COUNT=$(echo "$FEDERATION_INFO" | jq -r '.remotes | length' 2>/dev/null || echo "0")
    if [ "$REMOTE_COUNT" -gt 0 ]; then
        echo "  Found $REMOTE_COUNT federated remote(s)"
    fi
else
    echo "  Standalone mode or federation status unavailable"
fi

echo ""
echo "=========================================="
echo "SUMMARY"
echo "=========================================="
echo ""

if [ ${#GATEWAYS[@]} -eq 0 ]; then
    echo "❌ No gateways found"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Check if Gateway is running:"
    echo "     lsof -i:9998"
    echo "  2. Check Gateway logs:"
    echo "     ls -la /tmp/logs/universal-llm-gateway/"
    echo "  3. Try starting Gateway:"
    echo "     ./scripts/start-gateway.sh"
    exit 1
else
    echo "✓ Found ${#GATEWAYS[@]} HTTP gateway(s):"
    for gw in "${GATEWAYS[@]}"; do
        echo "  - $gw"
    done
    echo ""
    echo "To check model distribution across these gateways:"
    echo "  ./scripts/check_gateway_model_distribution.py MODEL_ID ${GATEWAYS[*]}"
    echo ""
    echo "Example:"
    echo "  ./scripts/check_gateway_model_distribution.py phi-3-5-mini-instruct-q8-0-16384 ${GATEWAYS[*]}"
fi
