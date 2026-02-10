#!/bin/bash
# vLLM Build Script - Python Wrapper
#
# ============================================================================
# vLLM Build Process
# ============================================================================
#
# This script builds vLLM from source with nightly PyTorch support.
#
# Key features:
#   - Builds wheel with --no-deps to preserve nightly PyTorch
#   - Extracts dependencies from wheel METADATA
#   - Installs dependencies (except torch/torchvision/torchaudio)
#   - Installs the wheel
#   - Post-processes to enforce NumPy <2.3 (required for Numba compatibility)
#
# Version Control:
#   Pass --vllm-version=<tag/hash> to pin specific version
#   Default: latest release tag (NOT main branch)
#   Use --vllm-version=main for bleeding edge
#
# The builder handles everything - no separate dependency installation needed.
#
# ============================================================================

set -e

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PYTHON_BUILDER="$SCRIPT_DIR/../../python_builders/vllm/build_vllm.py"

# Check if Python builder exists
if [ ! -f "$PYTHON_BUILDER" ]; then
    echo "❌ ERROR: Python builder not found: $PYTHON_BUILDER"
    exit 1
fi

# Prefer venv Python if available, otherwise use system Python
PYTHON_EXEC="python3"
if [ -n "$VIRTUAL_ENV" ]; then
    PYTHON_EXEC="$VIRTUAL_ENV/bin/python3"
fi

# Execute Python builder with all arguments
"$PYTHON_EXEC" "$PYTHON_BUILDER" "$@"
BUILD_EXIT_CODE=$?

if [ $BUILD_EXIT_CODE -ne 0 ]; then
    echo "❌ vLLM build failed with exit code $BUILD_EXIT_CODE"
    exit $BUILD_EXIT_CODE
fi

# ============================================================================
# Post-processing: Enforce NumPy version constraint
# ============================================================================
# Some packages (PyTorch nightly, vLLM deps) may upgrade NumPy to 2.3+
# but Numba (used by vLLM for ngram proposer) requires NumPy <= 2.2.
# This MUST run after ALL installations to ensure the constraint is met.

echo ""
echo "🔒 Post-processing: Enforcing NumPy version constraint..."

NUMPY_VERSION=$("$PYTHON_EXEC" -c "import numpy; print(numpy.__version__)" 2>/dev/null || echo "not found")
echo "   Current NumPy version: ${NUMPY_VERSION}"

if echo "${NUMPY_VERSION}" | grep -qE "^2\.[3-9]|^[3-9]\."; then
    echo "   ⚠️  NumPy ${NUMPY_VERSION} detected - downgrading to <2.3 for Numba compatibility..."
    "$PYTHON_EXEC" -m pip install --force-reinstall --no-deps "numpy>=1.24.0,<2.3"
    NEW_VERSION=$("$PYTHON_EXEC" -c "import numpy; print(numpy.__version__)")
    echo "   ✅ NumPy downgraded: ${NUMPY_VERSION} → ${NEW_VERSION}"
else
    echo "   ✅ NumPy ${NUMPY_VERSION} is already compatible (< 2.3)"
fi

echo ""
echo "✅ vLLM build and post-processing complete!"
