#!/bin/bash

# Build OpenBLAS optimized for AMD Zen architecture
# This script compiles OpenBLAS specifically for AMD Ryzen processors

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
OPENBLAS_VERSION="0.3.30"
OPENBLAS_SOURCE_DIR="/tmp/OpenBLAS"
OPENBLAS_INSTALL_DIR="/usr/local/openblas-zen"
CPU_CORES=$(nproc)

echo -e "${BLUE}🔧 Building OpenBLAS ${OPENBLAS_VERSION} optimized for AMD Zen${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${YELLOW}🎯 Target: AMD Zen architecture (Ryzen 9 7900X)${NC}"
echo -e "${YELLOW}🔧 CPU Cores: ${CPU_CORES}${NC}"
echo -e "${YELLOW}📁 Install Directory: ${OPENBLAS_INSTALL_DIR}${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   echo -e "${RED}❌ This script should not be run as root${NC}"
   echo "Please run without sudo and use sudo only for the install step"
   exit 1
fi

# Check dependencies
echo -e "${BLUE}🔍 Checking dependencies...${NC}"
if ! command -v git &> /dev/null; then
    echo -e "${RED}❌ git is not installed${NC}"
    exit 1
fi

if ! command -v make &> /dev/null; then
    echo -e "${RED}❌ make is not installed${NC}"
    exit 1
fi

if ! command -v gcc &> /dev/null; then
    echo -e "${RED}❌ gcc is not installed${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All dependencies found${NC}"

# Clean up previous build
echo -e "${BLUE}🧹 Cleaning up previous build...${NC}"
if [ -d "${OPENBLAS_SOURCE_DIR}" ]; then
    rm -rf "${OPENBLAS_SOURCE_DIR}"
fi

# Clone OpenBLAS repository
echo -e "${BLUE}📥 Cloning OpenBLAS repository...${NC}"
git clone https://github.com/xianyi/OpenBLAS.git "${OPENBLAS_SOURCE_DIR}"
cd "${OPENBLAS_SOURCE_DIR}"

# Checkout specific version
echo -e "${BLUE}🔀 Checking out version ${OPENBLAS_VERSION}...${NC}"
git checkout "v${OPENBLAS_VERSION}"

# Configure build for Zen architecture
echo -e "${BLUE}⚙️  Configuring build for Zen architecture...${NC}"
echo -e "${YELLOW}🎯 Target: ZEN${NC}"
echo -e "${YELLOW}🔧 Threads: ${CPU_CORES}${NC}"
echo -e "${YELLOW}🚀 OpenMP: Enabled${NC}"

# Build OpenBLAS
echo -e "${BLUE}🔨 Building OpenBLAS...${NC}"
echo "This may take several minutes..."

make clean
make TARGET=ZEN \
     BINARY=64 \
     DYNAMIC_ARCH=0 \
     NUM_THREADS=${CPU_CORES} \
     USE_OPENMP=1 \
     NO_AFFINITY=1 \
     -j${CPU_CORES}

# Check if build was successful
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Build failed${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Build completed successfully!${NC}"

# Install OpenBLAS
echo -e "${BLUE}📦 Installing OpenBLAS...${NC}"
echo -e "${YELLOW}📁 Installing to: ${OPENBLAS_INSTALL_DIR}${NC}"

sudo make PREFIX="${OPENBLAS_INSTALL_DIR}" install

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Installation failed${NC}"
    exit 1
fi

# Set permissions
echo -e "${BLUE}🔐 Setting permissions...${NC}"
sudo chown -R root:root "${OPENBLAS_INSTALL_DIR}"
sudo chmod -R 755 "${OPENBLAS_INSTALL_DIR}"

# Verify installation
echo -e "${BLUE}🔍 Verifying installation...${NC}"
if [ -f "${OPENBLAS_INSTALL_DIR}/lib/libopenblas.so" ]; then
    echo -e "${GREEN}✅ OpenBLAS library installed successfully${NC}"
else
    echo -e "${RED}❌ OpenBLAS library not found${NC}"
    exit 1
fi

# Display library info
echo -e "${BLUE}📊 Library information:${NC}"
ls -la "${OPENBLAS_INSTALL_DIR}/lib/" | grep openblas

echo ""
echo -e "${GREEN}🎉 OpenBLAS Zen optimization completed!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${YELLOW}📁 Installation directory: ${OPENBLAS_INSTALL_DIR}${NC}"
echo -e "${YELLOW}🔧 Next step: Run rebuild_numpy_openblas.sh to rebuild NumPy${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
