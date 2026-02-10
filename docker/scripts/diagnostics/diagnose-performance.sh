#!/usr/bin/env bash
# docker/diagnose-performance.sh
# Diagnose Docker vs bare metal performance gap for hybrid GPU+CPU inference

set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-universal-gateway-gpu}"
IMAGE_NAME="${IMAGE_NAME:-universal-llm-gateway:gpu}"

echo "=========================================="
echo "Docker Performance Diagnostics"
echo "=========================================="
echo ""

# Check if container is running
if ! docker ps | grep -q "$CONTAINER_NAME"; then
    echo "⚠️  Container '$CONTAINER_NAME' is not running"
    echo "   Start it with: docker compose -f docker/docker-compose.gateway-gpu.yml up -d"
    echo ""
    echo "Checking image configuration instead..."
    CONTAINER_RUNNING=false
else
    CONTAINER_RUNNING=true
fi

echo "1. Checking AVX-512 Compilation..."
echo "-----------------------------------"

# Check build configuration
CPU_OPT=$(docker inspect "$IMAGE_NAME" 2>/dev/null | grep -A 1 '"cpu.optimization"' | grep Value | cut -d'"' -f4 || echo "unknown")
echo "   Build CPU optimization: $CPU_OPT"

if [ "$CONTAINER_RUNNING" = true ]; then
    echo ""
    echo "2. Checking Runtime AVX-512 Detection..."
    echo "-----------------------------------"
    
    # Check if AVX-512 is actually enabled at runtime
    docker exec "$CONTAINER_NAME" python3 -c "
from llama_cpp import llama_cpp
info = llama_cpp.llama_print_system_info()
info_str = info.decode() if hasattr(info, 'decode') else str(info)
print(info_str)
" 2>&1 | tee /tmp/llama_sysinfo.txt
    
    echo ""
    if grep -q "AVX512.*1" /tmp/llama_sysinfo.txt; then
        echo "   ✅ AVX-512 is ENABLED"
    else
        echo "   ❌ AVX-512 is NOT enabled (this is the problem!)"
        echo "   Rebuild with: docker/build-gpu.sh --no-cache"
        exit 1
    fi
    
    if grep -q "AVX512_VNNI.*1" /tmp/llama_sysinfo.txt; then
        echo "   ✅ AVX-512 VNNI is ENABLED (critical for quantized models)"
    else
        echo "   ⚠️  AVX-512 VNNI not detected"
    fi
    
    echo ""
    echo "3. Checking CPU/Thread Configuration..."
    echo "-----------------------------------"
    
    # Check visible cores
    CORES=$(docker exec "$CONTAINER_NAME" grep -c ^processor /proc/cpuinfo)
    HOST_CORES=$(grep -c ^processor /proc/cpuinfo)
    echo "   Cores visible to container: $CORES"
    echo "   Cores on host: $HOST_CORES"
    
    if [ "$CORES" -ne "$HOST_CORES" ]; then
        echo "   ⚠️  Container has limited CPU access (cpuset restriction)"
    else
        echo "   ✅ Container can see all host CPUs"
    fi
    
    # Check thread environment variables
    echo ""
    echo "   Thread configuration:"
    docker exec "$CONTAINER_NAME" bash -c 'echo "   OMP_NUM_THREADS: ${OMP_NUM_THREADS:-not set}"'
    docker exec "$CONTAINER_NAME" bash -c 'echo "   OPENBLAS_NUM_THREADS: ${OPENBLAS_NUM_THREADS:-not set}"'
    docker exec "$CONTAINER_NAME" bash -c 'echo "   MKL_NUM_THREADS: ${MKL_NUM_THREADS:-not set}"'
    
    echo ""
    echo "4. Checking CPU Quota/Limits..."
    echo "-----------------------------------"
    
    CPU_QUOTA=$(docker exec "$CONTAINER_NAME" cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null || echo "-1")
    if [ "$CPU_QUOTA" = "-1" ]; then
        echo "   ✅ No CPU quota limit (unlimited)"
    else
        CPU_PERIOD=$(docker exec "$CONTAINER_NAME" cat /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>/dev/null || echo "100000")
        CPU_LIMIT=$(echo "scale=2; $CPU_QUOTA / $CPU_PERIOD" | bc)
        echo "   ⚠️  CPU quota limited to ${CPU_LIMIT} cores"
    fi
    
    # Check memory limits
    MEM_LIMIT=$(docker inspect "$CONTAINER_NAME" 2>/dev/null | grep -A 1 '"Memory":' | tail -1 | grep -oE '[0-9]+' || echo "0")
    if [ "$MEM_LIMIT" = "0" ]; then
        echo "   ✅ No memory limit"
    else
        MEM_LIMIT_GB=$(echo "scale=2; $MEM_LIMIT / 1024 / 1024 / 1024" | bc)
        echo "   Memory limit: ${MEM_LIMIT_GB} GB"
    fi
    
    echo ""
    echo "5. Checking CPU Frequency (Host)..."
    echo "-----------------------------------"
    
    # Check CPU governor
    GOVERNOR=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "unknown")
    echo "   CPU Governor: $GOVERNOR"
    
    if [ "$GOVERNOR" != "performance" ]; then
        echo "   ⚠️  CPU not in performance mode!"
        echo "   Fix: echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"
    else
        echo "   ✅ CPU in performance mode"
    fi
    
    # Sample CPU frequency
    AVG_FREQ=$(grep MHz /proc/cpuinfo | awk '{sum+=$4; count++} END {printf "%.0f", sum/count}')
    MAX_FREQ=$(cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq 2>/dev/null || echo "unknown")
    if [ "$MAX_FREQ" != "unknown" ]; then
        MAX_FREQ_GHZ=$(echo "scale=2; $MAX_FREQ / 1000000" | bc)
        echo "   Max CPU frequency: ${MAX_FREQ_GHZ} GHz"
    fi
    echo "   Current average: ${AVG_FREQ} MHz"
    
    echo ""
    echo "6. Current Resource Usage..."
    echo "-----------------------------------"
    docker stats "$CONTAINER_NAME" --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"
    
else
    echo ""
    echo "⚠️  Container not running - cannot check runtime configuration"
    echo "   Start with: docker compose -f docker/docker-compose.gateway-gpu.yml up -d"
fi

echo ""
echo "=========================================="
echo "Summary & Recommendations"
echo "=========================================="
echo ""

# Provide recommendations based on findings
if [ "$CONTAINER_RUNNING" = true ]; then
    if ! grep -q "AVX512.*1" /tmp/llama_sysinfo.txt 2>/dev/null; then
        echo "❌ CRITICAL: AVX-512 not enabled - rebuild required"
        echo "   Run: docker/build-gpu.sh --no-cache"
        echo ""
    fi
    
    if [ "$GOVERNOR" != "performance" ]; then
        echo "⚠️  Set CPU governor to performance mode:"
        echo "   echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"
        echo ""
    fi
    
    # Note about thread auto-detection
    if ! docker exec "$CONTAINER_NAME" bash -c '[ -n "${OMP_NUM_THREADS:-}" ]' 2>/dev/null; then
        echo "ℹ️  Thread count: auto-detected by llama-cpp-python (recommended)"
        echo "   Override only if needed: OMP_NUM_THREADS=N docker compose up"
        echo ""
    fi
    
    echo "✅ Quick performance test:"
    echo "   docker exec -it $CONTAINER_NAME python3 -c '"
    echo "   from llama_cpp import Llama"
    echo "   llm = Llama(model_path=\"/golem/models/YOUR_MODEL.gguf\", n_gpu_layers=35)"
    echo "   print(llm(\"Test\", max_tokens=50))'"
else
    echo "ℹ️  Start the container to perform full diagnostics"
fi

echo ""
echo "Full diagnostic guide: tmp/docker-performance-diagnostics.md"
echo ""

