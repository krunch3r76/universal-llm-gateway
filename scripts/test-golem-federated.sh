#!/usr/bin/env bash
# Test script for Golem federated deployment
# Manages Docker containers and local Master Stargate

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
COMPOSE_FILE="docker/compose/golem-federated-test.yml"
ENV_FILE=".env.golem-federated-test"
MASTER_CONFIG="config/stargate_config.golem-master.yaml"
MASTER_PID_FILE="/tmp/universal-stargate-golem-master.pid"
MOUNT_DIR="tmp/golem-testing-mounts"

# Track background processes
BACKGROUND_PIDS=()

# Cleanup function
cleanup() {
    log "Caught interrupt signal, cleaning up..."
    
    # Kill all tracked background processes first (including Master)
    for pid in "${BACKGROUND_PIDS[@]}"; do
        if ps -p "$pid" > /dev/null 2>&1; then
            log "Killing background process $pid..."
            pkill -TERM -P "$pid" 2>/dev/null || true  # Kill children
            kill -TERM "$pid" 2>/dev/null || true       # Kill parent
            sleep 1
            # Force kill if still alive
            if ps -p "$pid" > /dev/null 2>&1; then
                pkill -9 -P "$pid" 2>/dev/null || true
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
    done
    
    # Clean up PID file
    rm -f "$MASTER_PID_FILE"
    
    # Optionally stop Docker nodes on Ctrl-C
    stop_nodes
    
    exit 130
}

# Setup signal handlers
trap cleanup SIGINT SIGTERM

log() {
    echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

compose() {
    # Run docker compose with the federated test env file (if present).
    #
    # This prevents variable-substitution warnings like:
    #   "FEDERATION_KEY_REMOTE_1 variable is not set. Defaulting to a blank string."
    if [[ -f "$ENV_FILE" ]]; then
        docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
    else
        docker compose -f "$COMPOSE_FILE" "$@"
    fi
}

require_env_var() {
    local var_name="$1"
    local val="${!var_name:-}"
    if [[ -z "$val" ]]; then
        error "Required env var is missing/empty: $var_name"
        error "Fix: set it in $ENV_FILE (or export it in your shell) then re-run."
        exit 1
    fi
}

check_prerequisites() {
    log "Checking prerequisites..."
    
    # Check for environment file
    if [[ ! -f "$ENV_FILE" ]]; then
        error "Environment file not found: $ENV_FILE"
        error "Create it first or source the template"
        exit 1
    fi
    
    # Check for tarball
    if [[ ! -f "tmp/app-code.tar.gz" ]]; then
        error "Application tarball not found: tmp/app-code.tar.gz"
        error "Build it first: ./scripts/build_golem_tarball.sh"
        exit 1
    fi
    
    # Check for base image
    if ! docker image inspect universal-llm-gateway:golem-base >/dev/null 2>&1; then
        error "Base image not found: universal-llm-gateway:golem-base"
        error "Build it first: docker build -f docker/dockerfiles/Dockerfile.golem -t universal-llm-gateway:golem-base ."
        exit 1
    fi
    
    # Create mount directories
    log "Creating mount directories..."
    mkdir -p "$MOUNT_DIR"/{node-1,node-2}/{work,models,logs,output}
    
    success "All prerequisites met"
}

start_nodes() {
    log "Starting Golem nodes..."
    
    # Load and export environment
    set -a
    source "$ENV_FILE"
    set +a

    # Fail-fast: federation keys MUST be set (no blank auth).
    require_env_var "FEDERATION_KEY_REMOTE_1"
    require_env_var "FEDERATION_KEY_REMOTE_2"
    
    # Start containers
    compose up -d
    
    # Wait for health checks
    log "Waiting for nodes to be healthy..."
    local max_wait=120
    local waited=0
    
    while [[ $waited -lt $max_wait ]]; do
        if compose ps | grep -q "healthy"; then
            local node1_healthy=$(docker inspect --format='{{.State.Health.Status}}' golem-node-1 2>/dev/null || echo "none")
            local node2_healthy=$(docker inspect --format='{{.State.Health.Status}}' golem-node-2 2>/dev/null || echo "none")
            
            if [[ "$node1_healthy" == "healthy" ]] && [[ "$node2_healthy" == "healthy" ]]; then
                success "Both nodes are healthy"
                break
            fi
        fi
        
        sleep 2
        waited=$((waited + 2))
    done
    
    if [[ $waited -ge $max_wait ]]; then
        error "Nodes did not become healthy in time"
        compose logs --tail=50
        exit 1
    fi
    
    success "Golem nodes started"
    compose ps
}

start_master() {
    log "Starting Master Stargate..."
    
    # Check if already running
    if [[ -f "$MASTER_PID_FILE" ]]; then
        local pid=$(cat "$MASTER_PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            warn "Master Stargate already running (PID: $pid)"
            return 0
        else
            rm -f "$MASTER_PID_FILE"
        fi
    fi
    
    # Load and export environment
    set -a
    source "$ENV_FILE"
    set +a

    # Fail-fast: Master remotes require keys.
    require_env_var "FEDERATION_KEY_REMOTE_1"
    require_env_var "FEDERATION_KEY_REMOTE_2"
    
    # Export config for the Master Stargate
    export STARGATE_CONFIG="$MASTER_CONFIG"
    
    # Start Master Stargate (no setsid - we want signal propagation)
    ./services/universal-stargate/scripts/start-stargate.sh debug &
    
    local master_pid=$!
    echo "$master_pid" > "$MASTER_PID_FILE"
    BACKGROUND_PIDS+=("$master_pid")
    
    # Forward SIGINT/SIGTERM to master process
    trap "kill $master_pid 2>/dev/null || true; cleanup" SIGINT SIGTERM
    
    # Wait for Master to be ready
    log "Waiting for Master Stargate to be ready..."
    local max_wait=30
    local waited=0
    
    while [[ $waited -lt $max_wait ]]; do
        if curl -sf http://localhost:9999/health > /dev/null 2>&1; then
            success "Master Stargate is ready (PID: $master_pid)"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    
    error "Master Stargate did not start in time"
    stop_master
    exit 1
}

stop_nodes() {
    log "Stopping Golem nodes..."
    compose down
    success "Golem nodes stopped"
}

stop_master() {
    log "Stopping Master Stargate..."
    
    if [[ -f "$MASTER_PID_FILE" ]]; then
        local pid=$(cat "$MASTER_PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            log "Terminating Master Stargate (PID: $pid) and its children..."
            
            # First, gracefully terminate all child processes
            pkill -TERM -P "$pid" 2>/dev/null || true
            
            # Then terminate the parent
            kill -TERM "$pid" 2>/dev/null || true
            
            # Wait up to 5 seconds for graceful shutdown
            local waited=0
            while ps -p "$pid" > /dev/null 2>&1 && [[ $waited -lt 5 ]]; do
                sleep 1
                waited=$((waited + 1))
            done
            
            # Force kill if still running
            if ps -p "$pid" > /dev/null 2>&1; then
                log "Force killing (SIGKILL)..."
                pkill -9 -P "$pid" 2>/dev/null || true
                kill -9 "$pid" 2>/dev/null || true
                sleep 1
            fi
        fi
        rm -f "$MASTER_PID_FILE"
    fi
    
    # Fallback: kill by name (catches any orphaned processes)
    pkill -f "stargate_service_manager.*debug" || true
    pkill -f "uvicorn.*stargate" || true
    
    success "Master Stargate stopped"
}

stop_all() {
    stop_master
    stop_nodes
}

clean() {
    log "Cleaning up test data..."
    
    # Stop everything first
    stop_all
    
    # Remove mount directories
    if [[ -d "$MOUNT_DIR" ]]; then
        log "Removing mount directory: $MOUNT_DIR"
        rm -rf "$MOUNT_DIR"
        success "Mount directory removed"
    else
        log "No mount directory to clean"
    fi
    
    # Remove old Docker volumes (if any exist from previous setup)
    local volumes=$(docker volume ls -q | grep -E "golem-federated-test_(node-[12]-(work|models|logs|output))" || true)
    if [[ -n "$volumes" ]]; then
        log "Removing old Docker volumes..."
        echo "$volumes" | xargs -r docker volume rm
        success "Old Docker volumes removed"
    fi
}

status() {
    log "Checking status..."
    
    echo ""
    echo "=== Docker Nodes ==="
    if compose ps 2>/dev/null; then
        compose ps
    else
        echo "No containers running"
    fi
    
    echo ""
    echo "=== Master Stargate ==="
    if [[ -f "$MASTER_PID_FILE" ]]; then
        local pid=$(cat "$MASTER_PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "Running (PID: $pid)"
            if curl -sf http://localhost:9999/health > /dev/null 2>&1; then
                echo "Health: ✓ healthy"
            else
                echo "Health: ✗ unhealthy"
            fi
        else
            echo "Not running (stale PID file)"
        fi
    else
        echo "Not running"
    fi
    
    echo ""
    echo "=== Health Endpoints ==="
    echo -n "Master (9999): "
    if curl -sf http://localhost:9999/health > /dev/null 2>&1; then
        echo "✓"
    else
        echo "✗"
    fi
    
    echo -n "Node 1 (10999): "
    if curl -sf http://localhost:10999/health > /dev/null 2>&1; then
        echo "✓"
    else
        echo "✗"
    fi
    
    echo -n "Node 2 (11999): "
    if curl -sf http://localhost:11999/health > /dev/null 2>&1; then
        echo "✓"
    else
        echo "✗"
    fi
}

test_inference() {
    log "Testing inference through Master..."
    
    local model="${1:-hermes3-8b-8192}"
    
    curl -X POST http://localhost:9999/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d "{
            \"model\": \"$model\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Hello, test\"}],
            \"max_tokens\": 50,
            \"stream\": false
        }" | jq .
}

logs() {
    local service="${1:-}"
    
    if [[ -z "$service" ]]; then
        # Follow all Docker logs with cleanup on Ctrl+C
        compose logs -f
    elif [[ "$service" == "master" ]]; then
        # Follow master logs with cleanup on Ctrl+C
        tail -f /tmp/logs/universal-stargate-master/stargate.log
    else
        # Follow specific service logs with cleanup on Ctrl+C
        compose logs -f "$service"
    fi
}

usage() {
    cat << EOF
Usage: $0 <command> [options]

Commands:
    start          Start both nodes and Master Stargate
    start-nodes    Start only Golem nodes (Docker containers)
    start-master   Start only Master Stargate (local process)
    stop           Stop everything
    stop-nodes     Stop only Golem nodes
    stop-master    Stop only Master Stargate
    restart        Restart everything
    status         Show status of all components
    test [model]   Test inference (default: hermes3-8b-8192)
    logs [service] Show logs (service: golem-node-1, golem-node-2, master, or all)
    check          Check prerequisites
    clean          Stop everything and remove all test data

Examples:
    $0 start                    # Start everything
    $0 status                   # Check status
    $0 test hermes3-8b-8192     # Test inference
    $0 logs golem-node-1        # Show Node 1 logs
    $0 stop                     # Stop everything
    $0 clean                    # Stop and clean up all test data
EOF
}

main() {
    local command="${1:-}"
    
    case "$command" in
        check)
            check_prerequisites
            ;;
        start)
            check_prerequisites
            start_nodes
            start_master
            status
            ;;
        start-nodes)
            check_prerequisites
            start_nodes
            ;;
        start-master)
            start_master
            ;;
        stop)
            stop_all
            ;;
        stop-nodes)
            stop_nodes
            ;;
        stop-master)
            stop_master
            ;;
        restart)
            stop_all
            sleep 2
            check_prerequisites
            start_nodes
            start_master
            status
            ;;
        status)
            status
            ;;
        test)
            test_inference "${2:-}"
            ;;
        logs)
            logs "${2:-}"
            ;;
        clean)
            clean
            ;;
        *)
            usage
            exit 1
            ;;
    esac
}

main "$@"
