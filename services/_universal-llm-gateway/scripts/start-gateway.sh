#!/usr/bin/env bash
# ============================================================================
# Start Universal LLM Gateway
# ============================================================================
#
# Transport Modes:
#   Unix Socket (recommended): Set GATEWAY_UNIX_SOCKET environment variable
#   TCP Mode: Uses GATEWAY_HOST and GATEWAY_PORT (default)
#
# Examples:
#   # Unix socket mode (no TCP port exposure)
#   GATEWAY_UNIX_SOCKET=/tmp/gateway.sock ./start-gateway.sh debug
#
#   # TCP mode (default)
#   ./start-gateway.sh debug
#
#   # TCP mode with custom port
#   GATEWAY_PORT=8080 ./start-gateway.sh debug
#
# Environment Variables:
#   GATEWAY_UNIX_SOCKET - Unix socket path (overrides TCP mode)
#   GATEWAY_HOST        - TCP bind host (default: 0.0.0.0)
#   GATEWAY_PORT        - TCP bind port (default: 9998)
#   GATEWAY_VENV        - Python venv path (default: $HOME/.venvs/universal)
#   LOG_LEVEL           - Logging level (default: info)
#
# ============================================================================
#
# This is a simple wrapper around the Python service manager for backward compatibility
# and easy usage. It detects the environment and calls the appropriate Python script.


set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/gateway_service_manager.py"

# Handle help and version flags directly
if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
    echo "Universal LLM Gateway - Service Wrapper"
    echo ""
    echo "Usage: $0 [ENVIRONMENT]"
    echo ""
    echo "Environments:"
    echo "  default    Use base configuration only"
    echo "  debug      Debug environment (verbose logging, dev features enabled)"
    echo "  release    Release environment (production-optimized settings)"
    echo ""
    echo "Environment Variables:"
    echo "  FAST_SHUTDOWN=true         Enable fast shutdown (immediate termination on ctrl-c)"
    echo "  GATEWAY_UNIX_SOCKET=path   Use Unix socket instead of TCP (overrides host/port)"
    echo ""
    echo "Examples:"
    echo "  $0          # Start with default environment"
    echo "  $0 debug    # Start with debug settings (development)"
    echo "  $0 release  # Start with release settings (production)"
    echo "  FAST_SHUTDOWN=true $0 debug  # Debug mode with fast shutdown"
    echo "  GATEWAY_UNIX_SOCKET=/tmp/gateway.sock $0 debug  # Unix socket mode"
    echo ""
    echo "For advanced options, use the Python script directly:"
    echo "  python3 scripts/gateway_service_manager.py --help"
    exit 0
fi

# Default environment
ENVIRONMENT="${1:-default}"

# Validate environment
case "$ENVIRONMENT" in
    default|debug|release)
        # Valid environment
        ;;
    *)
        echo "Error: Invalid environment '$ENVIRONMENT'"
        echo "Valid environments: default, debug, release"
        echo "Use '$0 --help' for more information"
        exit 1
        ;;
esac

# Check if Python script exists
if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    echo "Error: Python service manager not found: $PYTHON_SCRIPT"
    echo "Please ensure the Python script is installed correctly."
    exit 1
fi

# Check if Python script is executable
if [[ ! -x "$PYTHON_SCRIPT" ]]; then
    echo "Making Python script executable..."
    chmod +x "$PYTHON_SCRIPT"
fi

# Check for required Python dependencies
if ! python3 -c "import psutil" 2>/dev/null; then
    echo "Error: psutil not available. Install with: pip install psutil"
    exit 1
fi

# Load environment from project root .env.local (canonical location)
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
ENV_LOCAL="$PROJECT_ROOT/.env.local"

if [[ -f "$ENV_LOCAL" ]]; then
    echo "Loading environment from: $ENV_LOCAL"
    set -a
    source "$ENV_LOCAL"
    set +a
fi

echo ""
echo "Starting Universal LLM Gateway..."
echo "Environment: $ENVIRONMENT"
echo "Using Python service manager: $PYTHON_SCRIPT"
echo ""

# Execute the Python service manager (no longer needs to load env files)
exec "$PYTHON_SCRIPT" --environment="$ENVIRONMENT"
