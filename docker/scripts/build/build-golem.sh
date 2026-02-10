#!/usr/bin/env bash
# ⚠️ OBSOLETE - DO NOT USE
# This script is obsolete. Use the new two-stage deployment model instead:
# 1. docker build -f Dockerfile.golem-base -t universal-llm-gateway:golem-base .
# 2. ./scripts/build_golem_tarball.sh
# 3. ./scripts/test_golem_container.sh
#
# See: GOLEM_BUILD_GUIDE.md for the new build process
#
# ============================================================================
# OLD: Build CPU-only Docker image for Golem Network deployment

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Configuration
IMAGE_NAME="${IMAGE_NAME:-universal-llm-gateway}"
IMAGE_TAG="${IMAGE_TAG:-golem}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
CPU_OPTIMIZATION="${CPU_OPTIMIZATION:-avx2}"
# Deprecated: USE_VOLUMES flag (immutable variant removed)
# All builds now use mutable variant (models in /app/models)
USE_VOLUMES="false"
# Default: Build readable images (set to true for obfuscated production builds)
OBFUSCATE="false"
# Model selection (for obfuscated builds - models embedded in container)
MODEL_FILE=""       # Single model file path (--model-file)
MODEL_DIR=""        # Directory containing model files (--model-dir)
MODEL_TAG=""        # Custom tag suffix (--model-tag or auto-derived)
# Default: Use stable pinned to (Aug 14, 2025 - known good)
# Override: --pinned-stable-commit=latest for bleeding edge
LLAMA_CPP_PYTHON_VERSION="${LLAMA_CPP_PYTHON_VERSION:-}"

# Parse arguments
NO_CACHE=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-cache)
            NO_CACHE="--no-cache"
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
        --use-volumes)
            USE_VOLUMES="true"
            shift
            ;;
        --mutable)
            USE_VOLUMES="false"
            shift
            ;;
        --obfuscate)
            OBFUSCATE="true"
            shift
            ;;
        --model-file)
            MODEL_FILE="$2"
            shift 2
            ;;
        --model-dir)
            MODEL_DIR="$2"
            shift 2
            ;;
        --model-tag)
            MODEL_TAG="$2"
            shift 2
            ;;
        --pinned-stable-commit=*)
            LLAMA_CPP_PYTHON_VERSION="${1#*=}"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --no-cache       Force rebuild without cache"
            echo ""
            echo "Model Selection (obfuscated builds, copied into container):"
            echo "  --model-file <path>  Single model file to embed (follows symlinks)"
            echo "  --model-dir <path>   Directory of .gguf models to embed (follows symlinks)"
            echo "  --model-tag <name>   Custom tag suffix (auto-derived from model if omitted)"
            echo ""
            echo "Model Storage:"
            echo "  Models stored in /app/models (read-write, in container filesystem)"
            echo "  --use-volumes    [DEPRECATED] Immutable variant removed"
            echo ""
            echo "CPU Optimization (default: avx2 for Golem compatibility):"
            echo "  --cpu-avx2      AVX2 (x86-64-v3) - 2-3x faster, Intel 2013+/AMD 2015+ (RECOMMENDED)"
            echo "  --cpu-avx512    AVX-512 (x86-64-v4) - 4-6x faster, Intel 2019+/AMD 2022+"
            echo "  --cpu-generic   Generic (x86-64) - maximum portability, slowest"
            echo ""
            echo "Obfuscation (requires PyArmor license):"
            echo "  --obfuscate      Build obfuscated CPU-only image (PyArmor, production)"
            echo "                   Works with both --mutable and --use-volumes"
            echo "                   Requires: pyarmor-regfile-*.zip in project root"
            echo "                   Purchase: https://jondy.github.io/paypal/index.html"
            echo ""
            echo "llama-cpp-python Options:"
            echo "  --pinned-stable-commit=HASH  llama-cpp-python commit (default: 4227c9be... Aug 14, 2025)"
            echo "                                Use 'latest' for bleeding edge main branch"
            echo ""
            echo "Examples:"
            echo "  # Build with specific model (auto-tagged)"
            echo "  $0 --obfuscate --model-file /path/to/qwen2-5-coder-14b-q8.gguf"
            echo "  # Result: universal-llm-gateway:golem-mutable-qwen2-5-coder-14b-obfuscated"
            echo ""
            echo "  # Build with custom tag"
            echo "  $0 --obfuscate --model-file /path/to/model.gguf --model-tag my-model"
            echo "  # Result: universal-llm-gateway:golem-mutable-my-model-obfuscated"
            echo ""
            echo "  # Build with directory of models"
            echo "  $0 --obfuscate --model-dir /path/to/models/ --model-tag multi-model"
            echo ""
            echo "Environment variables:"
            echo "  MODEL_FILE=<path>     Single model file (alternative to --model-file)"
            echo "  MODEL_DIR=<path>      Model directory (alternative to --model-dir)"
            echo "  MODEL_TAG=<name>      Tag suffix (alternative to --model-tag)"
            echo "  USE_VOLUMES=false|true (default: false, mutable)"
            echo "  CPU_OPTIMIZATION=avx2|avx512|generic (default: avx2)"
            echo "  PYTHON_VERSION=3.12 (default)"
            echo "  IMAGE_NAME=universal-llm-gateway"
            echo "  IMAGE_TAG=golem-mutable (or golem with --use-volumes)"
            exit 1
            ;;
    esac
done

# Validate mutually exclusive model options
if [[ -n "${MODEL_FILE}" && -n "${MODEL_DIR}" ]]; then
    echo "❌ ERROR: --model-file and --model-dir are mutually exclusive"
    exit 1
fi

# Auto-derive MODEL_TAG from MODEL_FILE if not specified
if [[ -n "${MODEL_FILE}" && -z "${MODEL_TAG}" ]]; then
    # Extract model name: "qwen2-5-coder-14b-instruct-q8-0.gguf" -> "qwen2-5-coder-14b"
    MODEL_TAG=$(basename "${MODEL_FILE}" .gguf | \
        sed -E 's/[-_]?[qQ][0-9]+[-_]?[kKmM]?[-_]?[0-9]*$//' | \
        sed -E 's/[-_]?instruct$//' | \
        tr '[:upper:]' '[:lower:]' | \
        tr ' _' '-' | \
        head -c 40)
    echo "📦 Auto-derived model tag: ${MODEL_TAG}"
fi

# Auto-derive MODEL_TAG from MODEL_DIR if not specified
if [[ -n "${MODEL_DIR}" && -z "${MODEL_TAG}" ]]; then
    MODEL_TAG=$(basename "${MODEL_DIR}" | tr '[:upper:]' '[:lower:]' | tr ' _' '-' | head -c 40)
    echo "📦 Auto-derived model tag from directory: ${MODEL_TAG}"
fi

# Append MODEL_TAG to IMAGE_TAG if set
if [[ -n "${MODEL_TAG}" ]]; then
    IMAGE_TAG="${IMAGE_TAG}-${MODEL_TAG}"
fi

# Dockerfile is now always Dockerfile.golem (mutable variant)
# USE_VOLUMES flag is deprecated but kept for backward compatibility
DOCKERFILE="docker/dockerfiles/Dockerfile.golem"
VARIANT="mutable (/app/models)"
if [[ "${USE_VOLUMES}" == "true" ]]; then
    echo "⚠️  WARNING: --use-volumes is deprecated (immutable variant removed)"
    echo "   Using mutable variant (Dockerfile.golem) instead"
fi

echo "Building CPU-only Docker image for Golem Network..."
echo "  Image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  Variant: ${VARIANT}"
echo "  Python Version: ${PYTHON_VERSION}"
echo "  CPU Optimization: ${CPU_OPTIMIZATION}"
if [[ -n "${NO_CACHE}" ]]; then
    echo "  Cache: DISABLED (forced rebuild)"
fi
if [[ "${OBFUSCATE}" == "true" ]]; then
    echo "  Obfuscation: ENABLED (PyArmor)"
fi
if [ "${LLAMA_CPP_PYTHON_VERSION}" = "latest" ]; then
    echo "  llama-cpp-python: latest main branch (bleeding edge)"
else
    echo "  llama-cpp-python: pinned to ${LLAMA_CPP_PYTHON_VERSION:0:8}... (stable)"
fi
echo ""

# Display CPU optimization info
case "${CPU_OPTIMIZATION}" in
    avx512)
        echo "🚀 CPU Optimization: AVX-512 (x86-64-v4)"
        echo "   - 4-6x faster CPU inference"
        echo "   - Requires: Intel Ice Lake+ (2019+), AMD Zen 4+ (2022+)"
        echo "   - Includes AVX-512 VNNI for quantized models"
        echo "   - ⚠️  Use only for known modern server deployments"
        ;;
    avx2)
        echo "⚡ CPU Optimization: AVX2 (x86-64-v3) [RECOMMENDED for Golem]"
        echo "   - 2-3x faster CPU inference"
        echo "   - Requires: Intel Haswell+ (2013+), AMD Excavator+ (2015+)"
        echo "   - Broader compatibility for unknown provider hardware"
        echo "   - ✅ Default for Golem Network deployment"
        ;;
    generic)
        echo "📦 CPU Optimization: Generic (x86-64)"
        echo "   - Maximum portability"
        echo "   - Slower CPU inference (no SIMD optimizations)"
        echo "   - Use only for very old hardware"
        ;;
    *)
        echo "⚠️  WARNING: Unknown CPU_OPTIMIZATION value: ${CPU_OPTIMIZATION}"
        echo "   Valid values: avx512, avx2, generic"
        echo "   Defaulting to: avx2"
        CPU_OPTIMIZATION="avx2"
        ;;
esac
echo ""

cd "${PROJECT_ROOT}"

# Always build base image (tagged for potential obfuscation consumption)
echo "🔨 Building base image (${VARIANT})..."
docker build \
    ${NO_CACHE} \
    --build-arg PYTHON_VERSION="${PYTHON_VERSION}" \
    --build-arg CPU_OPTIMIZATION="${CPU_OPTIMIZATION}" \
    --build-arg LLAMA_CPP_PYTHON_VERSION="${LLAMA_CPP_PYTHON_VERSION}" \
    -f "${DOCKERFILE}" \
    -t golem-base:runtime \
    .

if [[ "${OBFUSCATE}" == "true" ]]; then
    echo ""
    echo "🔒 Building obfuscated image (PyArmor)..."
    
    # Consolidated obfuscated Dockerfile (replaces obfuscated-cpu + obfuscated-cpu-mutable)
    OBFUSCATED_DOCKERFILE="docker/dockerfiles/Dockerfile.golem-obfuscated"
    
    # All Golem builds are mutable - models added at build time or post-build
    OBFUSCATED_DESC="obfuscated with writable /app/models"
    
    echo "  Using: ${OBFUSCATED_DOCKERFILE} (${OBFUSCATED_DESC})"
    
    # Create clean build context from working directory
    BUILD_CONTEXT=$(mktemp -d)
    trap "rm -rf ${BUILD_CONTEXT}" EXIT
    
    echo "📦 Copying working directory to clean build context..."
    rsync -a \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.pyo' \
        --exclude='.pytest_cache' \
        --exclude='*.egg-info' \
        --exclude='.mypy_cache' \
        --exclude='.venv' \
        --exclude='venv' \
        --exclude='node_modules' \
        --exclude='*.gvmi' \
        --exclude='*.gvmi.hash' \
        --exclude='.tox' \
        --exclude='.coverage' \
        --exclude='htmlcov' \
        --exclude='dist' \
        --exclude='build' \
        . "${BUILD_CONTEXT}/"
    
    # Copy PyArmor license file to build context (may be gitignored)
    if ls pyarmor-regfile-*.zip 1> /dev/null 2>&1; then
        cp pyarmor-regfile-*.zip "${BUILD_CONTEXT}/"
    else
        echo "⚠️  Warning: No PyArmor license file found - build may fail"
    fi
    
    # Copy model files to build context
    # CRITICAL: Always create models/ directory (even if empty) to prevent Docker COPY failure
    mkdir -p "${BUILD_CONTEXT}/models"
    MODELS_COPIED=0
    
    if [[ -n "${MODEL_FILE}" ]]; then
        # Single file mode
        if [[ -e "${MODEL_FILE}" ]]; then
            echo "📦 Copying model file ${MODEL_FILE} to build context..."
            # Use -L to follow symlinks, copy actual file content
            cp -L "${MODEL_FILE}" "${BUILD_CONTEXT}/models/" || {
                echo "❌ ERROR: Failed to copy model file: ${MODEL_FILE}"
                exit 1
            }
            MODELS_COPIED=1
            echo "✅ Model file copied to build context"
        else
            echo "❌ ERROR: Model file not found: ${MODEL_FILE}"
            exit 1
        fi
    elif [[ -n "${MODEL_DIR}" ]]; then
        # Directory mode - copy all .gguf files (follow symlinks)
        if [[ -d "${MODEL_DIR}" ]]; then
            echo "📦 Copying models from directory ${MODEL_DIR}..."
            # Use find to handle symlinks properly, -L follows symlinks
            while IFS= read -r -d '' model; do
                cp -L "${model}" "${BUILD_CONTEXT}/models/" || {
                    echo "⚠️  Warning: Failed to copy ${model}"
                    continue
                }
                MODELS_COPIED=$((MODELS_COPIED + 1))
                echo "   ✅ $(basename "${model}")"
            done < <(find -L "${MODEL_DIR}" -maxdepth 1 -name "*.gguf" -print0)
            
            if [[ ${MODELS_COPIED} -eq 0 ]]; then
                echo "⚠️  Warning: No .gguf files found in ${MODEL_DIR}"
            else
                echo "✅ Copied ${MODELS_COPIED} model file(s) to build context"
            fi
        else
            echo "❌ ERROR: Model directory not found: ${MODEL_DIR}"
            exit 1
        fi
    else
        # No model specified - create empty directory with marker
        echo "ℹ️  No model specified (use --model-file or --model-dir)"
        echo "   Container will have empty /app/models directory"
        touch "${BUILD_CONTEXT}/models/.gitkeep"
    fi
    
    # Copy correct CPU obfuscated Dockerfile
    mkdir -p "${BUILD_CONTEXT}/docker/dockerfiles"
    cp "${OBFUSCATED_DOCKERFILE}" "${BUILD_CONTEXT}/docker/dockerfiles/"
    
    docker build \
        ${NO_CACHE} \
        --build-arg PYTHON_VERSION="${PYTHON_VERSION}" \
        --build-arg CPU_OPTIMIZATION="${CPU_OPTIMIZATION}" \
        --build-arg LLAMA_CPP_PYTHON_VERSION="${LLAMA_CPP_PYTHON_VERSION}" \
        -f "${BUILD_CONTEXT}/docker/dockerfiles/Dockerfile.golem-obfuscated" \
        -t "${IMAGE_NAME}:${IMAGE_TAG}" \
        -t "${IMAGE_NAME}:latest-golem" \
        "${BUILD_CONTEXT}"
    
    # Tag with obfuscation indicator
    docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${IMAGE_NAME}:${IMAGE_TAG}-obfuscated"
    
    DEPLOYMENT_TYPE="obfuscated"
else
    # Non-obfuscated: tag base as final images
    docker tag golem-base:runtime "${IMAGE_NAME}:${IMAGE_TAG}"
    docker tag golem-base:runtime "${IMAGE_NAME}:latest-golem"
    
    DEPLOYMENT_TYPE="readable"
fi

echo ""
echo "✅ Build complete!"
echo ""
echo "Deployment type: ${DEPLOYMENT_TYPE}"
echo ""
echo "Image tags:"
echo "  - ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  - ${IMAGE_NAME}:latest-golem"
if [[ "${OBFUSCATE}" == "true" ]]; then
    echo "  - ${IMAGE_NAME}:${IMAGE_TAG}-obfuscated"
fi
echo ""
echo "Build configuration:"
echo "  - Deployment: ${DEPLOYMENT_TYPE}"
echo "  - Variant: ${VARIANT}"
echo "  - Python Version: ${PYTHON_VERSION}"
echo "  - CPU Optimization: ${CPU_OPTIMIZATION}"
echo "  - Platform: Golem Network (CPU-only)"
if [ "${LLAMA_CPP_PYTHON_VERSION}" = "latest" ]; then
    echo "  - llama-cpp-python: latest main branch (bleeding edge)"
else
    echo "  - llama-cpp-python: pinned to ${LLAMA_CPP_PYTHON_VERSION:0:8}... (stable)"
fi
if [[ -n "${MODEL_FILE}" ]]; then
    echo "  - Model: $(basename "${MODEL_FILE}")"
elif [[ -n "${MODEL_DIR}" ]]; then
    echo "  - Models: ${MODELS_COPIED} file(s) from $(basename "${MODEL_DIR}")"
else
    echo "  - Models: None embedded (empty /app/models)"
fi
echo ""
echo "Backend support:"
echo "  - llama-cpp-python (GGUF models, CPU-only)"
echo "  - OpenBLAS acceleration"
echo ""
echo "Performance (CPU-only inference):"
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
if [[ "${USE_VOLUMES}" == "true" ]]; then
    echo "  1. Test locally (gateway only): docker compose -f docker/docker-compose.gateway-env.yml up"
else
    echo "  1. Test locally (gateway only): docker compose -f docker/docker-compose.gateway-mutable.yml up"
    echo "     OR (gateway + stargate): docker compose -f docker/docker-compose.gateway-stargate-mutable.yml up"
fi
echo "  2. Convert to GVMI: gvmkit-build ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  3. Deploy to Golem Network"
echo ""
echo "Build options:"
echo "  - Force rebuild: docker/build-golem.sh --no-cache"
echo "  - Mutable variant (default): docker/build-golem.sh --mutable"
echo "  - Immutable variant: docker/build-golem.sh --use-volumes"
echo "  - Obfuscated (production): docker/build-golem.sh --obfuscate"
echo "  - AVX-512 build: docker/build-golem.sh --cpu-avx512"
echo "  - AVX2 build (default): docker/build-golem.sh --cpu-avx2"
echo "  - Generic build: docker/build-golem.sh --cpu-generic"
echo "  - Obfuscated AVX-512: docker/build-golem.sh --obfuscate --cpu-avx512"
echo ""
echo "Note: AVX2 (default) recommended for Golem Network (unknown provider hardware)"
echo "Note: Mutable variant (default) allows adding models after build"



