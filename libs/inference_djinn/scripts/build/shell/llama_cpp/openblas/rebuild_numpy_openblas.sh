#!/bin/bash

# Rebuild NumPy against optimized OpenBLAS
# This script rebuilds NumPy to use the Zen-optimized OpenBLAS

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
OPENBLAS_INSTALL_DIR="/usr/local/openblas-zen"
VENV_DIR=".djinn-venv"

echo -e "${BLUE}🔧 Rebuilding NumPy against optimized OpenBLAS${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${YELLOW}🎯 Target: NumPy with Zen-optimized OpenBLAS${NC}"
echo -e "${YELLOW}📁 OpenBLAS Directory: ${OPENBLAS_INSTALL_DIR}${NC}"
echo -e "${YELLOW}🐍 Virtual Environment: ${VENV_DIR}${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   echo -e "${RED}❌ This script should not be run as root${NC}"
   echo "Please run without sudo"
   exit 1
fi

# Check if virtual environment exists
if [ ! -d "${VENV_DIR}" ]; then
    echo -e "${RED}❌ Virtual environment not found: ${VENV_DIR}${NC}"
    echo "Please create the virtual environment first"
    exit 1
fi

# Check if OpenBLAS is installed
if [ ! -f "${OPENBLAS_INSTALL_DIR}/lib/libopenblas.so" ]; then
    echo -e "${RED}❌ Optimized OpenBLAS not found: ${OPENBLAS_INSTALL_DIR}/lib/libopenblas.so${NC}"
    echo "Please run build_openblas_zen.sh first"
    exit 1
fi

echo -e "${GREEN}✅ OpenBLAS found at: ${OPENBLAS_INSTALL_DIR}${NC}"

# Activate virtual environment
echo -e "${BLUE}🐍 Activating virtual environment...${NC}"
source "${VENV_DIR}/bin/activate"

# Check current NumPy version
echo -e "${BLUE}🔍 Checking current NumPy installation...${NC}"
if python -c "import numpy; print('Current NumPy version:', numpy.__version__)" 2>/dev/null; then
    echo -e "${YELLOW}⚠️  NumPy is currently installed${NC}"
else
    echo -e "${GREEN}✅ NumPy not installed, will install fresh${NC}"
fi

# Uninstall current NumPy
echo -e "${BLUE}🗑️  Uninstalling current NumPy...${NC}"
pip uninstall numpy -y || true

# Set environment variables for optimized OpenBLAS
echo -e "${BLUE}⚙️  Setting environment variables...${NC}"
export LD_LIBRARY_PATH="${OPENBLAS_INSTALL_DIR}/lib:$LD_LIBRARY_PATH"
export PKG_CONFIG_PATH="${OPENBLAS_INSTALL_DIR}/lib/pkgconfig:$PKG_CONFIG_PATH"
export OPENBLAS="${OPENBLAS_INSTALL_DIR}"
export BLAS="${OPENBLAS_INSTALL_DIR}"
export LAPACK="${OPENBLAS_INSTALL_DIR}"

echo -e "${YELLOW}📁 LD_LIBRARY_PATH: ${LD_LIBRARY_PATH}${NC}"
echo -e "${YELLOW}📁 PKG_CONFIG_PATH: ${PKG_CONFIG_PATH}${NC}"

# Rebuild NumPy from source
echo -e "${BLUE}🔨 Rebuilding NumPy from source...${NC}"
echo "This may take several minutes..."

pip install numpy --no-binary numpy --no-cache-dir

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ NumPy rebuild failed${NC}"
    exit 1
fi

echo -e "${GREEN}✅ NumPy rebuild completed successfully!${NC}"

# Verify the installation
echo -e "${BLUE}🔍 Verifying NumPy installation...${NC}"
python -c "
import numpy
print('✅ NumPy version:', numpy.__version__)
config = numpy.__config__.show()
print('✅ NumPy configuration loaded successfully')
"

# Test OpenBLAS configuration
echo -e "${BLUE}🔍 Checking OpenBLAS configuration...${NC}"
python -c "
import numpy
import json
config = numpy.__config__.show()
if 'ZEN' in str(config) and 'MAX_THREADS=24' in str(config):
    print('✅ NumPy is using Zen-optimized OpenBLAS!')
    print('   - Target: ZEN')
    print('   - Threads: 24')
else:
    print('❌ NumPy is NOT using Zen-optimized OpenBLAS')
    print('   Current config:', str(config))
"

# Test performance
echo -e "${BLUE}📊 Testing basic performance...${NC}"
python -c "
import numpy as np
import time

# Test matrix multiplication
size = 1000
a = np.random.random((size, size))
b = np.random.random((size, size))

# Warm up
_ = np.dot(a, b)

# Benchmark
start_time = time.time()
for _ in range(3):
    result = np.dot(a, b)
end_time = time.time()

avg_time = (end_time - start_time) / 3
gflops = (2 * size**3) / (avg_time * 1e9)

print(f'✅ Matrix multiplication ({size}x{size}): {avg_time:.4f}s')
print(f'✅ Performance: {gflops:.2f} GFLOPS')
"

echo ""
echo -e "${GREEN}🎉 NumPy rebuild completed successfully!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${YELLOW}📁 OpenBLAS: ${OPENBLAS_INSTALL_DIR}${NC}"
echo -e "${YELLOW}🐍 Virtual Environment: ${VENV_DIR}${NC}"
echo -e "${YELLOW}🔧 Next step: Test your applications for performance improvements${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Create a test script
echo -e "${BLUE}📝 Creating performance test script...${NC}"
cat > test_openblas_performance.py << 'EOF'
#!/usr/bin/env python3

import numpy as np
import time

def test_performance():
    print("🧪 Testing OpenBLAS Performance")
    print("=" * 50)
    
    sizes = [1000, 2000, 4000]
    
    for size in sizes:
        print(f"\n📐 Matrix size: {size}x{size}")
        
        # Create random matrices
        a = np.random.random((size, size))
        b = np.random.random((size, size))
        
        # Warm up
        _ = np.dot(a, b)
        
        # Benchmark
        start_time = time.time()
        for _ in range(5):
            result = np.dot(a, b)
        end_time = time.time()
        
        avg_time = (end_time - start_time) / 5
        gflops = (2 * size**3) / (avg_time * 1e9)
        
        print(f"   ⏱️  Time: {avg_time:.4f}s")
        print(f"   🚀 GFLOPS: {gflops:.2f}")

if __name__ == "__main__":
    test_performance()
EOF

chmod +x test_openblas_performance.py
echo -e "${GREEN}✅ Performance test script created: test_openblas_performance.py${NC}"
echo -e "${YELLOW}💡 Run: python test_openblas_performance.py to test performance${NC}"
