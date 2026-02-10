#!/usr/bin/env bash
# Diagnostic script to investigate package installation paths
# Run this inside the container: docker exec -it universal-gateway-gpu /bin/bash -c "./docker/diagnose-package-paths.sh"

set -euo pipefail

echo "============================================================================"
echo "Package Installation Diagnostics"
echo "============================================================================"
echo ""

echo "1. Python version and executable location:"
python3.12 --version
which python3.12
echo ""

echo "2. Python sys.path (where Python looks for packages):"
python3.12 -c "import sys; [print(p) for p in sys.path]"
echo ""

echo "3. Site packages information:"
python3.12 -c "import site; print('Site packages:', site.getsitepackages()); print('User site:', site.getusersitepackages())"
echo ""

echo "4. Directory structure of /usr/local:"
find /usr/local -type d | sort | head -50
echo ""

echo "5. Search for setuptools:"
find /usr/local -name "setuptools*" -type d 2>/dev/null | head -10
echo ""

echo "6. Search for llama_cpp:"
find /usr/local -name "llama_cpp*" -type d 2>/dev/null | head -10
echo ""

echo "7. Search for vllm:"
find /usr/local -name "vllm*" -type d 2>/dev/null | head -10
echo ""

echo "8. Search for torch:"
find /usr/local -name "torch*" -type d 2>/dev/null | head -10
echo ""

echo "9. Full /usr/local/lib structure:"
ls -laR /usr/local/lib/ | head -100
echo ""

echo "10. Check if packages exist in expected locations:"
echo ""

echo "Checking /usr/local/lib/python3.12/dist-packages/:"
if [ -d "/usr/local/lib/python3.12/dist-packages" ]; then
    echo "  ✅ Directory exists"
    ls -la /usr/local/lib/python3.12/dist-packages/ | head -20
else
    echo "  ❌ Directory does NOT exist"
fi
echo ""

echo "Checking /usr/local/lib/python3.12/site-packages/:"
if [ -d "/usr/local/lib/python3.12/site-packages" ]; then
    echo "  ✅ Directory exists"
    ls -la /usr/local/lib/python3.12/site-packages/ | head -20
else
    echo "  ❌ Directory does NOT exist"
fi
echo ""

echo "11. Test imports:"
echo ""

echo "Testing setuptools:"
python3.12 -c "import setuptools; print('✅ setuptools OK - version:', setuptools.__version__)" || echo "❌ setuptools FAILED"
echo ""

echo "Testing llama_cpp:"
python3.12 -c "import llama_cpp; print('✅ llama_cpp OK - version:', llama_cpp.__version__)" || echo "❌ llama_cpp FAILED"
echo ""

echo "Testing torch:"
python3.12 -c "import torch; print('✅ torch OK - version:', torch.__version__)" || echo "❌ torch FAILED"
echo ""

echo "Testing vllm:"
python3.12 -c "import vllm; print('✅ vllm OK - version:', vllm.__version__)" || echo "❌ vllm FAILED"
echo ""

echo "12. Search in alternate locations:"
echo ""

echo "Checking /usr/local/local/lib/:"
if [ -d "/usr/local/local/lib" ]; then
    echo "  ⚠️  /usr/local/local/lib EXISTS (double-nested local!)"
    find /usr/local/local/lib -name "setuptools*" -o -name "llama_cpp*" -o -name "vllm*" | head -10
else
    echo "  ✅ /usr/local/local/lib does NOT exist (good)"
fi
echo ""

echo "Checking /build/packages/:"
if [ -d "/build/packages" ]; then
    echo "  ⚠️  /build/packages still exists (shouldn't be in runtime)"
    find /build/packages -type d | sort | head -20
else
    echo "  ✅ /build/packages does NOT exist (good)"
fi
echo ""

echo "============================================================================"
echo "Diagnostics complete"
echo "============================================================================"

