#!/bin/bash
# llama-cpp-python Experimental Build Script - Shell Wrapper
# Enhanced version with compatibility fixes for Mixtral MoE support

set -e

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PYTHON_BUILDER="$SCRIPT_DIR/../../python_builders/llama_cpp/build_llama_cpp_experimental.py"

# Check if experimental Python builder exists
if [ ! -f "$PYTHON_BUILDER" ]; then
    echo "❌ ERROR: Experimental Python builder not found: $PYTHON_BUILDER"
    echo "💡 TIP: Use the stable builder: build_llama_cpp.sh"
    exit 1
fi

# Prefer venv Python if available, otherwise use system Python
PYTHON_EXEC="python3"
if [ -n "$VIRTUAL_ENV" ]; then
    PYTHON_EXEC="$VIRTUAL_ENV/bin/python3"
fi

# Add experimental warning unless --skip-warning is passed
SKIP_WARNING=false
for arg in "$@"; do
    if [ "$arg" = "--skip-warning" ]; then
        SKIP_WARNING=true
        break
    fi
done

if [ "$SKIP_WARNING" = false ]; then
    echo "⚠️  EXPERIMENTAL BUILD SCRIPT"
    echo "   This is an experimental version with enhanced compatibility features."
    echo "   For stable builds, use: build_llama_cpp.sh"
    echo ""
    echo "   New features:"
    echo "   - Compatibility matrix for known-good commits"
    echo "   - Dynamic CMake patching for version issues"
    echo "   - Build validation and fallback strategies"
    echo "   - Support for Mixtral MoE models"
    echo ""
    read -p "Continue? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
fi

# Remove --skip-warning from args before passing to Python
FILTERED_ARGS=()
for arg in "$@"; do
    if [ "$arg" != "--skip-warning" ]; then
        FILTERED_ARGS+=("$arg")
    fi
done

# Execute experimental Python builder with filtered arguments
exec "$PYTHON_EXEC" "$PYTHON_BUILDER" "${FILTERED_ARGS[@]}"