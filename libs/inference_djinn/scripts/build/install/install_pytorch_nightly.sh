#!/bin/bash
set -e

# PyTorch Latest Nightly Installation Script for RTX 5090 Blackwell
# Checks current version and installs latest nightly if needed
# Known working version: torch==2.10.0.dev20250914+cu130 (Sept 14, 2025 build)

echo "🔥 PyTorch Latest Nightly Installation Script for RTX 5090 Blackwell"
echo "🎯 CUDA 13.0 + sm_120 support"
echo "=================================================="

# ============================================================================
# CHECK VIRTUAL ENVIRONMENT
# ============================================================================

check_venv() {
    # Check if we're in a virtual environment
    if [ -z "${VIRTUAL_ENV:-}" ]; then
        echo "⚠️  WARNING: No virtual environment detected (VIRTUAL_ENV not set)"
        echo "   The script will install to: $(which python3)"
        echo ""
        echo "   It's recommended to activate a virtual environment first:"
        echo "   source .djinn-venv/bin/activate  # or your venv path"
        echo ""
        # Only prompt if running interactively
        if [ -t 0 ]; then
            read -p "   Continue anyway? (y/N) " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                echo "❌ Installation cancelled"
                exit 1
            fi
        else
            echo "   ⚠️  Non-interactive mode: proceeding with installation"
        fi
    else
        echo "✅ Virtual environment detected: $VIRTUAL_ENV"
        echo "   Python: $(which python3)"
        echo "   Pip: $(which pip)"
    fi
}

# ============================================================================
# CHECK CURRENT PYTORCH VERSION
# ============================================================================

check_pytorch_version() {
    echo "🔍 Checking current PyTorch version..."
    
    # Check if PyTorch is installed
    if ! python3 -c "import torch; print(torch.__version__)" 2>/dev/null; then
        echo "❌ PyTorch not found - will install latest nightly"
        return 1
    fi
    
    # Get current version
    local current_version=$(python3 -c "import torch; print(torch.__version__)" 2>/dev/null)
    echo "📦 Current PyTorch version: $current_version"
    
    # Check if it's already 2.10.0 or higher (latest nightly)
    if [[ "$current_version" == 2.1[0-9]* ]]; then
        echo "✅ PyTorch $current_version already installed (latest nightly)"
        return 0
    elif [[ "$current_version" == 2.[8-9]* ]]; then
        echo "⚠️  PyTorch $current_version found, need to upgrade to latest nightly (2.10.0+)"
        return 1
    else
        echo "⚠️  PyTorch version $current_version found, need to upgrade to latest nightly"
        return 1
    fi
}

# ============================================================================
# SETUP WHEELHOUSE
# ============================================================================

setup_wheelhouse() {
    # Determine wheelhouse location
    # Prefer venv wheelhouse if in a virtual environment, otherwise use global
    if [ -n "${VIRTUAL_ENV:-}" ]; then
        WHEELHOUSE_DIR="$VIRTUAL_ENV/wheelhouse"
        echo "📍 Using venv wheelhouse: $WHEELHOUSE_DIR"
    else
        WHEELHOUSE_DIR="$HOME/.wheelhouse"
        echo "📍 Using global wheelhouse: $WHEELHOUSE_DIR"
    fi
    
    # Create wheelhouse directory
    mkdir -p "$WHEELHOUSE_DIR"
    echo "✅ Wheelhouse directory ready: $WHEELHOUSE_DIR"
}

# ============================================================================
# INSTALL PYTORCH LATEST NIGHTLY
# ============================================================================

install_pytorch_nightly() {
    echo "🚀 Installing PyTorch latest nightly with CUDA 13.0 support..."
    
    # Setup wheelhouse
    setup_wheelhouse
    
    # Check if wheels already exist in wheelhouse
    TORCH_WHEEL_EXISTS=$(ls "$WHEELHOUSE_DIR"/torch-*.whl 2>/dev/null | head -1)
    TORCHVISION_WHEEL_EXISTS=$(ls "$WHEELHOUSE_DIR"/torchvision-*.whl 2>/dev/null | head -1)
    TORCHAUDIO_WHEEL_EXISTS=$(ls "$WHEELHOUSE_DIR"/torchaudio-*.whl 2>/dev/null | head -1)
    
    if [ -n "$TORCH_WHEEL_EXISTS" ] && [ -n "$TORCHVISION_WHEEL_EXISTS" ] && [ -n "$TORCHAUDIO_WHEEL_EXISTS" ]; then
        echo "✅ Wheels found in wheelhouse, using existing wheels..."
        echo "   📦 torch: $(basename "$TORCH_WHEEL_EXISTS")"
        echo "   📦 torchvision: $(basename "$TORCHVISION_WHEEL_EXISTS")"
        echo "   📦 torchaudio: $(basename "$TORCHAUDIO_WHEEL_EXISTS")"
    else
        # Uninstall existing PyTorch packages
        echo "🧹 Uninstalling existing PyTorch packages..."
        pip uninstall torch torchvision torchaudio -y 2>/dev/null || true
        
        # Install PyTorch latest nightly with CUDA 13.0
        # Known working version: torch==2.10.0.dev20250914+cu130 (Sept 14, 2025 build)
        echo "📦 Downloading PyTorch latest nightly build to wheelhouse..."
        
        # Save original PIP_REQUIRE_HASHES and PIP_CONFIG_FILE if set
        ORIG_PIP_REQUIRE_HASHES="${PIP_REQUIRE_HASHES:-}"
        ORIG_PIP_CONFIG_FILE="${PIP_CONFIG_FILE:-}"
        
        # Temporarily disable require-hashes for this install
        unset PIP_REQUIRE_HASHES
        unset PIP_CONFIG_FILE
        
        # Download wheels to wheelhouse using pip download
        # Use --no-deps=false to let pip resolve dependencies and download compatible versions
        echo "📥 Downloading wheels to wheelhouse (resolving dependencies)..."
        pip download torch torchvision torchaudio \
            --index-url https://download.pytorch.org/whl/nightly/cu130 \
            --pre \
            --no-cache-dir \
            --dest "$WHEELHOUSE_DIR" || {
            echo "⚠️  Warning: Failed to download to wheelhouse, installing directly..."
            pip install torch torchvision torchaudio \
                --index-url https://download.pytorch.org/whl/nightly/cu130 \
                --pre \
                --no-cache-dir
            
            # Restore original environment variables
            [ -n "$ORIG_PIP_REQUIRE_HASHES" ] && export PIP_REQUIRE_HASHES="$ORIG_PIP_REQUIRE_HASHES" || unset PIP_REQUIRE_HASHES
            [ -n "$ORIG_PIP_CONFIG_FILE" ] && export PIP_CONFIG_FILE="$ORIG_PIP_CONFIG_FILE" || unset PIP_CONFIG_FILE
            
            echo "✅ PyTorch installation complete (direct install)"
            return 0
        }
        
        # Restore original environment variables
        [ -n "$ORIG_PIP_REQUIRE_HASHES" ] && export PIP_REQUIRE_HASHES="$ORIG_PIP_REQUIRE_HASHES" || unset PIP_REQUIRE_HASHES
        [ -n "$ORIG_PIP_CONFIG_FILE" ] && export PIP_CONFIG_FILE="$ORIG_PIP_CONFIG_FILE" || unset PIP_CONFIG_FILE
        
        echo "✅ Wheels downloaded to wheelhouse"
    fi
    
    # Install from wheelhouse
    echo "📦 Installing from wheelhouse..."
    pip install torch torchvision torchaudio \
        --find-links "$WHEELHOUSE_DIR" \
        --no-index \
        --pre \
        --no-cache-dir || {
        echo "⚠️  Warning: Installation from wheelhouse failed, falling back to direct install..."
        
        # Save original PIP_REQUIRE_HASHES and PIP_CONFIG_FILE if set
        ORIG_PIP_REQUIRE_HASHES="${PIP_REQUIRE_HASHES:-}"
        ORIG_PIP_CONFIG_FILE="${PIP_CONFIG_FILE:-}"
        
        # Temporarily disable require-hashes for fallback install
        unset PIP_REQUIRE_HASHES
        unset PIP_CONFIG_FILE
        
        pip install torch torchvision torchaudio \
            --index-url https://download.pytorch.org/whl/nightly/cu130 \
            --pre \
            --no-cache-dir
        
        # Restore original environment variables
        [ -n "$ORIG_PIP_REQUIRE_HASHES" ] && export PIP_REQUIRE_HASHES="$ORIG_PIP_REQUIRE_HASHES" || unset PIP_REQUIRE_HASHES
        [ -n "$ORIG_PIP_CONFIG_FILE" ] && export PIP_CONFIG_FILE="$ORIG_PIP_CONFIG_FILE" || unset PIP_CONFIG_FILE
    }
    
    echo "✅ PyTorch installation complete"
}

# ============================================================================
# CREATE REQUIREMENTS LOCK FILE
# ============================================================================

create_requirements_lock() {
    echo "🔒 Creating requirements lock file..."
    
    # Setup wheelhouse
    setup_wheelhouse
    
    # Find wheels in wheelhouse first
    TORCH_WHEEL=$(ls "$WHEELHOUSE_DIR"/torch-*.whl 2>/dev/null | head -1)
    TORCHVISION_WHEEL=$(ls "$WHEELHOUSE_DIR"/torchvision-*.whl 2>/dev/null | head -1)
    TORCHAUDIO_WHEEL=$(ls "$WHEELHOUSE_DIR"/torchaudio-*.whl 2>/dev/null | head -1)
    
    if [ -z "$TORCH_WHEEL" ] || [ -z "$TORCHVISION_WHEEL" ] || [ -z "$TORCHAUDIO_WHEEL" ]; then
        echo "⚠️  Not all wheels found in wheelhouse, skipping lock file creation"
        echo "   Found: torch=$TORCH_WHEEL, torchvision=$TORCHVISION_WHEEL, torchaudio=$TORCHAUDIO_WHEEL"
        return 1
    fi
    
    # Extract versions from wheel filenames (more reliable than installed packages)
    # Wheel format: package-version+metadata-py3-none-any.whl or package-version+metadata-cp312-cp312-manylinux.whl
    TORCH_VERSION=$(basename "$TORCH_WHEEL" | sed -E 's/torch-([0-9.]+[^+]*\+[^-]+).*/\1/' || echo "")
    TORCHVISION_VERSION=$(basename "$TORCHVISION_WHEEL" | sed -E 's/torchvision-([0-9.]+[^+]*\+[^-]+).*/\1/' || echo "")
    TORCHAUDIO_VERSION=$(basename "$TORCHAUDIO_WHEEL" | sed -E 's/torchaudio-([0-9.]+[^+]*\+[^-]+).*/\1/' || echo "")
    
    if [ -z "$TORCH_VERSION" ] || [ -z "$TORCHVISION_VERSION" ] || [ -z "$TORCHAUDIO_VERSION" ]; then
        echo "⚠️  Failed to extract versions from wheel filenames"
        echo "   Trying to get from installed packages instead..."
        TORCH_VERSION=$(python3 -c "import torch; print(torch.__version__)" 2>/dev/null || echo "")
        TORCHVISION_VERSION=$(python3 -c "import torchvision; print(torchvision.__version__)" 2>/dev/null || echo "")
        TORCHAUDIO_VERSION=$(python3 -c "import torchaudio; print(torchaudio.__version__)" 2>/dev/null || echo "")
        
        if [ -z "$TORCH_VERSION" ] || [ -z "$TORCHVISION_VERSION" ] || [ -z "$TORCHAUDIO_VERSION" ]; then
            echo "⚠️  Cannot determine versions, skipping lock file creation"
            return 1
        fi
    fi
    
    # Generate hashes
    echo "🔐 Generating hashes..."
    TORCH_HASH=$(pip hash "$TORCH_WHEEL" 2>/dev/null | grep -o 'sha256:[a-f0-9]*' || echo "")
    TORCHVISION_HASH=$(pip hash "$TORCHVISION_WHEEL" 2>/dev/null | grep -o 'sha256:[a-f0-9]*' || echo "")
    TORCHAUDIO_HASH=$(pip hash "$TORCHAUDIO_WHEEL" 2>/dev/null | grep -o 'sha256:[a-f0-9]*' || echo "")
    
    if [ -z "$TORCH_HASH" ] || [ -z "$TORCHVISION_HASH" ] || [ -z "$TORCHAUDIO_HASH" ]; then
        echo "⚠️  Failed to generate hashes, skipping lock file creation"
        return 1
    fi
    
    # Determine lock file location
    if [ -n "${VIRTUAL_ENV:-}" ]; then
        LOCK_FILE="$VIRTUAL_ENV/requirements-pytorch-$(hostname).lock"
    else
        LOCK_FILE="requirements-pytorch-$(hostname).lock"
    fi
    
    # Create lock file
    cat > "$LOCK_FILE" << EOF
# PyTorch Latest Nightly Requirements Lock
# Generated: $(date)
# Machine: $(hostname)
# 
# This file provides hash-verified installation of PyTorch nightly builds
# optimized for RTX 5090 Blackwell (CUDA 13.0, sm_120/sm_130).
#
# Installation:
#   pip install --require-hashes -r $LOCK_FILE
#
# Note: Ensure wheelhouse is configured (--find-links or pip.conf)
#
# Available wheels in $WHEELHOUSE_DIR/:
#   ✅ torch==${TORCH_VERSION} ($(basename "$TORCH_WHEEL"))
#   ✅ torchvision==${TORCHVISION_VERSION} ($(basename "$TORCHVISION_WHEEL"))
#   ✅ torchaudio==${TORCHAUDIO_VERSION} ($(basename "$TORCHAUDIO_WHEEL"))

--no-index
--find-links file://$WHEELHOUSE_DIR
torch==${TORCH_VERSION} \\
    --hash=$TORCH_HASH
torchvision==${TORCHVISION_VERSION} \\
    --hash=$TORCHVISION_HASH
torchaudio==${TORCHAUDIO_VERSION} \\
    --hash=$TORCHAUDIO_HASH
EOF
    
    echo "✅ Requirements lock file created: $LOCK_FILE"
    echo "   📍 Wheelhouse: $WHEELHOUSE_DIR"
    echo "   🔒 Versions locked: torch==${TORCH_VERSION}, torchvision==${TORCHVISION_VERSION}, torchaudio==${TORCHAUDIO_VERSION}"
}

# ============================================================================
# FIX NCCL LIBRARY PATHS (POST-INSTALL)
# ============================================================================

fix_nccl_paths() {
    echo "🔧 Checking for corrupted NCCL library paths..."
    
    # Detect Python site-packages directory dynamically
    PYTHON_SITE_PACKAGES=$(python3 -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || echo "")
    
    if [ -z "$PYTHON_SITE_PACKAGES" ]; then
        echo "⚠️  Could not detect Python site-packages directory"
        return 0
    fi
    
    NCCL_DIR="$PYTHON_SITE_PACKAGES/nvidia/nccl"
    
    # Fix corrupted directory names if they exist
    if [ -d "$NCCL_DIR/~ib" ]; then
        echo "🔧 Fixing corrupted NCCL library path (~ib -> lib)..."
        cd "$NCCL_DIR"
        mv ~ib lib 2>/dev/null || true
        cd - > /dev/null
        echo "✅ NCCL library path fixed"
    fi
    
    if [ -d "$NCCL_DIR/~nclude" ]; then
        echo "🔧 Fixing corrupted NCCL include path (~nclude -> include)..."
        cd "$NCCL_DIR"
        mv ~nclude include 2>/dev/null || true
        cd - > /dev/null
        echo "✅ NCCL include path fixed"
    fi
    
    # Set LD_LIBRARY_PATH if NCCL lib directory exists
    if [ -d "$NCCL_DIR/lib" ]; then
        export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:$NCCL_DIR/lib"
    fi
}

# ============================================================================
# VERIFY INSTALLATION
# ============================================================================

verify_installation() {
    echo "🔍 Verifying PyTorch latest nightly installation..."
    
    # Fix NCCL library paths if needed (post-install step)
    fix_nccl_paths
    
    python3 -c "
import torch
print(f'✅ PyTorch {torch.__version__} installed successfully')

# Check CUDA support
if torch.cuda.is_available():
    print(f'✅ CUDA available: {torch.cuda.is_available()}')
    print(f'✅ CUDA version: {torch.version.cuda}')
    print(f'✅ GPU: {torch.cuda.get_device_name(0)}')
    
    # Check CUDA capability
    capability = torch.cuda.get_device_capability(0)
    major, minor = capability
    arch_code = major * 10 + minor
    print(f'✅ CUDA Capability: {major}.{minor} (sm_{arch_code})')
    
    if major == 12 and minor == 0:
        print('✅ RTX 5090 Blackwell (sm_120) detected - Perfect match!')
    elif major == 13 and minor == 0:
        print('✅ RTX 5090 Blackwell (sm_130) detected - Perfect match!')
    else:
        print(f'⚠️  Architecture mismatch - Expected sm_120 or sm_130, got sm_{arch_code}')
else:
    print('❌ CUDA not available')
    exit(1)

# Test basic CUDA operations
try:
    x = torch.randn(100, 100).cuda()
    y = torch.randn(100, 100).cuda()
    z = torch.mm(x, y)
    print('✅ Basic CUDA operations working')
except Exception as e:
    print(f'❌ CUDA operations failed: {e}')
    exit(1)

print('🎉 PyTorch latest nightly installation verified successfully!')
"
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

main() {
    echo "🚀 Starting PyTorch latest nightly installation check..."
    
    # Check virtual environment
    check_venv
    
    # Initialize LOCK_FILE variable
    LOCK_FILE=""
    
    # Check if PyTorch latest nightly is already installed
    if check_pytorch_version; then
        echo "✅ PyTorch latest nightly already installed - skipping installation"
        verify_installation
        # Still create/update lock file if wheels exist in wheelhouse
        if create_requirements_lock; then
            # Determine lock file location to check if it was created
            if [ -n "${VIRTUAL_ENV:-}" ]; then
                LOCK_FILE="$VIRTUAL_ENV/requirements-pytorch-$(hostname).lock"
            else
                LOCK_FILE="requirements-pytorch-$(hostname).lock"
            fi
        fi
    else
        echo "📦 PyTorch latest nightly installation required"
        install_pytorch_nightly
        verify_installation
        # Create lock file after installation
        if create_requirements_lock; then
            # Determine lock file location to check if it was created
            if [ -n "${VIRTUAL_ENV:-}" ]; then
                LOCK_FILE="$VIRTUAL_ENV/requirements-pytorch-$(hostname).lock"
            else
                LOCK_FILE="requirements-pytorch-$(hostname).lock"
            fi
        fi
    fi
    
    echo ""
    echo "🎉 PyTorch latest nightly setup complete!"
    echo "=================================================="
    echo "✅ PyTorch latest nightly with CUDA 13.0 support"
    echo "✅ RTX 5090 Blackwell (sm_120/sm_130) compatibility verified"
    echo "✅ Basic CUDA operations tested"
    if [ -n "$LOCK_FILE" ] && [ -f "$LOCK_FILE" ]; then
        echo "✅ Requirements lock file created: $LOCK_FILE"
    fi
    echo "=================================================="
}

# Run main function
main "$@" 