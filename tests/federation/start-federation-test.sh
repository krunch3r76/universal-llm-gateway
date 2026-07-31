#!/usr/bin/env bash
# start-federation-test.sh - Launch Federation Test Environment
# 
# Architecture:
# - Docker: Two Gateway containers (local, jupiter) with Unix sockets
# - Host: Two Stargate instances (Master on :9999, Remote on :9990)
#
# Flow:
# 1. Create socket directories
# 2. Launch Docker containers (Gateway-local, Gateway-jupiter)
# 3. Wait for Unix sockets to appear
# 4. Launch Stargate instances (Master, Remote)
# 5. Wait for WebSocket connection
# 6. Ready for testing

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log_info "Federation Test Environment Setup"
log_info "Project root: $PROJECT_ROOT"

# Create directories
log_info "Creating socket and log directories..."
mkdir -p "$SCRIPT_DIR/sockets/local"
mkdir -p "$SCRIPT_DIR/sockets/jupiter"
mkdir -p "$SCRIPT_DIR/logs/local"
mkdir -p "$SCRIPT_DIR/logs/jupiter"
mkdir -p "/tmp/logs/stargate-master"
mkdir -p "/tmp/logs/stargate-jupiter"

# Cleanup any existing state
log_info "Cleaning up existing state..."
rm -f "$SCRIPT_DIR/sockets/local/gateway.sock"
rm -f "$SCRIPT_DIR/sockets/jupiter/gateway.sock"

# Stop any existing containers
if docker ps -q --filter "name=federation-gateway" | grep -q .; then
    log_warn "Stopping existing Gateway containers..."
    docker compose -f "$SCRIPT_DIR/docker-compose.yml" down
fi

# Stop any existing Stargate instances
if lsof -ti:9999 >/dev/null 2>&1; then
    log_warn "Port 9999 is in use, attempting to kill..."
    pkill -f "universal-stargate" || true
    sleep 2
fi

if lsof -ti:9990 >/dev/null 2>&1; then
    log_warn "Port 9990 is in use, attempting to kill..."
    pkill -f "universal-stargate" || true
    sleep 2
fi

# Load environment variables
log_info "Loading federation API keys..."
source "$SCRIPT_DIR/configs/federation.env"

# Build Docker images
log_info "Building Gateway Docker images..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" build

# Start Docker containers
log_info "Starting Gateway containers..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d

# Wait for Unix sockets
log_info "Waiting for Gateway Unix sockets..."
TIMEOUT=30
for i in $(seq 1 $TIMEOUT); do
    if [ -S "$SCRIPT_DIR/sockets/local/gateway.sock" ] && [ -S "$SCRIPT_DIR/sockets/jupiter/gateway.sock" ]; then
        log_success "Both Gateway sockets are ready"
        break
    fi
    
    if [ $i -eq $TIMEOUT ]; then
        log_error "Timeout waiting for Gateway sockets"
        log_error "Check Docker logs: docker compose -f $SCRIPT_DIR/docker-compose.yml logs"
        exit 1
    fi
    
    echo -n "."
    sleep 1
done
echo ""

# Start Remote Stargate (jupiter) first
log_info "Starting Remote Stargate (jupiter:9990)..."
cd "$PROJECT_ROOT/services/universal-stargate"
STARGATE_CONFIG="$SCRIPT_DIR/configs/remote-stargate.yaml" \
    python3 -m uvicorn src.main:app --host 127.0.0.1 --port 9990 \
    > "$SCRIPT_DIR/logs/stargate-jupiter.log" 2>&1 &
JUPITER_PID=$!
echo $JUPITER_PID > "$SCRIPT_DIR/pids/stargate-jupiter.pid"

# Wait for jupiter to start
log_info "Waiting for jupiter Stargate to start..."
for i in $(seq 1 30); do
    if curl -s http://localhost:9990/health >/dev/null 2>&1; then
        log_success "Jupiter Stargate is ready (PID: $JUPITER_PID)"
        break
    fi
    
    if [ $i -eq 30 ]; then
        log_error "Timeout waiting for jupiter Stargate"
        log_error "Check logs: $SCRIPT_DIR/logs/stargate-jupiter.log"
        exit 1
    fi
    
    sleep 1
done

# Start Master Stargate (localhost)
log_info "Starting Master Stargate (localhost:9999)..."
STARGATE_CONFIG="$SCRIPT_DIR/configs/master-stargate.yaml" \
    python3 -m uvicorn src.main:app --host 127.0.0.1 --port 9999 \
    > "$SCRIPT_DIR/logs/stargate-master.log" 2>&1 &
MASTER_PID=$!
echo $MASTER_PID > "$SCRIPT_DIR/pids/stargate-master.pid"

# Wait for master to start
log_info "Waiting for master Stargate to start..."
for i in $(seq 1 30); do
    if curl -s http://localhost:9999/health >/dev/null 2>&1; then
        log_success "Master Stargate is ready (PID: $MASTER_PID)"
        break
    fi
    
    if [ $i -eq 30 ]; then
        log_error "Timeout waiting for master Stargate"
        log_error "Check logs: $SCRIPT_DIR/logs/stargate-master.log"
        exit 1
    fi
    
    sleep 1
done

# Wait for WebSocket connection
log_info "Waiting for federation WebSocket connection..."
sleep 5

# Verify federation status
log_info "Checking federation health..."
if curl -s http://localhost:9999/health | grep -q "federation"; then
    log_success "Federation connection established!"
else
    log_warn "Federation health check incomplete (may be OK if not implemented yet)"
fi

# Display status
echo ""
log_success "=== Federation Test Environment Ready ==="
echo ""
echo "Master Stargate (localhost):  http://localhost:9999"
echo "Remote Stargate (jupiter):    http://localhost:9990"
echo ""
echo "Gateway Containers:"
echo "  - federation-gateway-local   (socket: $SCRIPT_DIR/sockets/local/gateway.sock)"
echo "  - federation-gateway-jupiter (socket: $SCRIPT_DIR/sockets/jupiter/gateway.sock)"
echo ""
echo "Logs:"
echo "  Master:  $SCRIPT_DIR/logs/stargate-master.log"
echo "  Jupiter: $SCRIPT_DIR/logs/stargate-jupiter.log"
echo "  Docker:  docker compose -f $SCRIPT_DIR/docker-compose.yml logs"
echo ""
echo "Next steps:"
echo "  1. Run tests: $SCRIPT_DIR/test-federation-routing.sh"
echo "  2. View logs: tail -f $SCRIPT_DIR/logs/stargate-*.log"
echo "  3. Stop: $SCRIPT_DIR/stop-federation-test.sh"
echo ""
