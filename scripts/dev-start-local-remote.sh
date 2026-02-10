#!/bin/bash
# Quick start: Local development with uniform architecture
# Architecture: Master Stargate (host) → Remote Stargate (container) → Gateway (container)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default values
FEDERATION_API_KEY="${FEDERATION_API_KEY:-dev-key-not-for-production}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
REMOTE_ID="${REMOTE_ID:-local-remote}"
MASTER_PID_FILE="/tmp/universal-stargate-dev-localhost-only.pid"

# Cleanup on failure
cleanup_on_failure() {
    if [ $? -ne 0 ]; then
        echo ""
        echo "❌ Startup failed, cleaning up..."
        cd "$PROJECT_ROOT/docker"
        docker compose -f compose/dev-local.yml down 2>/dev/null
        [ -f "$MASTER_PID_FILE" ] && rm -f "$MASTER_PID_FILE"
    fi
}
trap cleanup_on_failure EXIT

# Check if ports are available
check_port_available() {
    ! lsof -i:$1 >/dev/null 2>&1
}

echo "=== Universal LLM Gateway - Local Remote Development ==="
echo "Architecture: Master Stargate (host) → Remote Stargate (container) → Gateway (container)"
echo ""

# 0. Pre-flight checks
echo "Pre-flight checks..."
if ! check_port_available 9999; then
    echo "❌ Port 9999 already in use (Master Stargate)"
    exit 1
fi
if ! check_port_available 10999; then
    echo "❌ Port 10999 already in use (Remote Stargate)"
    exit 1
fi
echo "✓ Ports 9999 and 10999 are available"

# 1. Stop any existing services
echo ""
echo "Stopping existing services..."
cd "$PROJECT_ROOT/docker"
docker compose -f compose/dev-local.yml down 2>&1 | grep -v "no configuration file provided" || true

# Stop Master Stargate via PID file
if [ -f "$MASTER_PID_FILE" ]; then
    if kill $(cat "$MASTER_PID_FILE") 2>/dev/null; then
        echo "  Stopped Master Stargate (PID $(cat "$MASTER_PID_FILE"))"
    fi
    rm -f "$MASTER_PID_FILE"
else
    echo "  (no Master Stargate PID file found)"
fi

# 2. Start Docker containers (Remote Stargate + Gateway)
echo ""
echo "Starting Docker containers (Remote Stargate + Gateway)..."
export FEDERATION_API_KEY
export REMOTE_ID
export LOG_LEVEL
docker compose -f compose/dev-local.yml up -d

# 3. Wait for containers to be healthy
echo ""
echo "Waiting for containers to be healthy..."
check_container_health() {
    local gateway_health=$(docker inspect --format='{{.State.Health.Status}}' local-gateway 2>/dev/null)
    local remote_health=$(docker inspect --format='{{.State.Health.Status}}' local-remote-stargate 2>/dev/null)
    [ "$gateway_health" = "healthy" ] && [ "$remote_health" = "healthy" ]
}

for i in {1..30}; do
    if check_container_health; then
        echo "✓ Both containers are healthy!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "⚠ Timeout waiting for health checks after 60 seconds"
        exit 1
    fi
    echo "⏳ Waiting for health checks... (attempt $i/30)"
    sleep 2
done

# 4. Start Master Stargate (host process)
echo ""
echo "Starting Master Stargate (host process)..."
cd "$PROJECT_ROOT"
STARGATE_CONFIG=config/stargate_config.dev-localhost-only.yaml \
FEDERATION_API_KEY="$FEDERATION_API_KEY" \
LOG_LEVEL="$LOG_LEVEL" \
./services/universal-stargate/scripts/start-stargate.sh debug > /tmp/logs/master-stargate-startup.log 2>&1 &

MASTER_PID=$!
echo "$MASTER_PID" > "$MASTER_PID_FILE"
echo "Master Stargate PID: $MASTER_PID (saved to $MASTER_PID_FILE)"

# 5. Wait for Master to start
echo ""
echo "Waiting for Master Stargate to start..."
sleep 5

# 6. Health checks
echo ""
echo "=== Health Check ==="
echo "Master Stargate (host:9999):"
curl -s http://localhost:9999/health | jq . || echo "  ❌ Not ready"

echo ""
echo "Remote Stargate (container:10999):"
curl -s http://localhost:10999/health | jq . || echo "  ❌ Not ready"

# 7. Show status
echo ""
echo "=== Ready ==="
echo "Master Stargate:  http://localhost:9999 (host process, PID $MASTER_PID)"
echo "Remote Stargate:  http://localhost:10999 (container)"
echo "Gateway:          Network isolated (via Remote Stargate only)"
echo ""
echo "Logs:"
echo "  Master:  tail -f /tmp/logs/universal-stargate-master/*.log"
echo "  Remote:  docker compose -f docker/compose/dev-local.yml logs -f remote-stargate"
echo "  Gateway: docker compose -f docker/compose/dev-local.yml logs -f gateway"
echo ""
echo "Test inference:"
echo "  curl -X POST http://localhost:9999/v1/chat/completions \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"model\": \"your-model\", \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}]}'"
echo ""
echo "Stop services:"
echo "  kill \$(cat $MASTER_PID_FILE)  # Stop Master"
echo "  docker compose -f docker/compose/dev-local.yml down  # Stop containers"

# Disable trap on successful completion
trap - EXIT
