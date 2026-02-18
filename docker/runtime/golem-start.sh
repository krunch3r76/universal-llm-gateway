#!/usr/bin/env bash
#
# Golem Network Startup Script
# Environment-based configuration for Universal LLM Gateway deployment
#
# Usage:
#   golem-start.sh gateway    - Start gateway service
#   golem-start.sh stargate   - Start stargate service
#   golem-start.sh both       - Start both services
#   golem-start.sh status     - Check service status
#

set -euo pipefail

# ============================================================================
# Configuration and Environment
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-/app}"
GATEWAY_DIR="${APP_DIR}/services/_universal-llm-gateway"
STARGATE_DIR="${APP_DIR}/services/universal-stargate"

# Service configuration from environment (with defaults)
GATEWAY_HOST="${GATEWAY_HOST:-0.0.0.0}"
GATEWAY_PORT="${GATEWAY_PORT:-9998}"
STARGATE_HOST="${STARGATE_HOST:-0.0.0.0}"
STARGATE_PORT="${STARGATE_PORT:-9999}"
LOG_LEVEL="${LOG_LEVEL:-info}"
ENVIRONMENT="${ENVIRONMENT:-default}"

# Optional Edge Stargate (Unix socket) started alongside the Relay Stargate.
# Used when a node's Relay config sets federation.local_edge.
EDGE_STARGATE_SOCKET="${EDGE_STARGATE_SOCKET:-}"
EDGE_STARGATE_CONFIG="${EDGE_STARGATE_CONFIG:-}"

# Golem volume paths (per Golem documentation)
GOLEM_MODELS="${MODEL_PATH_ROOT:-/golem/models}"
GOLEM_INPUT="${GOLEM_INPUT:-/golem/input}"
GOLEM_OUTPUT="${GOLEM_OUTPUT:-/golem/output}"
GOLEM_LOGS="${GOLEM_LOGS:-/golem/logs}"

# Feature flags
ENABLE_MODEL_AVAILABILITY_CHECK="${ENABLE_MODEL_AVAILABILITY_CHECK:-true}"
ENABLE_MANAGEMENT_API="${ENABLE_MANAGEMENT_API:-true}"
DISABLE_HEALTH_CHECKING="${DISABLE_HEALTH_CHECKING:-false}"
DEBUG_MODE="${DEBUG_MODE:-false}"
ENABLE_PROFILING="${ENABLE_PROFILING:-false}"

# Resource paths (override defaults for Golem)
WORKER_LOG_DIR="${WORKER_LOG_DIR:-${GOLEM_LOGS}/workers}"
SOCKET_DIR="${SOCKET_DIR:-${GOLEM_OUTPUT}/sockets}"
MODEL_CACHE_DIR="${MODEL_CACHE_DIR:-${GOLEM_MODELS}}"

# Timeouts
PROCESS_STARTUP_TIMEOUT="${PROCESS_STARTUP_TIMEOUT:-300}"
GATEWAY_SHUTDOWN_GRACE="${GATEWAY_SHUTDOWN_GRACE:-30}"

# CPU optimization (optional - auto-detect if not set)
# OMP_NUM_THREADS - set externally if override needed
# MKL_NUM_THREADS - set externally if override needed
# TOKENIZERS_PARALLELISM - set externally if override needed

# ============================================================================
# Logging
# ============================================================================

log_info() {
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] [INFO] $*" >&2
}

log_error() {
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] [ERROR] $*" >&2
}

log_debug() {
    if [[ "${DEBUG_MODE}" == "true" ]]; then
        echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] [DEBUG] $*" >&2
    fi
}

# ============================================================================
# GPU Detection and Validation
# ============================================================================

detect_gpu() {
    log_info "Detecting GPU availability..."
    
    # Check if running in Golem environment
    if [[ "${GOLEM_TASK:-false}" == "true" ]]; then
        log_info "Running in Golem task - GPU not supported"
        return 1
    fi
    
    # Check if NVIDIA runtime is available
    if command -v nvidia-smi &> /dev/null; then
        if nvidia-smi &> /dev/null; then
            log_info "✅ NVIDIA GPU detected"
            nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader | while read -r line; do
                log_info "  GPU: ${line}"
            done
            
            # Log CUDA version
            local cuda_version
            cuda_version=$(nvidia-smi | grep "CUDA Version" | awk '{print $9}')
            log_info "  CUDA Driver Version: ${cuda_version}"
            
            return 0
        else
            log_error "❌ nvidia-smi found but failed to query GPU"
            log_error "   Check NVIDIA driver installation and permissions"
            return 1
        fi
    else
        log_info "ℹ️  No NVIDIA GPU detected (CPU-only mode)"
        return 1
    fi
}

validate_gpu_environment() {
    if [[ "${GPU_ENABLED:-false}" == "true" ]]; then
        log_info "GPU mode enabled - validating environment..."
        
        # Prevent GPU mode in Golem tasks
        if [[ "${GOLEM_TASK:-false}" == "true" ]]; then
            log_error "❌ GPU not supported in Golem Network tasks"
            log_error "   Set GPU_ENABLED=false or use CPU-only image"
            return 1
        fi
        
        # Detect GPU
        if ! detect_gpu; then
            log_error "❌ GPU_ENABLED=true but no GPU available"
            log_error "   Either:"
            log_error "   1. Set GPU_ENABLED=false to run in CPU mode"
            log_error "   2. Ensure NVIDIA runtime is configured:"
            log_error "      - Install NVIDIA drivers"
            log_error "      - Install NVIDIA Container Toolkit"
            log_error "      - Restart Docker daemon"
            return 1
        fi
        
        # Validate CUDA is accessible to Python (best-effort: torch > nvidia-smi)
        log_info "Validating CUDA availability to Python..."
        if python3 -c "import torch" 2>/dev/null; then
            if ! python3 -c "import torch; assert torch.cuda.is_available(), 'CUDA not available'; print(f'PyTorch {torch.__version__}, CUDA {torch.version.cuda}, {torch.cuda.device_count()} device(s)')" 2>&1; then
                log_error "❌ PyTorch cannot access CUDA"
                log_error "   This may indicate:"
                log_error "   1. Wrong Docker image (use Dockerfile.gpu)"
                log_error "   2. NVIDIA runtime not enabled (--gpus all)"
                log_error "   3. CUDA version mismatch"
                return 1
            fi
        else
            log_info "  PyTorch not installed — CUDA validated via nvidia-smi only"
        fi
        
        log_info "✅ GPU environment validated successfully"
        
    else
        log_info "Running in CPU-only mode"
        
        # Warn if GPU detected but not enabled
        if detect_gpu 2>/dev/null; then
            log_info "⚠️  GPU detected but GPU_ENABLED=false"
            log_info "   Set GPU_ENABLED=true to use GPU acceleration"
        fi
    fi
    
    return 0
}

# ============================================================================
# Validation
# ============================================================================

validate_environment() {
    log_info "Validating environment configuration..."
    
    # Validate GPU environment first (if GPU enabled)
    if ! validate_gpu_environment; then
        return 1
    fi
    
    # Check required paths exist
    if [[ ! -d "${GOLEM_MODELS}" ]]; then
        log_error "MODEL_PATH_ROOT (${GOLEM_MODELS}) does not exist"
        return 1
    fi
    
    # Create required directories if they don't exist
    mkdir -p "${GOLEM_LOGS}" "${WORKER_LOG_DIR}" "${SOCKET_DIR}" "${GOLEM_OUTPUT}"
    
    # Check Python version
    local python_version
    python_version=$(python3 --version 2>&1 | awk '{print $2}')
    log_info "Python version: ${python_version}"
    
    if [[ ! "${python_version}" =~ ^3\.12 ]]; then
        log_error "Python 3.12 required, found: ${python_version}"
        return 1
    fi
    
    # Check sitecustomize.py is in place for libs/ path setup
    if [[ ! -f "${APP_DIR}/sitecustomize.py" ]]; then
        log_error "sitecustomize.py missing - required for libs/ imports"
        return 1
    fi
    
    # Verify critical directories
    for dir in "${GATEWAY_DIR}" "${STARGATE_DIR}" "${APP_DIR}/libs"; do
        if [[ ! -d "${dir}" ]]; then
            log_error "Required directory missing: ${dir}"
            return 1
        fi
    done
    
    log_info "Environment validation complete"
    return 0
}

# ============================================================================
# PYTHONPATH Setup (Critical for finding libs/)
# ============================================================================

setup_pythonpath() {
    # Add libs directory to PYTHONPATH for module imports
    export PYTHONPATH="${APP_DIR}/libs:${PYTHONPATH:-}"
    log_info "PYTHONPATH configured: ${PYTHONPATH}"
}

# ============================================================================
# Service Management
# ============================================================================

start_gateway() {
    log_info "Starting Universal LLM Gateway..."
    
    # Check for Unix socket mode
    if [[ -n "${GATEWAY_UNIX_SOCKET:-}" ]]; then
        log_info "  Mode: Unix Socket"
        log_info "  Socket: ${GATEWAY_UNIX_SOCKET}"
    else
        log_info "  Mode: TCP"
        log_info "  Host: ${GATEWAY_HOST}"
        log_info "  Port: ${GATEWAY_PORT}"
    fi
    
    log_info "  Log Level: ${LOG_LEVEL}"
    log_info "  Environment: ${ENVIRONMENT}"
    log_info "  Model Path Root: ${GOLEM_MODELS}"
    log_info "  Worker Logs: ${WORKER_LOG_DIR}"
    log_info "  Socket Dir: ${SOCKET_DIR}"
    
    # Setup Python module path (must include gateway directory for src.* imports)
    setup_pythonpath
    export PYTHONPATH="${GATEWAY_DIR}:${PYTHONPATH}"
    
    cd "${GATEWAY_DIR}" || {
        log_error "Failed to change directory to ${GATEWAY_DIR}"
        exit 1
    }
    
    # Initialize config directory from baked defaults
    if [[ ! -d "${GATEWAY_DIR}/config" ]]; then
        log_info "Initializing config from defaults..."
        cp -r "${GATEWAY_DIR}/config.default" "${GATEWAY_DIR}/config"
    fi
    
    # Export environment variables for gateway
    export GATEWAY_HOST GATEWAY_PORT LOG_LEVEL ENVIRONMENT
    export MODEL_PATH_ROOT="${GOLEM_MODELS}"
    export WORKER_LOG_DIR SOCKET_DIR MODEL_CACHE_DIR
    export ENABLE_MODEL_AVAILABILITY_CHECK ENABLE_MANAGEMENT_API
    export DISABLE_HEALTH_CHECKING DEBUG_MODE ENABLE_PROFILING
    export PROCESS_STARTUP_TIMEOUT GATEWAY_SHUTDOWN_GRACE
    export GATEWAY_LOG_DIR="${GOLEM_LOGS}/gateway"
    export LOG_DIR="${GOLEM_LOGS}/gateway"  # Used by logging.yaml
    
    # -------------------------------------------------------------------------
    # GPU-specific environment variables (vLLM)
    # -------------------------------------------------------------------------
    if [[ "${GPU_ENABLED:-false}" == "true" ]]; then
        # VLLM_SLEEP_WHEN_IDLE is baked into Dockerfile.gpu (ENV directive)
        # It MUST be set at image level because vLLM uses multiprocessing.spawn
        # which only inherits container-level env vars, not shell exports.
        log_info "  VLLM_SLEEP_WHEN_IDLE: ${VLLM_SLEEP_WHEN_IDLE:-not set (using image default)}"
    fi
    
    # -------------------------------------------------------------------------
    # CPU-specific environment variables (llama.cpp)
    # -------------------------------------------------------------------------
    # Optional CPU optimization (only set if provided)
    if [[ -n "${OMP_NUM_THREADS:-}" ]]; then
        export OMP_NUM_THREADS
        log_info "  OMP_NUM_THREADS: ${OMP_NUM_THREADS}"
    fi
    if [[ -n "${MKL_NUM_THREADS:-}" ]]; then
        export MKL_NUM_THREADS
        log_info "  MKL_NUM_THREADS: ${MKL_NUM_THREADS}"
    fi
    if [[ -n "${TOKENIZERS_PARALLELISM:-}" ]]; then
        export TOKENIZERS_PARALLELISM
        log_info "  TOKENIZERS_PARALLELISM: ${TOKENIZERS_PARALLELISM}"
    fi
    
    # Create gateway log directory
    mkdir -p "${GATEWAY_LOG_DIR}"
    
    # Cleanup old Unix socket if exists
    if [[ -n "${GATEWAY_UNIX_SOCKET:-}" ]]; then
        rm -f "${GATEWAY_UNIX_SOCKET}"
        mkdir -p "$(dirname "${GATEWAY_UNIX_SOCKET}")"
    fi
    
    log_info "Launching gateway service..."
    
    # Choose transport mode
    if [[ -n "${GATEWAY_UNIX_SOCKET:-}" ]]; then
        # Unix socket mode
        exec python3 src/main.py \
            --unix-socket "${GATEWAY_UNIX_SOCKET}" \
            --log-level trace
    else
        # TCP mode
        exec python3 -m uvicorn src.main:app \
            --host "${GATEWAY_HOST}" \
            --port "${GATEWAY_PORT}" \
            --log-level trace \
	--timeout-keep-alive 3600 \
	--timeout-graceful-shutdown 120 \
            --no-access-log
    fi
}

start_stargate() {
    log_info "Starting Universal Stargate..."
    
    # Check for Unix socket mode
    if [[ -n "${STARGATE_UNIX_SOCKET:-}" ]]; then
        log_info "  Mode: Unix Socket"
        log_info "  Socket: ${STARGATE_UNIX_SOCKET}"
    else
        log_info "  Mode: TCP"
        log_info "  Host: ${STARGATE_HOST}"
        log_info "  Port: ${STARGATE_PORT}"
    fi
    
    log_info "  Log Level: ${LOG_LEVEL}"
    log_info "  Environment: ${ENVIRONMENT}"
    
    # Setup Python module path (must include stargate directory for systems.* imports)
    setup_pythonpath
    export PYTHONPATH="${STARGATE_DIR}:${PYTHONPATH}"
    
    cd "${STARGATE_DIR}" || {
        log_error "Failed to change directory to ${STARGATE_DIR}"
        exit 1
    }
    
    # Initialize config directory from baked defaults
    if [[ ! -d "${STARGATE_DIR}/config" ]]; then
        log_info "Initializing config from defaults..."
        cp -r "${STARGATE_DIR}/config.default" "${STARGATE_DIR}/config"
    fi
    
    # STARGATE_CONFIG is set via Docker environment variable to point to specific config
    # No copying needed - Python code reads directly from STARGATE_CONFIG path
    
    # Gateway configuration is now in stargate_config.yaml (no separate gateways.yaml)
    
    # Export environment variables for stargate
    export STARGATE_HOST STARGATE_PORT LOG_LEVEL ENVIRONMENT
    export GATEWAY_HOST GATEWAY_PORT
    export STARGATE_LOG_DIR="${GOLEM_LOGS}/stargate"
    export LOG_DIR="${GOLEM_LOGS}/stargate"  # Used by logging.yaml
    
    # Create stargate log directory
    mkdir -p "${STARGATE_LOG_DIR}"
    
    # Cleanup old Unix socket if exists
    if [[ -n "${STARGATE_UNIX_SOCKET:-}" ]]; then
        rm -f "${STARGATE_UNIX_SOCKET}"
        mkdir -p "$(dirname "${STARGATE_UNIX_SOCKET}")"
    fi
    
    log_info "Launching stargate service..."
    
    # Choose transport mode
    if [[ -n "${STARGATE_UNIX_SOCKET:-}" ]]; then
        # Unix socket mode
        exec python3 -m uvicorn systems.proxy.app:app \
            --uds "${STARGATE_UNIX_SOCKET}" \
            --log-level trace \
    --timeout-keep-alive 3600 \
    --timeout-graceful-shutdown 120 \
            --no-access-log
    else
        # TCP mode
        exec python3 -m uvicorn systems.proxy.app:app \
            --host "${STARGATE_HOST}" \
            --port "${STARGATE_PORT}" \
            --log-level trace \
    --timeout-keep-alive 3600 \
    --timeout-graceful-shutdown 120 \
            --no-access-log
    fi
}

start_both() {
    log_info "Starting both Gateway and Stargate services..."
    
    # Check for Unix socket mode
    if [[ -n "${GATEWAY_UNIX_SOCKET:-}" ]]; then
        log_info "  Gateway: Unix Socket (${GATEWAY_UNIX_SOCKET})"
    else
        log_info "  Gateway: TCP (${GATEWAY_HOST}:${GATEWAY_PORT})"
    fi
    
    if [[ -n "${STARGATE_UNIX_SOCKET:-}" ]]; then
        log_info "  Stargate: Unix Socket (${STARGATE_UNIX_SOCKET})"
    else
        log_info "  Stargate: TCP (${STARGATE_HOST}:${STARGATE_PORT})"
    fi
    
    # Setup Python module path (shared by both services)
    setup_pythonpath
    
    # Validate both service directories exist
    if [[ ! -d "${GATEWAY_DIR}" ]]; then
        log_error "Gateway directory not found: ${GATEWAY_DIR}"
        exit 1
    fi
    
    if [[ ! -d "${STARGATE_DIR}" ]]; then
        log_error "Stargate directory not found: ${STARGATE_DIR}"
        exit 1
    fi
    
    # Initialize config directories from baked defaults
    if [[ ! -d "${GATEWAY_DIR}/config" ]]; then
        log_info "Initializing gateway config from defaults..."
        cp -r "${GATEWAY_DIR}/config.default" "${GATEWAY_DIR}/config"
    fi
    
    if [[ ! -d "${STARGATE_DIR}/config" ]]; then
        log_info "Initializing stargate config from defaults..."
        cp -r "${STARGATE_DIR}/config.default" "${STARGATE_DIR}/config"
    fi
    
    # STARGATE_CONFIG is set via Docker environment variable to point to specific config
    # No copying needed - Python code reads directly from STARGATE_CONFIG path
    
    # Gateway configuration is now in stargate_config.yaml (no separate gateways.yaml)
    
    # Create log directories
    mkdir -p "${GOLEM_LOGS}/gateway"
    mkdir -p "${GOLEM_LOGS}/stargate"
    mkdir -p "${WORKER_LOG_DIR}"
    mkdir -p "${SOCKET_DIR}"
    
    # Cleanup old Unix sockets if they exist
    if [[ -n "${GATEWAY_UNIX_SOCKET:-}" ]]; then
        rm -f "${GATEWAY_UNIX_SOCKET}"
        mkdir -p "$(dirname "${GATEWAY_UNIX_SOCKET}")"
    fi
    
    if [[ -n "${STARGATE_UNIX_SOCKET:-}" ]]; then
        rm -f "${STARGATE_UNIX_SOCKET}"
        mkdir -p "$(dirname "${STARGATE_UNIX_SOCKET}")"
    fi
    
    # Export shared environment variables
    export GATEWAY_HOST GATEWAY_PORT STARGATE_HOST STARGATE_PORT
    export GATEWAY_UNIX_SOCKET STARGATE_UNIX_SOCKET
    export LOG_LEVEL ENVIRONMENT
    export MODEL_PATH_ROOT="${GOLEM_MODELS}"
    export WORKER_LOG_DIR SOCKET_DIR MODEL_CACHE_DIR
    export ENABLE_MODEL_AVAILABILITY_CHECK ENABLE_MANAGEMENT_API
    export DISABLE_HEALTH_CHECKING DEBUG_MODE ENABLE_PROFILING
    export PROCESS_STARTUP_TIMEOUT GATEWAY_SHUTDOWN_GRACE
    
    # Gateway-specific exports
    export GATEWAY_LOG_DIR="${GOLEM_LOGS}/gateway"
    
    # Stargate-specific exports
    export STARGATE_LOG_DIR="${GOLEM_LOGS}/stargate"
    
    # Trap signals for graceful shutdown
    EDGE_PID=""
    trap 'kill -TERM $GATEWAY_PID $STARGATE_PID ${EDGE_PID:-} 2>/dev/null; wait' SIGTERM SIGINT
    
    log_info "Starting Gateway service..."
    (
        cd "${GATEWAY_DIR}" || exit 1
        export LOG_DIR="${GOLEM_LOGS}/gateway"
        export PYTHONPATH="${APP_DIR}:${APP_DIR}/libs:${GATEWAY_DIR}:${PYTHONPATH:-}"
        
        # Choose transport mode for Gateway
        if [[ -n "${GATEWAY_UNIX_SOCKET:-}" ]]; then
            # Unix socket mode
            python3 src/main.py \
                --unix-socket "${GATEWAY_UNIX_SOCKET}" \
                --log-level trace \
    --timeout-keep-alive 3600 \
    --timeout-graceful-shutdown 120
        else
            # TCP mode
            python3 -m uvicorn src.main:app \
                --host "${GATEWAY_HOST}" \
                --port "${GATEWAY_PORT}" \
                --log-level trace \
    --timeout-keep-alive 3600 \
    --timeout-graceful-shutdown 120 \
                --no-access-log
        fi
    ) &
    GATEWAY_PID=$!
    log_info "  Gateway PID: ${GATEWAY_PID}"
    
    # Give gateway a moment to start binding to socket/port
    sleep 2

    # Optional Edge Stargate (UDS) for relay topology.
    # If configured, start it BEFORE the Relay Stargate so relay can connect immediately.
    if [[ -n "${EDGE_STARGATE_SOCKET}" || -n "${EDGE_STARGATE_CONFIG}" ]]; then
        if [[ -z "${EDGE_STARGATE_SOCKET}" || -z "${EDGE_STARGATE_CONFIG}" ]]; then
            log_error "EDGE_STARGATE_SOCKET and EDGE_STARGATE_CONFIG must both be set"
            exit 1
        fi

        log_info "Starting Edge Stargate (Unix socket)..."
        log_info "  Edge socket: ${EDGE_STARGATE_SOCKET}"
        log_info "  Edge config: ${EDGE_STARGATE_CONFIG}"

        mkdir -p "/tmp/universal-protocol"
        rm -f "${EDGE_STARGATE_SOCKET}"

        (
            cd "${STARGATE_DIR}" || exit 1
            export LOG_DIR="${GOLEM_LOGS}/stargate-edge"
            export PYTHONPATH="${APP_DIR}:${APP_DIR}/libs:${STARGATE_DIR}:${PYTHONPATH:-}"

            # Edge-specific config/env
            export STARGATE_CONFIG="${EDGE_STARGATE_CONFIG}"
            export STARGATE_SOCKET_PATH="${EDGE_STARGATE_SOCKET}"

            mkdir -p "${LOG_DIR}"

            python3 -m uvicorn systems.proxy.app:app \
                --uds "${EDGE_STARGATE_SOCKET}" \
                --log-level trace \
                --timeout-keep-alive 3600 \
                --timeout-graceful-shutdown 120 \
                --no-access-log
        ) &
        EDGE_PID=$!
        log_info "  Edge Stargate PID: ${EDGE_PID}"

        # Give Edge a moment to bind the UDS
        sleep 1
    fi
    
    log_info "Starting Stargate service..."
    (
        cd "${STARGATE_DIR}" || exit 1
        export LOG_DIR="${GOLEM_LOGS}/stargate"
        export PYTHONPATH="${APP_DIR}:${APP_DIR}/libs:${STARGATE_DIR}:${PYTHONPATH:-}"
        
        # Choose transport mode for Stargate
        if [[ -n "${STARGATE_UNIX_SOCKET:-}" ]]; then
            # Unix socket mode
            python3 -m uvicorn systems.proxy.app:app \
                --uds "${STARGATE_UNIX_SOCKET}" \
                --log-level trace \
    --timeout-keep-alive 3600 \
    --timeout-graceful-shutdown 120 \
                --no-access-log
        else
            # TCP mode
            python3 -m uvicorn systems.proxy.app:app \
                --host "${STARGATE_HOST}" \
                --port "${STARGATE_PORT}" \
                --log-level trace \
    --timeout-keep-alive 3600 \
    --timeout-graceful-shutdown 120 \
                --no-access-log
        fi
    ) &
    STARGATE_PID=$!
    log_info "  Stargate PID: ${STARGATE_PID}"
    
    log_info "Both services started successfully"
    
    if [[ -n "${GATEWAY_UNIX_SOCKET:-}" ]]; then
        log_info "  Gateway: ${GATEWAY_UNIX_SOCKET}"
    else
        log_info "  Gateway: http://${GATEWAY_HOST}:${GATEWAY_PORT}"
    fi
    
    if [[ -n "${STARGATE_UNIX_SOCKET:-}" ]]; then
        log_info "  Stargate: ${STARGATE_UNIX_SOCKET}"
    else
        log_info "  Stargate: http://${STARGATE_HOST}:${STARGATE_PORT}"
    fi
    
    # Wait for processes and report which one exited
    if [[ -n "${EDGE_PID}" ]]; then
        log_info "🔍 DIAGNOSTIC: Waiting for processes (Gateway PID=$GATEWAY_PID, Relay Stargate PID=$STARGATE_PID, Edge Stargate PID=$EDGE_PID)"
        wait -n $GATEWAY_PID $STARGATE_PID $EDGE_PID
    else
        log_info "🔍 DIAGNOSTIC: Waiting for both processes (Gateway PID=$GATEWAY_PID, Stargate PID=$STARGATE_PID)"
        wait -n $GATEWAY_PID $STARGATE_PID
    fi
    exit_code=$?
    
    # Check which process exited
    if ! kill -0 $GATEWAY_PID 2>/dev/null; then
        wait $GATEWAY_PID 2>/dev/null || true
        gw_exit=$?
        log_error "🚨 Gateway (PID $GATEWAY_PID) exited first with code $gw_exit"
        log_info "Stargate(s) still running, killing..."
        kill -TERM $STARGATE_PID ${EDGE_PID:-} 2>/dev/null || true
    elif ! kill -0 $STARGATE_PID 2>/dev/null; then
        wait $STARGATE_PID 2>/dev/null || true
        sg_exit=$?
        log_error "🚨 Relay Stargate (PID $STARGATE_PID) exited first with code $sg_exit"
        log_info "Gateway (PID $GATEWAY_PID) still running, killing..."
        kill -TERM $GATEWAY_PID ${EDGE_PID:-} 2>/dev/null || true
    elif [[ -n "${EDGE_PID}" ]] && ! kill -0 $EDGE_PID 2>/dev/null; then
        wait $EDGE_PID 2>/dev/null || true
        edge_exit=$?
        log_error "🚨 Edge Stargate (PID $EDGE_PID) exited first with code $edge_exit"
        log_info "Gateway/Relay still running, killing..."
        kill -TERM $GATEWAY_PID $STARGATE_PID 2>/dev/null || true
    else
        log_error "🚨 wait returned but processes still running? exit_code=$exit_code"
    fi
    
    log_info "🔍 DIAGNOSTIC: Container exiting (wait returned)"
}

show_status() {
    log_info "Service Status Check"
    log_info "===================="
    
    # Check if processes are running
    local gateway_running=false
    local stargate_running=false
    
    if pgrep -f "uvicorn.*src.main:app.*${GATEWAY_PORT}" > /dev/null; then
        gateway_running=true
    fi
    
    if pgrep -f "uvicorn.*systems.proxy.app:app.*${STARGATE_PORT}" > /dev/null; then
        stargate_running=true
    fi
    
    # Display status
    if [[ "${gateway_running}" == "true" ]]; then
        log_info "Gateway: RUNNING (port ${GATEWAY_PORT})"
    else
        log_info "Gateway: NOT RUNNING"
    fi
    
    if [[ "${stargate_running}" == "true" ]]; then
        log_info "Stargate: RUNNING (port ${STARGATE_PORT})"
    else
        log_info "Stargate: NOT RUNNING"
    fi
    
    # Check connectivity
    if command -v curl > /dev/null 2>&1; then
        log_info "Health Check Results:"
        
        if curl -sf "http://localhost:${GATEWAY_PORT}/health" > /dev/null 2>&1; then
            log_info "  Gateway health: OK"
        else
            log_info "  Gateway health: FAILED"
        fi
        
        if curl -sf "http://localhost:${STARGATE_PORT}/health" > /dev/null 2>&1; then
            log_info "  Stargate health: OK"
        else
            log_info "  Stargate health: FAILED"
        fi
    fi
}

# ============================================================================
# Main Entry Point
# ============================================================================

main() {
    local command="${1:-}"
    
    if [[ -z "${command}" ]]; then
        log_error "Usage: $0 {gateway|stargate|both|status}"
        exit 1
    fi
    
    # Validate environment before starting services
    if [[ "${command}" != "status" ]]; then
        validate_environment || exit 1
    fi
    
    case "${command}" in
        gateway)
            start_gateway
            ;;
        stargate)
            start_stargate
            ;;
        both)
            start_both
            ;;
        status)
            show_status
            ;;
        *)
            log_error "Unknown command: ${command}"
            log_error "Usage: $0 {gateway|stargate|both|status}"
            exit 1
            ;;
    esac
}

# Run main if not sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi