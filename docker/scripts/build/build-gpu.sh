#!/usr/bin/env bash
# Build GPU-enabled Docker image (multi-stage)
#
# Uses independent builder stages for vLLM, llama-cpp-python, and llama-server.
# Build scripts: libs/inference_djinn/scripts/build/python_builders/
#
# ──────────────────────────────────────────────────────────────────────
# Multi-Stage Build Architecture
# ──────────────────────────────────────────────────────────────────────
# Dockerfile.gpu has 4 builder stages + 1 runtime stage:
#
#   base-builder ─┬─ vllm-builder ──────────┐
#                 ├─ llama-builder ──────────┤
#                 └─ llama-server-builder ───┤
#                                           ▼
#                                        runtime
#
# Docker --build-arg is GLOBAL: it's passed to ALL stages.
# But each stage only declares the ARGs it consumes.
# Cache invalidation happens per-stage based on THAT stage's ARGs:
#
#   --build-arg LLAMA_SERVER_VERSION=b7951
#   → Only invalidates llama-server-builder
#   → vllm-builder and llama-builder caches preserved
#
#   --build-arg GPU_ARCH=120
#   → Invalidates llama-builder AND llama-server-builder (both use GPU_ARCH)
#   → vllm-builder cache preserved
#
#   --build-arg VLLM_VERSION=v0.15.0
#   → Only invalidates vllm-builder
#   → llama-builder and llama-server-builder caches preserved
#
# ──────────────────────────────────────────────────────────────────────
# BUILD_SCOPE Integration
# ──────────────────────────────────────────────────────────────────────
# When called with BUILD_SCOPE=llama:
#   - --no-vllm is passed, setting ENABLE_VLLM=false
#   - vllm-builder produces an empty package directory (instant)
#   - Only llama-builder and llama-server-builder do real work
#
# Example: BUILD_SCOPE=llama ./docker/scripts/build/build-gpu.sh
#          BUILD_SCOPE=all  ./docker/scripts/build/build-gpu.sh   # default
#          TUI: ./manage → Services → Build Image

# ──────────────────────────────────────────────────────────────────────
# CALLER CHAIN
# ──────────────────────────────────────────────────────────────────────
# This script is called by:
#   - ./manage (TUI → Services → Build Image)
#   - Developers directly (./docker/scripts/build/build-gpu.sh [OPTIONS])
#
# It constructs --build-arg flags and invokes:
#   docker build -f docker/dockerfiles/Dockerfile.gpu
#
# Stage → ARG mapping (which --build-arg controls which stage):
#   vllm-builder         ← ENABLE_VLLM, VLLM_VERSION, VLLM_FROM_SOURCE,
#                          VLLM_BUILD_ARGS, VLLM_MAX_JOBS, TORCH_NIGHTLY_DATE
#   llama-builder        ← CPU_OPTIMIZATION, GPU_ARCH, LLAMA_CPP_PYTHON_VERSION
#   llama-server-builder ← CPU_OPTIMIZATION, GPU_ARCH, ENABLE_LLAMA_SERVER,
#                          LLAMA_SERVER_VERSION
#   runtime              ← ENABLE_VLLM (conflict resolution),
#                          CPU_OPTIMIZATION, GPU_ARCH, CUDA_VERSION (labels only)
# ──────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────
# COMPONENT SELECTION FLAGS
# ──────────────────────────────────────────────────────────────────────
# Controls which inference backends are built:
#   --vllm / --no-vllm           → ENABLE_VLLM (default: true)
#   --llama-server / --no-llama-server → ENABLE_LLAMA_SERVER (default: true)
#   --llama-cpp-python / --no-llama-cpp-python → ENABLE_LLAMA_CPP_PYTHON (default: false)
#
# llama-cpp-python is disabled by default; use llama-server for GGUF.
# Pass --with-llama-cpp-python to build the Python bindings.
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
BUILD_SCRIPT="${PROJECT_ROOT}/libs/inference_djinn/scripts/build/python_builders/llama_cpp/build_llama_cpp.py"

# Configuration
IMAGE_NAME="${IMAGE_NAME:-universal-llm-gateway}"
IMAGE_TAG="${IMAGE_TAG:-gpu}"
CUDA_VERSION="${CUDA_VERSION:-13.0.0}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
# Default: AVX2 for portable Docker images (works on ~95% of servers)
CPU_OPTIMIZATION="${CPU_OPTIMIZATION:-avx2}"
# Default: multi-arch GPU build for portability (set to specific arch like "120" for native)
GPU_ARCH="${GPU_ARCH:-multi}"
# Default: vLLM enabled (set to false for faster builds during development)
ENABLE_VLLM="${ENABLE_VLLM:-true}"
# Default: Build vLLM from source for optimal performance
# Provides latest PyTorch 2.11 nightly with CUDA 13.0 optimizations
# Critical for Blackwell GB300: FlashAttention v3, FP8, improved CUDA graphs
# Set to false for faster deployment with pre-built wheel (uses older PyTorch 2.9)
VLLM_FROM_SOURCE="${VLLM_FROM_SOURCE:-true}"
# Default: vLLM portable build (AVX2 + multi-arch GPU)
# Override for native optimization: VLLM_BUILD_ARGS="--gpu-arch=120"
VLLM_BUILD_ARGS="${VLLM_BUILD_ARGS:---portable}"
# Additional vLLM build flags (e.g., --no-patches)
VLLM_EXTRA_FLAGS="${VLLM_EXTRA_FLAGS:-}"
# Default: Conservative job count for vLLM (memory-intensive CUDA compilation)
# Each job uses ~3-6GB RAM, so limit to avoid OOM
VLLM_MAX_JOBS="${VLLM_MAX_JOBS:-8}"
# Default: Use latest llama-cpp-python from main (empty = latest)
# Override: --llama-cpp-python-version=<hash/tag> for specific version
# Example: --llama-cpp-python-version=ce6fd8b (Aug 14, 2025)
# Example: --llama-cpp-python-version=v0.3.8 (tag)
LLAMA_CPP_PYTHON_VERSION="${LLAMA_CPP_PYTHON_VERSION:-}"


ENABLE_LLAMA_CPP_PYTHON="${ENABLE_LLAMA_CPP_PYTHON:-false}"
# Default: Build native llama-server for native integration
ENABLE_LLAMA_SERVER="${ENABLE_LLAMA_SERVER:-true}"
# Default: Use latest llama.cpp main branch
LLAMA_SERVER_VERSION="${LLAMA_SERVER_VERSION:-b8369}"

# Default: Use latest vLLM release tag (empty = latest release)
# Override: --vllm-version=<hash/tag> for specific version
# Example: --vllm-version=v0.12.0 (release tag)
# Example: --vllm-version=abc123def (commit hash)
# Example: --vllm-version=main (bleeding edge)
VLLM_VERSION="${VLLM_VERSION:-}"

# Default: Pin PyTorch nightly to known-working version (avoids broken nightly deps).
# The Dockerfile falls back to the latest cu130 nightly automatically if this
# pin is removed from the index.
# Set to "latest" to always pull the newest nightly (may fail if deps are broken).
# Format: YYYYMMDD (e.g., 20260315)
TORCH_NIGHTLY_DATE="${TORCH_NIGHTLY_DATE:-20260315}"

# Source cache-busting: empty = rely on Docker's content checksum (default).
# Set to any value (e.g. timestamp) to force re-COPY of libs/ + services/.
SOURCE_VERSION=""

# Default: Build readable images (set to true for obfuscated production builds)
OBFUSCATE="false"

# Parse arguments
NO_CACHE=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-cache)
            NO_CACHE="--no-cache"
            shift
            ;;
        --cpu-native)
            CPU_OPTIMIZATION="native"
            shift
            ;;
        --cpu-avx512)
            CPU_OPTIMIZATION="avx512"
            shift
            ;;
        --cpu-avx2)
            CPU_OPTIMIZATION="avx2"
            shift
            ;;
        --cpu-generic)
            CPU_OPTIMIZATION="generic"
            shift
            ;;
        --gpu-arch=*)
            GPU_ARCH="${1#*=}"
            # Set flag to rebuild VLLM_BUILD_ARGS after all args are parsed
            _VLLM_GPU_OVERRIDE="${GPU_ARCH}"
            shift
            ;;
        --gpu-native)
            # Auto-detect GPU and use single architecture for BOTH llama-cpp-python AND vLLM
            GPU_ARCH=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d '.' || echo "multi")
            if [[ "${GPU_ARCH}" == "multi" || -z "${GPU_ARCH}" ]]; then
                echo "⚠️  Could not detect GPU architecture, using multi-arch build"
                GPU_ARCH="multi"
            else
                echo "🎯 Detected GPU compute capability: ${GPU_ARCH}"
                echo "   Both llama-cpp-python AND vLLM will target SM_${GPU_ARCH} exclusively"
                _VLLM_GPU_OVERRIDE="${GPU_ARCH}"
            fi
            shift
            ;;
        --no-vllm)
            ENABLE_VLLM="false"
            shift
            ;;
        --with-vllm)
            ENABLE_VLLM="true"
            shift
            ;;
        --vllm-source)
            VLLM_FROM_SOURCE="true"  # Explicit (already default)
            shift
            ;;
        --vllm-wheel)
            # Use pre-built wheel (older PyTorch 2.9, faster deployment)
            VLLM_FROM_SOURCE="false"
            shift
            ;;
        --vllm-jobs=*)
            VLLM_MAX_JOBS="${1#*=}"
            shift
            ;;
        --llama-cpp-python-version=*)
            LLAMA_CPP_PYTHON_VERSION="${1#*=}"
            shift
            ;;
        --vllm-version=*)
            VLLM_VERSION="${1#*=}"
            shift
            ;;
        --torch-nightly-date=*)
            TORCH_NIGHTLY_DATE="${1#*=}"
            shift
            ;;
        --torch-nightly-latest)
            TORCH_NIGHTLY_DATE="latest"
            shift
            ;;
        --no-llama-cpp-python)
            ENABLE_LLAMA_CPP_PYTHON="false"
            shift
            ;;
        --with-llama-cpp-python)
            ENABLE_LLAMA_CPP_PYTHON="true"
            shift
            ;;
        --no-llama-server)
            ENABLE_LLAMA_SERVER="false"
            shift
            ;;
        --with-llama-server)
            ENABLE_LLAMA_SERVER="true"
            shift
            ;;
        --llama-server-version=*)
            LLAMA_SERVER_VERSION="${1#*=}"
            shift
            ;;
        --refresh-source)
            SOURCE_VERSION="$(date +%s)"
            shift
            ;;
        --obfuscate)
            OBFUSCATE="true"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --no-cache          Force rebuild without cache"
            echo "  --refresh-source    Bust cache for source COPY (libs/, services/, config/)"
            echo "  --obfuscate         Build obfuscated image (PyArmor, production)"
            echo ""
            echo "vLLM Options:"
            echo "  --no-vllm           Skip vLLM installation (fastest builds)"
            echo "  --with-vllm         Include vLLM via pre-built wheel (default)"
            echo "  --vllm-source       Build vLLM from source (default, required for CUDA 13.0)"
            echo "  --vllm-wheel        Use pre-built wheel (faster, requires CUDA 12.x)"
            echo "  --vllm-jobs=N       Max parallel jobs for source build (default: 8)"
            echo ""
            echo "llama-cpp-python Options (disabled by default):"
            echo "  --no-llama-cpp-python            Skip llama-cpp-python (default)"
            echo "  --with-llama-cpp-python          Build llama-cpp-python Python bindings"
            echo "  --llama-cpp-python-version=HASH/TAG  Pin version (default: latest)"
            echo "                                        Example: ce6fd8b (Aug 14, 2025) or v0.3.8"
            echo ""
            echo "  --vllm-version=HASH/TAG      Pin vLLM version (default: latest release)"
            echo ""
            echo "PyTorch Nightly Options:"
            echo "  --torch-nightly-date=YYYYMMDD  Pin to specific nightly (default: 20260111)"
            echo "  --torch-nightly-latest         Use latest nightly (may fail if deps broken)"
            echo ""
            echo "llama-server Options:"
            echo "  --no-llama-server               Skip llama-server build"
            echo "  --with-llama-server             Build llama-server (default)"
            echo "  --llama-server-version=REF      Pin version (default: main)"
            echo "                                    Example: b7951 (release tag)"
            echo "                                    Example: abc123 (commit hash)"
            echo ""
            echo "CPU Optimization (default: avx2 for portable images):"
            echo "  --cpu-native   True native (-march=native) - MAXIMUM performance for build machine"
            echo "  --cpu-avx2     AVX2 (x86-64-v3) - 2-3x faster, Intel 2013+/AMD 2015+"
            echo "  --cpu-avx512   AVX-512 (x86-64-v4) - 4-6x faster, Intel 2019+/AMD 2022+"
            echo "  --cpu-generic  Generic (x86-64) - maximum portability, slowest"
            echo ""
            echo "GPU Optimization (default: multi-arch for portable images):"
            echo "  --gpu-native       Auto-detect GPU and build for single architecture"
            echo "  --gpu-arch=CODE    Build for specific GPU (e.g., --gpu-arch=120 for RTX 5090)"
            echo ""
            echo "Environment variables:"
            echo "  CPU_OPTIMIZATION=native|avx2|avx512|generic"
            echo "  GPU_ARCH=multi|120|89|90|..."
            echo "  ENABLE_VLLM=true|false"
            echo "  VLLM_FROM_SOURCE=true|false (default: true, source build)"
            echo "  VLLM_BUILD_ARGS='--portable' (source build args)"
            echo "  VLLM_MAX_JOBS=8 (source build parallelism)"
            echo "  ENABLE_LLAMA_CPP_PYTHON=true|false (default: false)"
            echo "  LLAMA_CPP_PYTHON_VERSION=hash/tag (default: empty = latest)"
            echo "  VLLM_VERSION=hash/tag (default: empty = latest release)"
            echo "  TORCH_NIGHTLY_DATE=YYYYMMDD|latest (default: 20260315)"
            echo "  ENABLE_LLAMA_SERVER=true|false (default: true)"
            echo "  LLAMA_SERVER_VERSION=ref (default: b8369)"
            echo "  IMAGE_NAME=universal-llm-gateway"
            echo "  IMAGE_TAG=gpu"
            exit 1
            ;;
    esac
done

# Construct VLLM_BUILD_ARGS based on CPU and GPU optimization settings
# Map CPU_OPTIMIZATION to vLLM CPU flags and add GPU arch if specified
_build_vllm_args() {
    local cpu_flag=""
    local gpu_flag=""
    
    # Map CPU_OPTIMIZATION to vLLM flags
    case "${CPU_OPTIMIZATION}" in
        native)
            cpu_flag="--cpu-native"
            ;;
        avx512)
            # vLLM doesn't have --cpu-avx512, use native on AVX-512 capable machines
            cpu_flag="--cpu-native"
            ;;
        avx2)
            cpu_flag="--cpu-avx2"
            ;;
        generic)
            cpu_flag="--cpu-generic"
            ;;
        *)
            cpu_flag="--cpu-avx2"  # Safe default
            ;;
    esac
    
    # Add GPU arch if specified
    if [[ -n "${_VLLM_GPU_OVERRIDE:-}" ]]; then
        gpu_flag="--gpu-arch=${_VLLM_GPU_OVERRIDE}"
    elif [[ "${GPU_ARCH}" == "multi" ]]; then
        # Multi-arch build: use --gpu-generic for vLLM
        gpu_flag="--gpu-generic"
    fi
    
    # Combine flags - always respect CPU optimization setting
    if [[ -n "${gpu_flag}" ]]; then
        echo "${cpu_flag} ${gpu_flag}"
    else
        # No GPU override - use CPU flag alone
        echo "${cpu_flag}"
    fi
}

# Build VLLM_BUILD_ARGS based on CPU/GPU optimization settings
# Always rebuild if default --portable, or if any optimization is specified
if [[ "${VLLM_BUILD_ARGS}" == "--portable" ]] || [[ -n "${_VLLM_GPU_OVERRIDE:-}" ]]; then
    VLLM_BUILD_ARGS="$(_build_vllm_args)"
fi

# Append extra flags (e.g., --no-patches for clean builds)
if [[ -n "${VLLM_EXTRA_FLAGS:-}" ]]; then
    VLLM_BUILD_ARGS="${VLLM_BUILD_ARGS} ${VLLM_EXTRA_FLAGS}"
fi

echo "Building GPU-enabled Docker image..."
echo "  Image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  CUDA Version: ${CUDA_VERSION}"
echo "  Python Version: ${PYTHON_VERSION}"
echo "  CPU Optimization: ${CPU_OPTIMIZATION}"
echo "  GPU Architecture: ${GPU_ARCH}"
echo "  vLLM: ${ENABLE_VLLM}"
if [[ "${ENABLE_VLLM}" == "true" ]]; then
    if [[ "${VLLM_FROM_SOURCE}" == "true" ]]; then
        echo "  vLLM Method: SOURCE BUILD (${VLLM_BUILD_ARGS}, ${VLLM_MAX_JOBS} jobs)"
    else
        echo "  vLLM Method: PRE-BUILT WHEEL (CUDA 13.0, fast deployment)"
    fi
fi
echo "  llama-cpp-python: ${ENABLE_LLAMA_CPP_PYTHON}"
if [[ "${ENABLE_LLAMA_CPP_PYTHON}" == "true" ]]; then
    if [ -z "${LLAMA_CPP_PYTHON_VERSION}" ]; then
        echo "    (version: latest from main)"
    else
        echo "    (version: ${LLAMA_CPP_PYTHON_VERSION})"
    fi
fi
if [ -z "${VLLM_VERSION}" ]; then
    echo "  vLLM: latest release tag"
elif [ "${VLLM_VERSION}" = "main" ]; then
    echo "  vLLM: main branch (bleeding edge)"
else
    echo "  vLLM: pinned to ${VLLM_VERSION}"
fi
echo "  llama-server: ${ENABLE_LLAMA_SERVER}"
if [[ "${ENABLE_LLAMA_SERVER}" == "true" ]]; then
    echo "  llama-server version: ${LLAMA_SERVER_VERSION}"
fi
if [[ -n "${NO_CACHE}" ]]; then
    echo "  Cache: DISABLED (forced rebuild)"
fi
if [[ -n "${SOURCE_VERSION}" ]]; then
    echo "  Source refresh: YES (SOURCE_VERSION=${SOURCE_VERSION})"
fi
echo ""

# Display CPU optimization info
echo "🔧 Build configuration (unified build_llama_cpp.py)"
    case "${CPU_OPTIMIZATION}" in
    native)
        echo "🚀 CPU Optimization: Native (-march=native -mtune=native)"
        echo "   - MAXIMUM performance for build machine"
        echo "   - Aggressive optimizations: -funroll-loops, -falign-functions=32, etc."
        echo "   - ⚠️  Binary will ONLY work on this exact CPU or compatible"
        ;;
    avx512)
        echo "🚀 CPU Optimization: AVX-512 (x86-64-v4)"
        echo "   - 4-6x faster hybrid GPU+CPU inference"
        echo "   - Requires: Intel Ice Lake+ (2019+), AMD Zen 4+ (2022+)"
        echo "   - ⚠️  NOTE: Intel 12th-14th gen CONSUMER CPUs do NOT support AVX-512!"
        ;;
    avx2)
        echo "⚡ CPU Optimization: AVX2 (x86-64-v3)"
        echo "   - 2-3x faster hybrid GPU+CPU inference"
        echo "   - Requires: Intel Haswell+ (2013+), AMD Excavator+ (2015+)"
        echo "   - ✅ RECOMMENDED: Works on ~95% of servers"
        ;;
    generic)
            echo "📦 CPU Optimization: Generic (x86-64)"
            echo "   - Maximum portability"
            echo "   - Slower hybrid inference (no SIMD optimizations)"
            ;;
        *)
            echo "⚠️  WARNING: Unknown CPU_OPTIMIZATION value: ${CPU_OPTIMIZATION}"
        echo "   Valid values: native, avx512, avx2, generic"
            echo "   Defaulting to: avx2"
            CPU_OPTIMIZATION="avx2"
            ;;
    esac
if [[ "${GPU_ARCH}" == "multi" ]]; then
    echo "🎮 GPU Architecture: Multi-arch (portable)"
    echo "   - Targets: Ampere (80,86,87), Ada (89), Hopper (90), Blackwell (120)"
else
    echo "🎮 GPU Architecture: ${GPU_ARCH} (native single-arch)"
    echo "   - Optimized for SM_${GPU_ARCH} exclusively"
fi
    echo ""

# Export build args for docker-compose to reuse (enables cache reuse)
# This allows docker-compose to use the same build args and leverage cache
# while still rebuilding COPY layers when source files change
export CPU_OPTIMIZATION
export GPU_ARCH
export ENABLE_VLLM
export VLLM_FROM_SOURCE
export VLLM_BUILD_ARGS
export VLLM_MAX_JOBS
export LLAMA_CPP_PYTHON_VERSION
export VLLM_VERSION
export TORCH_NIGHTLY_DATE
export ENABLE_LLAMA_CPP_PYTHON
export ENABLE_LLAMA_SERVER
export LLAMA_SERVER_VERSION

cd "${PROJECT_ROOT}"

BUILD_LOG="/tmp/gateway-build-$(date +%Y%m%d-%H%M%S).log"

# Always build base image (tagged for potential obfuscation consumption)
echo "🔨 Building base image..."
echo "📋 Build log: ${BUILD_LOG}"
docker build \
    --progress=plain \
    ${NO_CACHE} \
    --build-arg CUDA_VERSION="${CUDA_VERSION}" \
    --build-arg PYTHON_VERSION="${PYTHON_VERSION}" \
    --build-arg CPU_OPTIMIZATION="${CPU_OPTIMIZATION}" \
    --build-arg GPU_ARCH="${GPU_ARCH}" \
    --build-arg ENABLE_VLLM="${ENABLE_VLLM}" \
    --build-arg VLLM_FROM_SOURCE="${VLLM_FROM_SOURCE}" \
    --build-arg VLLM_BUILD_ARGS="${VLLM_BUILD_ARGS}" \
    --build-arg VLLM_MAX_JOBS="${VLLM_MAX_JOBS}" \
    --build-arg ENABLE_LLAMA_CPP_PYTHON="${ENABLE_LLAMA_CPP_PYTHON}" \
    --build-arg LLAMA_CPP_PYTHON_VERSION="${LLAMA_CPP_PYTHON_VERSION}" \
    --build-arg VLLM_VERSION="${VLLM_VERSION}" \
    --build-arg TORCH_NIGHTLY_DATE="${TORCH_NIGHTLY_DATE}" \
    --build-arg ENABLE_LLAMA_SERVER="${ENABLE_LLAMA_SERVER}" \
    --build-arg LLAMA_SERVER_VERSION="${LLAMA_SERVER_VERSION}" \
    ${SOURCE_VERSION:+--build-arg SOURCE_VERSION="${SOURCE_VERSION}"} \
    -f docker/dockerfiles/Dockerfile.gpu \
    -t gateway-base:runtime \
    . > "${BUILD_LOG}" 2>&1

if [[ "${OBFUSCATE}" == "true" ]]; then
    echo ""
    echo "🔒 Building obfuscated image (PyArmor)..."
    
    # Create clean build context with only git-tracked files
    BUILD_CONTEXT=$(mktemp -d)
    trap "rm -rf ${BUILD_CONTEXT}" EXIT
    
    echo "📦 Exporting git-tracked files to clean build context..."
    git archive HEAD | tar -x -C "${BUILD_CONTEXT}"
    
    # Copy PyArmor license file to build context
    if ls pyarmor-regfile-*.zip 1> /dev/null 2>&1; then
        cp pyarmor-regfile-*.zip "${BUILD_CONTEXT}/"
    else
        echo "⚠️  Warning: No PyArmor license file found - build may fail"
    fi
    
    # Copy Dockerfiles (they may be in .dockerignore)
    cp docker/dockerfiles/Dockerfile.obfuscated "${BUILD_CONTEXT}/docker/dockerfiles/"
    
    docker build \
        --progress=plain \
        ${NO_CACHE} \
        --build-arg PYTHON_VERSION="${PYTHON_VERSION}" \
        --build-arg CUDA_VERSION="${CUDA_VERSION}" \
        --build-arg CPU_OPTIMIZATION="${CPU_OPTIMIZATION}" \
        --build-arg GPU_ARCH="${GPU_ARCH}" \
        --build-arg ENABLE_VLLM="${ENABLE_VLLM}" \
        --build-arg VLLM_FROM_SOURCE="${VLLM_FROM_SOURCE}" \
        --build-arg VLLM_BUILD_ARGS="${VLLM_BUILD_ARGS}" \
        --build-arg VLLM_MAX_JOBS="${VLLM_MAX_JOBS}" \
        --build-arg ENABLE_LLAMA_CPP_PYTHON="${ENABLE_LLAMA_CPP_PYTHON}" \
        --build-arg LLAMA_CPP_PYTHON_VERSION="${LLAMA_CPP_PYTHON_VERSION}" \
        --build-arg VLLM_VERSION="${VLLM_VERSION}" \
        --build-arg TORCH_NIGHTLY_DATE="${TORCH_NIGHTLY_DATE}" \
        --build-arg ENABLE_LLAMA_SERVER="${ENABLE_LLAMA_SERVER}" \
        --build-arg LLAMA_SERVER_VERSION="${LLAMA_SERVER_VERSION}" \
        ${SOURCE_VERSION:+--build-arg SOURCE_VERSION="${SOURCE_VERSION}"} \
        -f "${BUILD_CONTEXT}/docker/dockerfiles/Dockerfile.obfuscated" \
        -t "${IMAGE_NAME}:${IMAGE_TAG}" \
        "${BUILD_CONTEXT}" >> "${BUILD_LOG}" 2>&1
    
    DEPLOYMENT_TYPE="obfuscated"
else
    # Non-obfuscated: tag base as final image
    docker tag gateway-base:runtime "${IMAGE_NAME}:${IMAGE_TAG}"
    
    DEPLOYMENT_TYPE="readable"
fi

echo ""
echo "✅ Build complete!"
echo ""
echo "Deployment type: ${DEPLOYMENT_TYPE}"
echo ""
echo "Image tag:"
echo "  - ${IMAGE_NAME}:${IMAGE_TAG}"
echo ""
echo "Build configuration:"
echo "  - Deployment: ${DEPLOYMENT_TYPE}"
echo "  - CUDA Version: ${CUDA_VERSION}"
echo "  - Python Version: ${PYTHON_VERSION}"
echo "  - CPU Optimization: ${CPU_OPTIMIZATION}"
echo "  - GPU Architecture: ${GPU_ARCH}"
echo "  - vLLM: ${ENABLE_VLLM}"
if [[ "${ENABLE_VLLM}" == "true" ]]; then
    if [[ "${VLLM_FROM_SOURCE}" == "true" ]]; then
        echo "  - vLLM Method: SOURCE BUILD (${VLLM_BUILD_ARGS}, ${VLLM_MAX_JOBS} jobs)"
    else
        echo "  - vLLM Method: PRE-BUILT WHEEL (CUDA 13.0, fast deployment)"
    fi
fi
echo "  - llama-cpp-python: ${ENABLE_LLAMA_CPP_PYTHON}"
if [[ "${ENABLE_LLAMA_CPP_PYTHON}" == "true" ]] && [ -n "${LLAMA_CPP_PYTHON_VERSION}" ]; then
    echo "    (pinned: ${LLAMA_CPP_PYTHON_VERSION})"
fi
if [ -z "${VLLM_VERSION}" ]; then
    echo "  - vLLM: latest release (stable)"
elif [ "${VLLM_VERSION}" = "main" ]; then
    echo "  - vLLM: main branch (bleeding edge)"
else
    echo "  - vLLM: pinned to ${VLLM_VERSION}"
fi
echo ""
echo "Backend support:"
if [[ "${ENABLE_LLAMA_CPP_PYTHON}" == "true" ]]; then
    echo "  - llama-cpp-python (GGUF models)"
fi
if [[ "${ENABLE_LLAMA_SERVER}" == "true" ]]; then
    echo "  - llama-server (GGUF models)"
fi
if [[ "${ENABLE_VLLM}" == "true" ]]; then
    echo "  - vLLM 0.11.2+ (safetensors/HuggingFace models)"
fi
echo "  - PyTorch nightly with CUDA 13.0"
echo ""
echo "Performance (hybrid GPU+CPU inference):"
case "${CPU_OPTIMIZATION}" in
    avx512)
        echo "  ⚡ 4-6x faster than generic (AVX-512 VNNI enabled)"
        ;;
    avx2)
        echo "  ⚡ 2-3x faster than generic (AVX2 + FMA enabled)"
        ;;
    generic)
        echo "  ⚠️  Baseline performance (no SIMD optimizations)"
        ;;
esac
echo ""
echo "Next steps:"
echo "  1. Test: docker/test-gpu.sh"
echo "  2. Run: docker compose -f docker/docker-compose.gateway-gpu.yml up"
echo ""
echo "Build options:"
echo "  - Force rebuild: docker/build-gpu.sh --no-cache"
echo "  - Source refresh: docker/build-gpu.sh --refresh-source  (fast, re-copies source only)"
echo "  - Use pre-built wheel: VLLM_FROM_SOURCE=false docker/build-gpu.sh"
echo "  - Obfuscated (production): docker/build-gpu.sh --obfuscate"
echo "  - No vLLM (fastest): docker/build-gpu.sh --no-vllm"
echo "  - AVX-512 build: docker/build-gpu.sh --cpu-avx512"
echo "  - Native CPU build: docker/build-gpu.sh --cpu-native"
echo "  - Single GPU arch: docker/build-gpu.sh --gpu-arch=120  (RTX 5090)"
echo "  - Native GPU build: docker/build-gpu.sh --gpu-native"
echo "  - Full native: docker/build-gpu.sh --cpu-native --gpu-native"
echo "  - Obfuscated native: docker/build-gpu.sh --obfuscate --cpu-native --gpu-native"

