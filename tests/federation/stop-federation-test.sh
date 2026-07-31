#!/usr/bin/env bash
# stop-federation-test.sh - Stop Federation Test Environment

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log_info "Stopping Federation Test Environment..."

# Stop Stargate instances
if [ -f "$SCRIPT_DIR/pids/stargate-master.pid" ]; then
    MASTER_PID=$(cat "$SCRIPT_DIR/pids/stargate-master.pid")
    if kill -0 "$MASTER_PID" 2>/dev/null; then
        log_info "Stopping Master Stargate (PID: $MASTER_PID)..."
        kill "$MASTER_PID"
    fi
    rm -f "$SCRIPT_DIR/pids/stargate-master.pid"
fi

if [ -f "$SCRIPT_DIR/pids/stargate-jupiter.pid" ]; then
    JUPITER_PID=$(cat "$SCRIPT_DIR/pids/stargate-jupiter.pid")
    if kill -0 "$JUPITER_PID" 2>/dev/null; then
        log_info "Stopping Jupiter Stargate (PID: $JUPITER_PID)..."
        kill "$JUPITER_PID"
    fi
    rm -f "$SCRIPT_DIR/pids/stargate-jupiter.pid"
fi

# Stop Docker containers
log_info "Stopping Gateway containers..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" down

# Cleanup sockets
log_info "Cleaning up Unix sockets..."
rm -f "$SCRIPT_DIR/sockets/local/gateway.sock"
rm -f "$SCRIPT_DIR/sockets/jupiter/gateway.sock"

# Cleanup any remaining processes
pkill -f "universal-stargate.*9999" || true
pkill -f "universal-stargate.*9990" || true

log_success "Federation Test Environment stopped"
