#!/usr/bin/env bash
# Build native llama-server binary from llama.cpp source.
#
# Usage: build_llama_server.sh [VERSION] [CPU_OPT] [GPU_ARCH]
#   VERSION:  git ref (commit, tag, "main") — default: main
#   CPU_OPT:  native|avx512|avx2|generic  — default: avx2
#   GPU_ARCH: multi|120|89|90|...         — default: multi
#
# Output: /opt/llama-server/bin/llama-server
#
# ──────────────────────────────────────────────────────────────────────
# TRICKY: Flag Alignment with llama-cpp-python
# ──────────────────────────────────────────────────────────────────────
# This script's CPU and CUDA flags MUST stay aligned with:
#   libs/inference_djinn/scripts/build/python_builders/common/cmake_config.py
#
# cmake_config.py is the single source of truth for llama-cpp-python.
# This script duplicates the flag mappings because:
#   1. llama-server is a SEPARATE git clone (ggml-org/llama.cpp main)
#   2. llama-cpp-python uses its vendored submodule (older commit)
#   3. This script runs in a minimal Docker build context without Python
#
# If you change flags in cmake_config.py, update the case statement below.
# Search for "Match cmake_config.py" comments to find corresponding sections.
#
# TRICKY: Static vs Shared Build
# ──────────────────────────────────────────────────────────────────────
# BUILD_SHARED_LIBS=OFF is INTENTIONAL — produces a static binary.
# llama-cpp-python installs libllama.so (shared library).
# If llama-server also used shared libs, the two would conflict at runtime
# because they may link different llama.cpp versions.
# Static binary = self-contained, no library conflicts.

# ──────────────────────────────────────────────────────────────────────
# FLAG CORRESPONDENCE TABLE
# ──────────────────────────────────────────────────────────────────────
# This script's flags must match cmake_config.py. Here's the mapping:
#
# CPU Flags (case statement below ↔ cmake_config.py get_cpu_flags()):
#   native  → native_cflags in cmake_config.py
#   avx512  → AVX512 dict in cmake_config.py
#   avx2    → AVX2 dict in cmake_config.py
#   generic → GENERIC dict in cmake_config.py
#
# CUDA Flags (cmake invocation below ↔ cmake_config.py get_cmake_args()):
#   GGML_CUDA=ON              → cmake_config.py get_cmake_args()
#   GGML_CUDA_F16=ON          → cmake_config.py get_cmake_args()
#   GGML_CUDA_FORCE_MMQ=ON    → cmake_config.py get_cmake_args()
#   GGML_CUDA_FORCE_CUBLAS=ON → cmake_config.py get_cmake_args()
#   CMAKE_CUDA_ARCHITECTURES  → cmake_config.py get_cmake_args()
#
# Last synchronized: Phase 1 of docker-build-optimization plan
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

LLAMA_CPP_VERSION="${1:-main}"
CPU_OPTIMIZATION="${2:-avx2}"
GPU_ARCH="${3:-multi}"

WORK_DIR="/build/llama-server-build"
INSTALL_PREFIX="/opt/llama-server"

echo "🔨 Building llama-server from llama.cpp ${LLAMA_CPP_VERSION}"
echo "   CPU: ${CPU_OPTIMIZATION}, GPU: ${GPU_ARCH}"

mkdir -p "${WORK_DIR}"
cd "${WORK_DIR}"

# Clone llama.cpp (shallow clone for speed)
if [ "${LLAMA_CPP_VERSION}" = "main" ] || [ "${LLAMA_CPP_VERSION}" = "latest" ]; then
    git clone --depth=1 https://github.com/ggml-org/llama.cpp.git .
else
    git clone --depth=1 --branch "${LLAMA_CPP_VERSION}" https://github.com/ggml-org/llama.cpp.git . 2>/dev/null || {
        # Not a tag — try as commit hash
        git clone https://github.com/ggml-org/llama.cpp.git .
        git checkout "${LLAMA_CPP_VERSION}"
    }
fi

ACTUAL_COMMIT=$(git rev-parse --short HEAD)
echo "   Commit: ${ACTUAL_COMMIT}"

# Map CPU optimization to CMake flags
# Match cmake_config.py get_cpu_flags() lines 287-343
case "${CPU_OPTIMIZATION}" in
    native)
        # Match cmake_config.py native_cflags (line 291)
        COMMON_FLAGS="-O3 -march=native -mtune=native -ffast-math -fno-finite-math-only -funroll-loops -fomit-frame-pointer -falign-functions=32 -falign-loops=32 -fprefetch-loop-arrays -ftree-vectorize -fno-signed-zeros -fno-trapping-math"
        CMAKE_C_FLAGS="${COMMON_FLAGS}"
        CMAKE_CXX_FLAGS="${COMMON_FLAGS}"
        ;;
    avx512)
        # Match cmake_config.py AVX512 cflags (line 314)
        COMMON_FLAGS="-O3 -march=x86-64-v4 -mtune=generic -ffast-math -fno-finite-math-only"
        CMAKE_C_FLAGS="${COMMON_FLAGS}"
        CMAKE_CXX_FLAGS="${COMMON_FLAGS}"
        ;;
    avx2)
        # Match cmake_config.py AVX2 cflags (line 323)
        COMMON_FLAGS="-O3 -march=x86-64-v3 -mtune=generic -ffast-math -fno-finite-math-only"
        CMAKE_C_FLAGS="${COMMON_FLAGS}"
        CMAKE_CXX_FLAGS="${COMMON_FLAGS}"
        ;;
    generic)
        # Match cmake_config.py GENERIC cflags (line 333)
        COMMON_FLAGS="-O3 -march=x86-64 -mtune=generic -fno-finite-math-only"
        CMAKE_C_FLAGS="${COMMON_FLAGS}"
        CMAKE_CXX_FLAGS="${COMMON_FLAGS}"
        ;;
    *)
        echo "❌ Unknown CPU_OPTIMIZATION: ${CPU_OPTIMIZATION}"
        exit 1
        ;;
esac

# Map GPU arch to CMake CUDA architectures
if [ "${GPU_ARCH}" = "multi" ]; then
    CUDA_ARCHS="80;86;87;89;90;120"
else
    CUDA_ARCHS="${GPU_ARCH}"
fi

# Build with CMake
mkdir -p build && cd build

# Match cmake_config.py get_cmake_args() CUDA flags (lines 364-368)
cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${INSTALL_PREFIX}" \
    -DCMAKE_C_FLAGS="${CMAKE_C_FLAGS}" \
    -DCMAKE_CXX_FLAGS="${CMAKE_CXX_FLAGS}" \
    -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHS}" \
    -DGGML_CUDA=ON \
    -DGGML_CUDA_F16=ON \
    -DGGML_CUDA_FORCE_MMQ=ON \
    -DGGML_CUDA_FORCE_CUBLAS=ON \
    -DGGML_OPENMP=ON \
    -DGGML_BLAS=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -G Ninja

cmake --build . --config Release --target llama-server -j"$(nproc)"

# Install
mkdir -p "${INSTALL_PREFIX}/bin"
cp bin/llama-server "${INSTALL_PREFIX}/bin/"
chmod +x "${INSTALL_PREFIX}/bin/llama-server"

# Record build metadata
cat > "${INSTALL_PREFIX}/BUILD_INFO" << EOF
commit: ${ACTUAL_COMMIT}
version: ${LLAMA_CPP_VERSION}
cpu: ${CPU_OPTIMIZATION}
cpu_flags: ${CMAKE_C_FLAGS}
gpu_arch: ${GPU_ARCH}
cuda_archs: ${CUDA_ARCHS}
cuda_f16: ON
cuda_force_mmq: ON
cuda_force_cublas: ON
build_date: $(date -u +%Y-%m-%dT%H:%M:%SZ)
flag_reference: libs/inference_djinn/scripts/build/python_builders/common/cmake_config.py
EOF

# Verify
echo "🔍 Verifying llama-server..."
"${INSTALL_PREFIX}/bin/llama-server" --version 2>/dev/null || \
    echo "   (--version not supported, binary exists: $(ls -lh "${INSTALL_PREFIX}/bin/llama-server"))"

# Cleanup build artifacts (save ~2GB in Docker layer)
cd /
rm -rf "${WORK_DIR}"

echo "✅ llama-server installed to ${INSTALL_PREFIX}/bin/"
echo "   Binary size: $(du -h "${INSTALL_PREFIX}/bin/llama-server" | cut -f1)"
