#!/bin/bash

# Build Flash Attention from source for specific PyTorch and CUDA versions
# This script builds Flash Attention optimized for the current environment

set -e

echo "🔧 Building Flash Attention from source..."

# Set environment variables for compilation
export FLASH_ATTN_CUDA_ARCHS="120"  # Only build for sm_120
export MAX_JOBS=$(($(nproc) / 2))  # Use half the CPU cores for parallelization

# Get the project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KERNELS_DIR="$PROJECT_ROOT/tmp/extra_sources/flash-attention"

# Ensure we're in the virtual environment
if [[ "$VIRTUAL_ENV" != *".djinn-venv"* ]]; then
    echo "❌ Error: Not in .djinn-venv virtual environment"
    echo "Please activate the virtual environment first:"
    echo "source .djinn-venv/bin/activate"
    exit 1
fi

echo "📁 Building in: $KERNELS_DIR"
echo "🔧 CUDA Architecture: $FLASH_ATTN_CUDA_ARCHS (sm_120 only)"
echo "⚙️  Max Jobs: $MAX_JOBS"

# Navigate to the kernels directory
cd "$KERNELS_DIR"

# Clean the source tree
echo "🧹 Cleaning source tree..."
git reset --hard HEAD
git clean -dfx

# Build Flash Attention wheel with PEP 517 to avoid deprecation warnings
# This will only build for sm_120 (compute capability 12.0)
echo "🔨 Building Flash Attention wheel with PEP 517 (sm_120 only)..."
WHEELHOUSE_DIR="$PROJECT_ROOT/.djinn-venv/wheelhouse"
mkdir -p "$WHEELHOUSE_DIR"
python -m pip wheel . --wheel-dir "$WHEELHOUSE_DIR" --use-pep517 --no-build-isolation --no-deps

# Install the wheel
echo "📦 Installing Flash Attention wheel..."
pip install --force-reinstall --no-deps "$WHEELHOUSE_DIR"/flash_attn-*.whl

# Verify installation
echo "✅ Verifying Flash Attention installation..."
python3 -c "
import flash_attn
print('✅ Flash Attention imported successfully')
print(f'Flash Attention version: {flash_attn.__version__}')
"

echo "🎉 Flash Attention build completed successfully!"
