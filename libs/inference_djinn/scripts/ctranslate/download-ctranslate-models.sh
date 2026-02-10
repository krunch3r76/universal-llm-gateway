#!/bin/bash
# download-ctranslate-models.sh
# Downloads and converts Polish-English translation models to CTranslate2 format

set -e

MODEL_DIR="${MODEL_PATH_ROOT:-/mnt/torus/models}/ctranslate"
VENV="${GATEWAY_VENV:-$HOME/.venvs/universal}"

echo "=== CTranslate2 Model Download & Conversion ==="
echo "Target directory: $MODEL_DIR"
echo "Using venv: $VENV"
echo ""

# Activate venv
source "$VENV/bin/activate"

# Ensure dependencies are installed
pip install --quiet ctranslate2 transformers sentencepiece

# Create target directory
mkdir -p "$MODEL_DIR"
cd "$MODEL_DIR"

# ============================================================================
# 1. OPUS-MT Polish→English
# ============================================================================
echo ""
echo "=== Converting Helsinki-NLP/opus-mt-pl-en ==="
if [ -d "opus-mt-pl-en" ]; then
    echo "Already exists, skipping..."
else
    ct2-transformers-converter \
        --model Helsinki-NLP/opus-mt-pl-en \
        --output_dir opus-mt-pl-en \
        --quantization int8
    echo "✓ opus-mt-pl-en converted"
fi

# ============================================================================
# Note: OPUS-MT-en-pl does not exist on HuggingFace.
# Flan-T5 translator (below) is bidirectional and handles English→Polish.
# ============================================================================

# ============================================================================
# 2. Flan-T5 Translator (Bidirectional: trained on 70M Polish-English pairs)
#    Handles both PL→EN and EN→PL directions
#    Note: Model is missing spiece.model file, so we download locally and add it
# ============================================================================
echo ""
echo "=== Converting sdadas/flan-t5-base-translator-en-pl ==="
if [ -d "flan-t5-translator" ]; then
    echo "Already exists, skipping..."
else
    echo "Note: This conversion may take several minutes..."
    echo "Step 1: Downloading model locally..."
    
    # Create temporary directory for local model
    TEMP_MODEL_DIR="/tmp/flan-t5-local-$$"
    mkdir -p "$TEMP_MODEL_DIR"
    
    # Download model locally and add missing spiece.model
    python3 << PYTHON_SCRIPT
import sys
from huggingface_hub import snapshot_download
from huggingface_hub import hf_hub_download
import shutil
import os

model_id = 'sdadas/flan-t5-base-translator-en-pl'
local_dir = "$TEMP_MODEL_DIR"

print(f"Downloading {model_id} to {local_dir}...")
snapshot_download(model_id, local_dir=local_dir)

# The model is missing spiece.model, get it from base T5 model
print("Fetching missing spiece.model from google/flan-t5-base...")
try:
    spiece_path = hf_hub_download('google/flan-t5-base', 'spiece.model')
    target = os.path.join(local_dir, 'spiece.model')
    shutil.copy(spiece_path, target)
    print(f"✓ Copied spiece.model to {target}")
except Exception as e:
    print(f"⚠ Warning: Could not fetch spiece.model: {e}")
    sys.exit(1)
PYTHON_SCRIPT
    
    if [ $? -ne 0 ]; then
        echo "⚠ Failed to download model locally"
        rm -rf "$TEMP_MODEL_DIR"
    else
        echo "Step 2: Converting from local directory..."
        if ct2-transformers-converter \
            --model "$TEMP_MODEL_DIR" \
            --output_dir flan-t5-translator \
            --quantization int8 > /tmp/flan-t5-conversion.log 2>&1; then
            echo "✓ flan-t5-translator converted"
            rm -rf "$TEMP_MODEL_DIR"
        else
            echo "⚠ flan-t5-translator conversion failed (check /tmp/flan-t5-conversion.log)"
            echo "   Error details:"
            tail -10 /tmp/flan-t5-conversion.log | sed 's/^/     /'
            rm -rf "$TEMP_MODEL_DIR"
        fi
    fi
fi

# ============================================================================
# 3. NLLB-200 (Pre-converted, download directly)
#    Note: 600M version not available pre-converted, using 1.3B instead
# ============================================================================
echo ""
echo "=== Downloading OpenNMT/nllb-200-distilled-1.3B-ct2-int8 ==="
if [ -d "nllb-200-1.3b" ]; then
    echo "Already exists, skipping..."
else
    # Use huggingface-cli to download pre-converted model
    huggingface-cli download \
        OpenNMT/nllb-200-distilled-1.3B-ct2-int8 \
        --local-dir nllb-200-1.3b \
        --local-dir-use-symlinks False
    echo "✓ nllb-200-1.3b downloaded"
fi

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "=== Conversion Complete ==="
echo "Models in $MODEL_DIR:"
ls -la "$MODEL_DIR"
echo ""
echo "Total size:"
du -sh "$MODEL_DIR"