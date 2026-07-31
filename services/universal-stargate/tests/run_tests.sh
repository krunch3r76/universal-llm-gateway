#!/bin/bash
# Test runner that temporarily renames __init__.py to avoid pytest import issues

set -e

cd "$(dirname "$0")/.."

# Temporarily rename __init__.py
if [ -f "__init__.py" ]; then
    mv __init__.py __init__.py.bak
    trap "mv __init__.py.bak __init__.py" EXIT
fi

# Run pytest with all arguments passed through
pytest "$@"

