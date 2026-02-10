#!/usr/bin/env bash
# ============================================================================
# Start Universal Stargate Proxy
# ============================================================================
#
# Transport Modes:
#   TCP Mode (default): Uses STARGATE_HOST and STARGATE_PORT, connects to Gateway via TCP
#   Unix Socket: Set STARGATE_UNIX_SOCKET environment variable (enhanced security)
#
# Examples:
#   # TCP mode (default) - Stargate on TCP, connects to Gateway via TCP
#   ./start-stargate.sh debug
#
#   # Unix socket mode for Stargate only (Gateway still on TCP)
#   STARGATE_UNIX_SOCKET=/tmp/stargate.sock ./start-stargate.sh debug
#
#   # Full Unix socket deployment (both services on Unix sockets)
#   GATEWAY_UNIX_SOCKET=/tmp/gateway.sock STARGATE_UNIX_SOCKET=/tmp/stargate.sock ./start-stargate.sh debug
#
# Environment Variables:
#   STARGATE_UNIX_SOCKET        - Unix socket path for Stargate server (overrides TCP mode)
#   STARGATE_HOST               - TCP bind host for Stargate server (default: 0.0.0.0)
#   STARGATE_PORT               - TCP bind port for Stargate server (default: 9999)
#   STARGATE_ENABLE_TCP_MONITORING - Enable TCP monitoring on port 9997 (default: false)
#   GATEWAY_VENV                - Python venv path (default: $HOME/.venvs/universal)
#   LOG_LEVEL                   - Logging level (default: info)
#
# Note: Gateway connection mode is configured in config/gateways.yaml
#       Default: TCP (url: http://localhost:9998)
#       Unix socket: socket_path: /tmp/gateway.sock
#
# GUI Monitoring (Port 9997):
#   By default, GUI monitoring uses Unix socket only (/tmp/stargate_events.sock)
#   To enable TCP monitoring on port 9997, use one of:
#     ./start-stargate.sh debug --enable-tcp-monitoring
#     STARGATE_ENABLE_TCP_MONITORING=true ./start-stargate.sh debug
#   Note: TCP monitoring is automatically disabled when STARGATE_UNIX_SOCKET is set
#
# ============================================================================
# Universal Stargate - Service Wrapper
# Simple wrapper around the Python service manager

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/stargate_service_manager.py"

# Parse arguments
ENVIRONMENT="default"
ENABLE_TCP_MONITORING=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        "default"|"debug"|"release")
            ENVIRONMENT="$1"
            shift
            ;;
        "--enable-tcp-monitoring")
            ENABLE_TCP_MONITORING=true
            shift
            ;;
        "--help"|"-h")
            echo "Usage: $0 [environment] [options]"
            echo ""
            echo "Environments:"
            echo "  default   - Default configuration"
            echo "  debug     - Debug mode with verbose logging"
            echo "  release   - Production mode"
            echo ""
            echo "Options:"
            echo "  --enable-tcp-monitoring   Enable TCP monitoring on port 9997 (default: Unix socket only)"
            echo "  --help, -h                Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 debug"
            echo "  $0 debug --enable-tcp-monitoring"
            echo "  $0 --enable-tcp-monitoring debug"
            echo ""
            exit 0
            ;;
        *)
            echo "Error: Unknown argument '$1'" >&2
            echo "Usage: $0 [environment] [--enable-tcp-monitoring]" >&2
            echo "       $0 --help" >&2
            exit 1
            ;;
    esac
done

# Export TCP monitoring flag if enabled
if [ "$ENABLE_TCP_MONITORING" = true ]; then
    export STARGATE_ENABLE_TCP_MONITORING=true
    echo "TCP monitoring on port 9997 will be enabled"
fi

# Make script executable if not already
if [[ ! -x "$PYTHON_SCRIPT" ]]; then
    chmod +x "$PYTHON_SCRIPT"
fi

# Load environment files from project root
# Script is at services/universal-stargate/scripts/, so go up 3 levels
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
ENV_FILE="$PROJECT_ROOT/config/env/stargate.env"
ENV_LOCAL_FILE="$PROJECT_ROOT/config/env/stargate.env.local"

# Load base environment file
if [[ -f "$ENV_FILE" ]]; then
    echo "Loading environment from: $ENV_FILE"
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "Warning: Base environment file not found: $ENV_FILE"
fi

# Load local overrides
if [[ -f "$ENV_LOCAL_FILE" ]]; then
    echo "Loading local overrides from: $ENV_LOCAL_FILE"
    set -a
    source "$ENV_LOCAL_FILE"
    set +a
fi

# Resolve STARGATE_CONFIG to absolute path if it's relative
# This allows: export STARGATE_CONFIG="config/stargate_config.io.yaml"
# from project root before running start-stargate.sh
if [[ -n "${STARGATE_CONFIG:-}" ]]; then
    # Check if it's not already an absolute path
    if [[ "${STARGATE_CONFIG:0:1}" != "/" ]]; then
        # Resolve relative to project root
        STARGATE_CONFIG="$PROJECT_ROOT/$STARGATE_CONFIG"
        export STARGATE_CONFIG
        echo "Resolved STARGATE_CONFIG to: $STARGATE_CONFIG"
    else
        echo "Using STARGATE_CONFIG: $STARGATE_CONFIG"
    fi
fi

echo ""
echo "Starting Universal Stargate..."
echo "Environment: $ENVIRONMENT"
echo ""

# Use venv if available, fall back to system python
GATEWAY_VENV="${GATEWAY_VENV:-$HOME/.venvs/universal}"
if [[ -f "$GATEWAY_VENV/bin/python" ]]; then
    PYTHON_BIN="$GATEWAY_VENV/bin/python"
    echo "Using venv: $GATEWAY_VENV"
else
    PYTHON_BIN="python3"
    echo "Warning: venv not found at $GATEWAY_VENV, using system python3"
fi

# Set PYTHONPATH to include libs/ for sitecustomize.py
export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}"

# Execute Python service manager (no longer needs to load env files)
exec "$PYTHON_BIN" "$PYTHON_SCRIPT" --environment="$ENVIRONMENT"
