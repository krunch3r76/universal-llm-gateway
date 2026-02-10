#!/usr/bin/env bash
#
# CUDA Health Check for Docker Container
# Tests CUDA availability via multiple methods
# Exit 0 = healthy, Exit 1 = unhealthy

set -u  # Don't use -e, we want to control exit

# Configuration
MAX_CHECK_TIME=30  # Maximum time for all checks (seconds)
TIMEOUT_CMD="timeout"

# Logging helper
log() {
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*" >&2
}

# Main health check
check_cuda_health() {
    local start_time=$(date +%s)
    
    # Check 1: nvidia-smi (basic NVML check)
    log "Running nvidia-smi check..."
    if ! ${TIMEOUT_CMD} 10s nvidia-smi > /dev/null 2>&1; then
        log "❌ HEALTH CHECK FAILED: nvidia-smi failed"
        return 1
    fi
    log "✅ nvidia-smi: OK"
    
    # Check 2: PyTorch CUDA availability
    log "Running PyTorch CUDA check..."
    if ! ${TIMEOUT_CMD} 15s python3 -c 'import torch; assert torch.cuda.is_available(), "CUDA not available"; print(f"CUDA devices: {torch.cuda.device_count()}")' 2>&1 | grep -q "CUDA devices:"; then
        log "❌ HEALTH CHECK FAILED: PyTorch cannot see CUDA"
        return 1
    fi
    log "✅ PyTorch CUDA: OK"
    
    # Check 3: Gateway health endpoint (ensure gateway is responsive)
    log "Running gateway health endpoint check..."
    if ! ${TIMEOUT_CMD} 10s curl -sf http://localhost:9998/health > /dev/null 2>&1; then
        log "⚠️  Gateway health endpoint not responding (may be starting up)"
        # Don't fail on this - gateway might be initializing
    else
        log "✅ Gateway health endpoint: OK"
    fi
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    log "✅ HEALTH CHECK PASSED (${duration}s)"
    
    return 0
}

# Run health check with timeout protection
if ${TIMEOUT_CMD} ${MAX_CHECK_TIME}s bash -c "$(declare -f check_cuda_health); check_cuda_health"; then
    exit 0
else
    log "❌ HEALTH CHECK FAILED OR TIMED OUT"
    exit 1
fi
