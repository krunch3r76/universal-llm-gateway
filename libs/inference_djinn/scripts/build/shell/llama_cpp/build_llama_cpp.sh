#!/bin/bash
# llama-cpp-python Build Script - Compatibility Matrix Enhanced
#
# ============================================================================
# llama-cpp-python Build Process
# ============================================================================
#
# This script builds llama-cpp-python from source with GPU support.
#
# Key features:
#   - Builds wheel with GPU optimizations (CUDA/ROCm)
#   - Uses tested commits by default, with option to use pinned stable
#   - Post-processes to enforce NumPy <2.3 (required for vLLM/Numba compatibility)
#
# ============================================================================

set -e

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PYTHON_BUILDER="$SCRIPT_DIR/../../python_builders/llama_cpp/build_llama_cpp_experimental.py"

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

# Parse arguments to inject smart defaults
ARGS=()
HAS_COMMIT_ARG=false
HAS_FEATURE_ARG=false
HAS_PROFILE_ARG=false

for arg in "$@"; do
    case "$arg" in
        --llama-cpp-commit=*|--latest-llama-cpp|--pinned-llama-cpp)
            HAS_COMMIT_ARG=true
            ;;
        --feature=*)
            HAS_FEATURE_ARG=true
            ;;
        --compatibility-profile=*)
            HAS_PROFILE_ARG=true
            ;;
    esac
    ARGS+=("$arg")
done

# Smart default: Use latest working commit unless user specifies otherwise
if [ "$HAS_COMMIT_ARG" = false ] && [ "$HAS_FEATURE_ARG" = false ] && [ "$HAS_PROFILE_ARG" = false ]; then
    echo "ℹ️  Using pinned stable commit (no API patches required)"
    echo "   To use old pinned stable: --pinned-llama-cpp"
    echo "   To try latest master: --latest-llama-cpp"
    echo ""
    
    
    ARGS+=("--pinned-llama-cpp")
fi

# Execute Python builder with enhanced arguments
"$PYTHON_EXEC" "$PYTHON_BUILDER" "${ARGS[@]}"
BUILD_EXIT_CODE=$?

if [ $BUILD_EXIT_CODE -ne 0 ]; then
    echo "❌ llama-cpp-python build failed with exit code $BUILD_EXIT_CODE"
    exit $BUILD_EXIT_CODE
fi

# ============================================================================
# Post-processing: Enforce NumPy version constraint
# ============================================================================
# Some packages (llama-cpp-python deps, PyTorch nightly) may upgrade NumPy to 2.3+
# but vLLM's Numba dependency (used for ngram proposer) requires NumPy <= 2.2.
# Since both vLLM and llama-cpp-python coexist in the same environment,
# we MUST enforce this constraint after ALL installations.

echo ""
echo "🔒 Post-processing: Enforcing NumPy version constraint..."

NUMPY_VERSION=$("$PYTHON_EXEC" -c "import numpy; print(numpy.__version__)" 2>/dev/null || echo "not found")
echo "   Current NumPy version: ${NUMPY_VERSION}"

if echo "${NUMPY_VERSION}" | grep -qE "^2\.[3-9]|^[3-9]\."; then
    echo "   ⚠️  NumPy ${NUMPY_VERSION} detected - downgrading to <2.3 for vLLM/Numba compatibility..."
    "$PYTHON_EXEC" -m pip install --force-reinstall --no-deps "numpy>=1.24.0,<2.3"
    NEW_VERSION=$("$PYTHON_EXEC" -c "import numpy; print(numpy.__version__)")
    echo "   ✅ NumPy downgraded: ${NUMPY_VERSION} → ${NEW_VERSION}"
else
    echo "   ✅ NumPy ${NUMPY_VERSION} is already compatible (< 2.3)"
fi

echo ""
echo "✅ llama-cpp-python build and post-processing complete!"
