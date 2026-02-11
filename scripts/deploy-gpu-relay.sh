#!/usr/bin/env bash
# Deploy GPU nodes with relay topology
# Master (localhost) is execution-capable with local Edge + remote Relay on jupiter
#
# ──────────────────────────────────────────────────────────────────────
# DEPLOYMENT ORCHESTRATOR
# ──────────────────────────────────────────────────────────────────────
# This script orchestrates the full deployment lifecycle:
#   build/rebuild → start edges → start master → start relay
#
# Build delegation:
#   build/rebuild commands call: ./docker/scripts/build/build-gpu.sh
#   On both localhost and jupiter (parallel SSH builds)
#
# Key environment variables:
#   BUILD_SCOPE=all|llama    Which components to rebuild (default: all)
#   BUILD_NO_CACHE=1         Disable Docker build cache (forces rebuild)
#   VLLM_VERSION=TAG         Pin vLLM version
#   VLLM_EXTRA_FLAGS=FLAGS   Extra vLLM build flags
#   JUPITER_HOST=USER@HOST   Jupiter SSH target
#
# TRICKY: BUILD_SCOPE=llama only affects the --no-vllm flag.
# It does NOT selectively rebuild Docker layers (that's handled by
# Dockerfile.gpu's multi-stage architecture). BUILD_SCOPE is a
# convenience to skip the vLLM build entirely when iterating on
# llama components.
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Track background processes for cleanup
declare -a BACKGROUND_PIDS=()

# Cleanup function
cleanup() {
    local exit_code=$?
    if [[ ${#BACKGROUND_PIDS[@]} -gt 0 ]]; then
        warn "Cleaning up background processes..."
        for pid in "${BACKGROUND_PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                log "Killing process $pid"
                kill "$pid" 2>/dev/null || true
            fi
        done
        # Also kill remote build processes on jupiter (script + docker processes)
        ssh "$JUPITER_HOST" "pkill -9 -f 'build-gpu.sh|build_vllm.py|docker.*build.*gateway-base'; exit 0" 2>/dev/null || true
    fi
    exit $exit_code
}

# Set up signal handlers
trap cleanup EXIT INT TERM

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# Configuration
MASTER_CONFIG="config/stargate_config.gpu-master-localhost.yaml"
RELAY_JUPITER_CONFIG="config/stargate_config.gpu-relay-jupiter.yaml"
MASTER_PID_FILE="/tmp/universal-stargate-gpu-master.pid"
RELAY_JUPITER_PID_FILE="/tmp/universal-stargate-relay-jupiter.pid"

JUPITER_HOST="${JUPITER_HOST:-user@remote-gpu-node}"

#VLLM_VERSION=v0.13.0
# Calculate max jobs per host (nproc evaluated on each host)
VLLM_MAX_JOBS_LOCALHOST=$((($(nproc) / 2) - 0))
# Additional vLLM build flags
# Empty (default): Auto-apply patches for verified versions (v0.13.x)
# --no-patches: Skip all patches (generic multi-arch build, clean version string)
# --apply-patches: Force patches even for unverified versions
VLLM_EXTRA_FLAGS="${VLLM_EXTRA_FLAGS:-}"

# Load environment from config file early (for BUILD_SCOPE and other variables)
if [[ -f .env.gpu-master-localhost ]]; then
    set -a
    source .env.gpu-master-localhost
    set +a
    log "Loaded environment from .env.gpu-master-localhost"
fi

# BUILD_SCOPE: Control which components are rebuilt
# - "all" (default): Build everything (vLLM + llama-cpp-python + llama-server)
# - "llama": Only rebuild llama-cpp-python + llama-server (skip vLLM)
#
# ──────────────────────────────────────────────────────────────────────
# TRICKY: BUILD_SCOPE=llama produces a NO-VLLM image (before Phase 3)
# ──────────────────────────────────────────────────────────────────────
# Currently, BUILD_SCOPE=llama passes --no-vllm to build-gpu.sh.
# This DISABLES vLLM entirely — the resulting image cannot serve
# HuggingFace/safetensors models, only GGUF models.
#
# This is acceptable for iterating on llama-cpp or llama-server changes
# because Docker cache will restore the full image on the next
# BUILD_SCOPE=all build.
#
# After multi-stage Dockerfile refactor: BUILD_SCOPE=llama will keep
# the cached vLLM from a previous build, producing a complete image.
# ──────────────────────────────────────────────────────────────────────
BUILD_SCOPE="${BUILD_SCOPE:-all}"
BUILD_NO_CACHE="${BUILD_NO_CACHE:-}"

is_truthy() {
    local raw="${1:-}"
    local value="${raw,,}"
    case "${value}" in
        1|true|yes|y|on)
            return 0
            ;;
        ""|0|false|no|n|off)
            return 1
            ;;
        *)
            warn "Unknown BUILD_NO_CACHE value '${raw}'; treating as enabled"
            return 0
            ;;
    esac
}

NO_CACHE_FLAG=""
if is_truthy "${BUILD_NO_CACHE}"; then
    NO_CACHE_FLAG="--no-cache"
fi
usage() {
    cat << EOF
Usage: $0 <command> [options]

Topology:
  Master Stargate (localhost:9999) - Pure orchestrator
    ├─ Local Edge+Gateway (Unix socket to Docker container on localhost)
    └─ Remote Relay Stargate (jupiter:9999) → Edge+Gateway (Docker on jupiter)
        └─ Edge+Gateway (Docker on jupiter)

Commands:
    build                      Build GPU images on both hosts
    rebuild                    Fast rebuild (uses Docker cache)
    start-edge-localhost       Start Edge+Gateway container on localhost
    start-edge-jupiter        Start Edge+Gateway container on jupiter (via SSH)
    start-master              Start Master Stargate on localhost (execution-capable)
    start-relay-jupiter       Start Relay Stargate on jupiter (via SSH)
    start-all                 Start everything in correct order
    stop-edge-localhost       Stop localhost Edge container
    stop-edge-jupiter         Stop jupiter Edge container
    stop-master               Stop Master Stargate
    stop-relay-jupiter        Stop jupiter Relay Stargate
    stop-all                  Stop everything
    restart                   Stop everything, clean sockets, and restart
    status                    Show status of all components

Options:
    --jupiter-host HOST       Remote GPU node hostname/IP (default: user@remote-gpu-node)
    --no-cache               Disable Docker build cache (forces rebuild)

Environment Variables:
    BUILD_SCOPE=all|llama    Control rebuild scope (default: all)
                             llama: Only rebuild llama-cpp + llama-server (skip vLLM)
    BUILD_NO_CACHE=1         Disable Docker build cache (forces rebuild)
    VLLM_VERSION=TAG         Pin vLLM version (e.g., v0.13.0)
    VLLM_EXTRA_FLAGS=FLAGS   Extra vLLM build flags (e.g., --no-patches)
    JUPITER_HOST=USER@HOST   Remote GPU node SSH target (default: user@remote-gpu-node)

Examples:
    $0 build
    $0 rebuild
    $0 start-all
    $0 restart
    $0 status
    $0 stop-all
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --jupiter-host)
                JUPITER_HOST="$2"
                shift 2
                ;;
            --no-cache)
                NO_CACHE_FLAG="--no-cache"
                shift
                ;;
            *)
                shift
                ;;
        esac
    done
}

build_prerequisites() {
    log "Building GPU images on both hosts (parallel)..."
    
    # Build GPU image on localhost in background
    log "Starting GPU build on localhost (background)..."
    local build_args="--cpu-native --gpu-native"
    [[ -n "${NO_CACHE_FLAG}" ]] && build_args+=" ${NO_CACHE_FLAG}"
    [[ -n "${VLLM_VERSION:-}" ]] && build_args+=" --vllm-version=${VLLM_VERSION}"
    [[ -n "${VLLM_MAX_JOBS_LOCALHOST:-}" ]] && build_args+=" --vllm-jobs=${VLLM_MAX_JOBS_LOCALHOST}"
    
    # Apply BUILD_SCOPE
    if [[ "${BUILD_SCOPE}" == "llama" ]]; then
        warn "BUILD_SCOPE=llama: Skipping vLLM (llama-only image)"
        build_args+=" --no-vllm"
    fi
    
    # Export VLLM_EXTRA_FLAGS as environment variable for build-gpu.sh to use
    export VLLM_EXTRA_FLAGS
    ./docker/scripts/build/build-gpu.sh $build_args > /tmp/build-gpu.log 2>&1 &
    local localhost_pid=$!
    BACKGROUND_PIDS+=("$localhost_pid")
    
    # Build GPU image on jupiter in background
    log "Starting GPU build on jupiter (background)..."
    local build_args="--cpu-native --gpu-native"
    [[ -n "${NO_CACHE_FLAG}" ]] && build_args+=" ${NO_CACHE_FLAG}"
    [[ -n "${VLLM_VERSION:-}" ]] && build_args+=" --vllm-version=${VLLM_VERSION}"
    
    # Apply BUILD_SCOPE (same as localhost)
    if [[ "${BUILD_SCOPE}" == "llama" ]]; then
        build_args+=" --no-vllm"
    fi
    
    # Pass VLLM_EXTRA_FLAGS to remote host via environment variable
    ssh "$JUPITER_HOST" "cd /mnt/torus/projects/universal-llm-gateway && \
        JOBS=\$(((\$(nproc) / 2) - 0)) && \
        export VLLM_EXTRA_FLAGS='${VLLM_EXTRA_FLAGS}' && \
        ./docker/scripts/build/build-gpu.sh $build_args --vllm-jobs=\${JOBS} > /tmp/build-gpu.log 2>&1 &"
    local jupiter_build=true
    
    # Wait for localhost build
    log "Waiting for localhost build to complete (PID: $localhost_pid, log: /tmp/build-gpu.log)..."
    if wait $localhost_pid; then
        success "Localhost build completed"
        # Remove from tracking array
        BACKGROUND_PIDS=("${BACKGROUND_PIDS[@]/$localhost_pid}")
    else
        error "Localhost build failed. Check /tmp/build-gpu.log"
        tail -50 /tmp/build-gpu.log
        exit 1
    fi
    
    # Wait for jupiter build
    log "Waiting for jupiter build to complete (log: ${JUPITER_HOST}:/tmp/build-gpu.log)..."
    
    # Poll jupiter for build completion
    local max_wait=3600  # 1 hour timeout
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        # Check if build script is still running
        # Use [b] trick to exclude pgrep itself from matching
        if ssh "$JUPITER_HOST" "pgrep -f 'docker/scripts/[b]uild/build-gpu.sh' > /dev/null 2>&1"; then
            # Build still running
            sleep 10
            waited=$((waited + 10))
            if [[ $((waited % 60)) -eq 0 ]]; then
                log "Still waiting for jupiter build... ($((waited / 60)) min elapsed)"
            fi
            continue
        fi
        
        # Build process finished, check if image exists
        if ssh "$JUPITER_HOST" "docker image inspect universal-llm-gateway:gpu >/dev/null 2>&1"; then
            success "Jupiter build completed"
            break
        else
            error "Jupiter build failed. Check ${JUPITER_HOST}:/tmp/build-gpu.log"
            ssh "$JUPITER_HOST" "tail -50 /tmp/build-gpu.log"
            exit 1
        fi
    done
    
    if [[ $waited -ge $max_wait ]]; then
        error "Jupiter build timeout. Check ${JUPITER_HOST}:/tmp/build-gpu.log"
        ssh "$JUPITER_HOST" "tail -50 /tmp/build-gpu.log"
        exit 1
    fi
    
    success "All prerequisites ready"
}

rebuild_code() {
    log "Rebuilding images on both hosts (parallel, uses Docker cache)..."
    
    # Rebuild on localhost in background
    log "Rebuilding on localhost (background)..."
    local build_args="--cpu-native --gpu-native"
    [[ -n "${NO_CACHE_FLAG}" ]] && build_args+=" ${NO_CACHE_FLAG}"
    [[ -n "${VLLM_VERSION:-}" ]] && build_args+=" --vllm-version=${VLLM_VERSION}"
    [[ -n "${VLLM_MAX_JOBS_LOCALHOST:-}" ]] && build_args+=" --vllm-jobs=${VLLM_MAX_JOBS_LOCALHOST}"
    
    # Apply BUILD_SCOPE
    if [[ "${BUILD_SCOPE}" == "llama" ]]; then
        warn "BUILD_SCOPE=llama: Skipping vLLM (llama-only image)"
        build_args+=" --no-vllm"
    fi
    
    # Export VLLM_EXTRA_FLAGS as environment variable for build-gpu.sh to use
    export VLLM_EXTRA_FLAGS
    ./docker/scripts/build/build-gpu.sh $build_args > /tmp/rebuild-gpu.log 2>&1 &
    local localhost_pid=$!
    BACKGROUND_PIDS+=("$localhost_pid")
    
    # Rebuild on jupiter in background
    log "Rebuilding on jupiter (background)..."
    local build_args="--cpu-native --gpu-native"
    [[ -n "${NO_CACHE_FLAG}" ]] && build_args+=" ${NO_CACHE_FLAG}"
    [[ -n "${VLLM_VERSION:-}" ]] && build_args+=" --vllm-version=${VLLM_VERSION}"
    
    # Apply BUILD_SCOPE (same as localhost)
    if [[ "${BUILD_SCOPE}" == "llama" ]]; then
        build_args+=" --no-vllm"
    fi
    
    # Pass VLLM_EXTRA_FLAGS to remote host via environment variable
    ssh "$JUPITER_HOST" "cd /mnt/torus/projects/universal-llm-gateway && \
        JOBS=\$(((\$(nproc) / 2) - 0)) && \
        export VLLM_EXTRA_FLAGS='${VLLM_EXTRA_FLAGS}' && \
        ./docker/scripts/build/build-gpu.sh $build_args --vllm-jobs=\${JOBS} > /tmp/rebuild-gpu.log 2>&1 &"
    local jupiter_build=true
    
    # Wait for localhost rebuild
    log "Waiting for localhost rebuild (PID: $localhost_pid, log: /tmp/rebuild-gpu.log)..."
    if wait $localhost_pid; then
        success "Localhost rebuild completed"
        # Remove from tracking array
        BACKGROUND_PIDS=("${BACKGROUND_PIDS[@]/$localhost_pid}")
    else
        error "Localhost rebuild failed. Check /tmp/rebuild-gpu.log"
        tail -50 /tmp/rebuild-gpu.log
        exit 1
    fi
    
    # Wait for jupiter rebuild
    log "Waiting for jupiter rebuild (log: ${JUPITER_HOST}:/tmp/rebuild-gpu.log)..."
    
    local max_wait=600  # 10 min timeout (should be fast with cache)
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        # Check if build script is still running
        # Use [b] trick to exclude pgrep itself from matching
        if ssh "$JUPITER_HOST" "pgrep -f 'docker/scripts/[b]uild/build-gpu.sh' > /dev/null 2>&1"; then
            # Build still running
            sleep 5
            waited=$((waited + 5))
            if [[ $((waited % 30)) -eq 0 ]]; then
                log "Still waiting for jupiter rebuild... ($waited sec elapsed)"
            fi
            continue
        fi
        
        # Build process finished, check if image exists
        if ssh "$JUPITER_HOST" "docker image inspect universal-llm-gateway:gpu >/dev/null 2>&1"; then
            success "Jupiter rebuild completed"
            break
        else
            error "Jupiter rebuild failed. Check ${JUPITER_HOST}:/tmp/rebuild-gpu.log"
            ssh "$JUPITER_HOST" "tail -50 /tmp/rebuild-gpu.log"
            exit 1
        fi
    done
    
    if [[ $waited -ge $max_wait ]]; then
        error "Jupiter rebuild timeout. Check ${JUPITER_HOST}:/tmp/rebuild-gpu.log"
        ssh "$JUPITER_HOST" "tail -50 /tmp/rebuild-gpu.log"
        exit 1
    fi
    
    success "Code rebuild complete on both hosts"
}

start_edge_localhost() {
    log "Starting Edge+Gateway container on localhost..."
    
    # Create mount directories (models dir is host system /mnt/torus/models)
    mkdir -p tmp/gpu-nodes/localhost/{logs,output}
    
    # Socket directory selection: try system dir first, fall back to user dir for non-admin
    local socket_dir="/tmp/universal-protocol"
    local user_socket_dir="$PROJECT_ROOT/tmp/sockets"
    local use_user_mode=false
    
    # Try to use system socket directory
    mkdir -p "$socket_dir" 2>/dev/null || true
    if ! chmod 0777 "$socket_dir" 2>/dev/null; then
        # Can't chmod - check if we can write anyway
        if ! touch "$socket_dir/.test" 2>/dev/null; then
            # Can't write to system dir - use user-owned directory
            warn "Cannot write to $socket_dir (no admin access)"
            log "Using user-owned socket directory: $user_socket_dir"
            socket_dir="$user_socket_dir"
            mkdir -p "$socket_dir"
            use_user_mode=true
        else
            rm -f "$socket_dir/.test"
        fi
    fi
    
    # Export socket path for container
    export SOCKET_DIR="$socket_dir"
    export SOCKET_PATH="$socket_dir/edge-localhost.sock"
    
    # For user mode: run container as current user to avoid permission issues
    if [ "$use_user_mode" = true ]; then
        export DOCKER_USER="$(id -u):$(id -g)"
        log "Running container as user $(id -u):$(id -g) (non-admin mode)"
    else
        unset DOCKER_USER
    fi

    # Source env file to make federation keys available for docker compose substitution
    if [[ -f .env.gpu-edge-localhost ]]; then
        set -a
        source .env.gpu-edge-localhost
        set +a
        log "Loaded environment from .env.gpu-edge-localhost"
    else
        warn "No .env.gpu-edge-localhost found - container may fail authentication"
    fi
    
    # Start container
    docker compose -f docker/compose/gpu-edge-localhost.yml up -d
    
    # Re-ensure socket dir is writable after compose up (Docker can re-own it)
    chmod 0777 "$socket_dir" 2>/dev/null || true
    
    # Wait for socket to appear
    log "Waiting for localhost Edge socket at $SOCKET_PATH..."
    local max_wait=60
    local waited=0
    
    while [[ $waited -lt $max_wait ]]; do
        if [[ -S "$SOCKET_PATH" ]]; then
            success "Localhost Edge+Gateway socket ready at $SOCKET_PATH"
            return 0
        fi
        log "  ... waiting for socket (${waited}s/${max_wait}s)"
        sleep 2
        waited=$((waited + 2))
    done
    
    error "Localhost Edge socket did not appear at $SOCKET_PATH"
    docker logs edge-localhost --tail 50
    if [ "$use_user_mode" = true ]; then
        error "User mode failed - check container logs above for errors"
    else
        error "If container logs show PermissionError on create_unix_server:"
        error "  sudo chmod 0777 /tmp/universal-protocol"
    fi
    exit 1
}

start_edge_jupiter() {
    log "Starting Edge+Gateway container on jupiter ($JUPITER_HOST)..."
    
    # Create mount directories on jupiter via SSH (models dir is host system /mnt/mai/models)
    ssh "$JUPITER_HOST" "mkdir -p /mnt/torus/projects/universal-llm-gateway/tmp/gpu-nodes/jupiter/{logs,output}"
    
    # Ensure socket directory exists and is writable by container (appuser UID 1000)
    ssh "$JUPITER_HOST" "mkdir -p /tmp/universal-protocol && chmod 0777 /tmp/universal-protocol 2>/dev/null || true"

    # Start container on jupiter (source env file for federation keys)
    ssh "$JUPITER_HOST" "cd /mnt/torus/projects/universal-llm-gateway && \
        if [[ -f .env.gpu-edge-jupiter ]]; then set -a; source .env.gpu-edge-jupiter; set +a; fi && \
        docker compose -f docker/compose/gpu-edge-jupiter.yml up -d"
    
    # Wait for socket (check via SSH)
    log "Waiting for jupiter Edge socket..."
    local max_wait=60
    local waited=0
    
    while [[ $waited -lt $max_wait ]]; do
        if ssh "$JUPITER_HOST" "test -S /tmp/universal-protocol/edge-jupiter.sock"; then
            success "Jupiter Edge+Gateway socket ready"
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    
    error "Jupiter Edge socket did not appear"
    ssh "$JUPITER_HOST" "docker logs edge-jupiter --tail 50"
    error "If container logs show PermissionError on create_unix_server, on jupiter run:"
    error "  sudo chmod 0777 /tmp/universal-protocol"
    exit 1
}

start_master() {
    log "Starting Master Stargate on localhost (execution-capable)..."
    
    if [[ -f "$MASTER_PID_FILE" ]]; then
        local pid=$(cat "$MASTER_PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            warn "Master already running (PID: $pid)"
            return 0
        fi
        rm -f "$MASTER_PID_FILE"
    fi
    
    # Determine socket path (may have been set by start_edge_localhost)
    local socket_path="${SOCKET_PATH:-/tmp/universal-protocol/edge-localhost.sock}"
    
    # Check if local Edge socket exists first
    if [[ ! -S "$socket_path" ]]; then
        error "Local Edge socket must exist before starting Master: $socket_path"
        error "Run: $0 start-edge-localhost"
        exit 1
    fi
    
    # Load environment if exists (includes federation keys)
    if [[ -f .env.gpu-master-localhost ]]; then
        set -a
        source .env.gpu-master-localhost
        set +a
        log "Loaded environment from .env.gpu-master-localhost"
    fi
    
    # Update master config with jupiter hostname and socket path
    sed -i.bak "s|jupiter.local|$JUPITER_HOST|g" "$MASTER_CONFIG"
    sed -i.bak "s|/tmp/universal-protocol/edge-localhost.sock|$socket_path|g" "$MASTER_CONFIG"
    
    # Export config and start
    export STARGATE_CONFIG="$MASTER_CONFIG"
    ./services/universal-stargate/scripts/start-stargate.sh --enable-tcp-monitoring debug &
    
    local master_pid=$!
    echo "$master_pid" > "$MASTER_PID_FILE"
    
    # Wait for health
    log "Waiting for Master to be ready..."
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
    
    error "Master did not start in time"
    exit 1
}

start_relay_jupiter() {
    log "Starting Relay Stargate on jupiter ($JUPITER_HOST)..."
    
    # Check if jupiter Edge socket exists first
    if ! ssh "$JUPITER_HOST" "test -S /tmp/universal-protocol/edge-jupiter.sock"; then
        error "Jupiter Edge socket must exist before starting Relay"
        error "Run: $0 start-edge-jupiter"
        exit 1
    fi
    
    # Start relay on jupiter (env file sourced for federation keys)
    ssh "$JUPITER_HOST" "mkdir -p /tmp/logs && cd /mnt/torus/projects/universal-llm-gateway && \
        if [[ -f .env.gpu-relay-jupiter ]]; then set -a; source .env.gpu-relay-jupiter; set +a; fi && \
        export STARGATE_CONFIG='$RELAY_JUPITER_CONFIG' && \
        ./services/universal-stargate/scripts/start-stargate.sh debug > /tmp/logs/relay-jupiter.log 2>&1 & \
        echo \$! > $RELAY_JUPITER_PID_FILE"
    
    # Wait a moment for startup
    sleep 3
    
    # Check if process is running
    if ssh "$JUPITER_HOST" "test -f $RELAY_JUPITER_PID_FILE && ps -p \$(cat $RELAY_JUPITER_PID_FILE) > /dev/null 2>&1"; then
        local pid=$(ssh "$JUPITER_HOST" "cat $RELAY_JUPITER_PID_FILE")
        success "Jupiter Relay Stargate started (PID: $pid)"
    else
        error "Jupiter Relay failed to start"
        ssh "$JUPITER_HOST" "tail -50 /tmp/logs/relay-jupiter.log"
        exit 1
    fi
}

stop_edge_localhost() {
    log "Stopping localhost Edge container..."
    # --volumes removed: it can delete/re-own the bind-mounted socket directory,
    # causing PermissionError on next start when appuser tries create_unix_server
    docker compose -f docker/compose/gpu-edge-localhost.yml down
    success "Localhost Edge stopped"
}

stop_edge_jupiter() {
    log "Stopping jupiter Edge container..."
    # --volumes removed: same as stop_edge_localhost (protects socket dir ownership)
    ssh "$JUPITER_HOST" "cd /mnt/torus/projects/universal-llm-gateway && docker compose -f docker/compose/gpu-edge-jupiter.yml down"
    success "Jupiter Edge stopped"
}

stop_master() {
    log "Stopping Master Stargate..."
    if [[ -f "$MASTER_PID_FILE" ]]; then
        local pid=$(cat "$MASTER_PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            pkill -TERM -P "$pid" 2>/dev/null || true
            kill -TERM "$pid" 2>/dev/null || true
            sleep 2
            if ps -p "$pid" > /dev/null 2>&1; then
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$MASTER_PID_FILE"
    fi
    success "Master stopped"
}

stop_relay_jupiter() {
    log "Stopping jupiter Relay Stargate..."
    ssh "$JUPITER_HOST" "if [[ -f $RELAY_JUPITER_PID_FILE ]]; then \
        pid=\$(cat $RELAY_JUPITER_PID_FILE); \
        if ps -p \$pid > /dev/null 2>&1; then \
            pkill -TERM -P \$pid 2>/dev/null || true; \
            kill -TERM \$pid 2>/dev/null || true; \
            sleep 2; \
            if ps -p \$pid > /dev/null 2>&1; then kill -9 \$pid 2>/dev/null || true; fi; \
        fi; \
        rm -f $RELAY_JUPITER_PID_FILE; \
    fi"
    success "Jupiter Relay stopped"
}

clean_sockets() {
    log "Cleaning up sockets..."
    
    # Localhost - clean both system and user socket dirs
    rm -f /tmp/universal-protocol/*.sock /tmp/process_ipc/*.sock 2>/dev/null || true
    rm -f "$PROJECT_ROOT"/tmp/sockets/*.sock 2>/dev/null || true
    
    # Jupiter
    ssh "$JUPITER_HOST" "rm -f /tmp/universal-protocol/*.sock /tmp/process_ipc/*.sock"
    
    success "Sockets cleaned"
}

restart_all() {
    log "Restarting entire relay topology (with rebuild)..."
    
    # Stop everything
    stop_relay_jupiter
    stop_master
    stop_edge_localhost
    stop_edge_jupiter
    
    # Clean sockets
    clean_sockets
    
    # Rebuild images (fast with Docker cache)
    rebuild_code
    
    # Wait a moment
    sleep 2
    
    # Start everything in correct order
    log "Starting in correct order: Edges → Master → Relay..."
    start_edge_localhost
    start_edge_jupiter
    start_master
    start_relay_jupiter
    
    show_status
    success "Restart complete"
}

show_status() {
    log "Checking status..."
    
    echo ""
    echo "=== Localhost Edge (Edge+Gateway) ==="
    docker ps --filter "name=edge-localhost" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    
    echo ""
    echo "=== Jupiter Edge (Edge+Gateway) ==="
    ssh "$JUPITER_HOST" "docker ps --filter 'name=edge-jupiter' --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'" 2>/dev/null || echo "Unable to connect"
    
    echo ""
    echo "=== Master Stargate (localhost) ==="
    if [[ -f "$MASTER_PID_FILE" ]]; then
        local pid=$(cat "$MASTER_PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "Running (PID: $pid)"
            curl -sf http://localhost:9999/health > /dev/null 2>&1 && echo "Health: ✓" || echo "Health: ✗"
        else
            echo "Not running (stale PID)"
        fi
    else
        echo "Not running"
    fi
    
    echo ""
    echo "=== Jupiter Relay Stargate ==="
    if ssh "$JUPITER_HOST" "test -f $RELAY_JUPITER_PID_FILE" 2>/dev/null; then
        local pid=$(ssh "$JUPITER_HOST" "cat $RELAY_JUPITER_PID_FILE")
        if ssh "$JUPITER_HOST" "ps -p $pid > /dev/null 2>&1"; then
            echo "Running (PID: $pid)"
        else
            echo "Not running (stale PID)"
        fi
    else
        echo "Not running"
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

main() {
    local command="${1:-}"
    shift || true
    
    parse_args "$@"
    
    case "$command" in
    build)
        build_prerequisites
        ;;
    rebuild)
        rebuild_code
        ;;
    start-edge-localhost)
            start_edge_localhost
            ;;
        start-edge-jupiter)
            start_edge_jupiter
            ;;
        start-master)
            start_master
            ;;
        start-relay-jupiter)
            start_relay_jupiter
            ;;
        start-all)
            log "Starting in correct order: Edges → Master → Relay..."
            start_edge_localhost
            start_edge_jupiter
            start_master
            start_relay_jupiter
            show_status
            ;;
        stop-edge-localhost)
            stop_edge_localhost
            ;;
        stop-edge-jupiter)
            stop_edge_jupiter
            ;;
        stop-master)
            stop_master
            ;;
        stop-relay-jupiter)
            stop_relay_jupiter
            ;;
        stop-all)
            stop_relay_jupiter
            stop_master
            stop_edge_localhost
            stop_edge_jupiter
            ;;
        restart)
            restart_all
            ;;
        status)
            show_status
            ;;
        test)
            test_inference "${1:-}"
            ;;
        *)
            usage
            exit 1
            ;;
    esac
}

main "$@"
