#!/usr/bin/env bash
set -euo pipefail

# Download Whisper models directly from OpenAI (privacy-focused, no HuggingFace)
# 
# Usage:
#   ./scripts/download_whisper_models.sh [model_size] [target_dir]
#
# Examples:
#   ./scripts/download_whisper_models.sh large-v3      # Download to MODEL_PATH_ROOT
#   ./scripts/download_whisper_models.sh medium /tmp/models/  # Download to custom dir
#   ./scripts/download_whisper_models.sh all           # Download all models

MODEL_SIZE="${1:-large-v3}"
TARGET_DIR="${2:-${MODEL_PATH_ROOT:-/mnt/torus/models}}"

# Ensure target directory exists
mkdir -p "$TARGET_DIR"

# Model URLs and checksums from OpenAI
declare -A MODELS=(
    ["large-v3"]="https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt"
    ["large-v2"]="https://openaipublic.azureedge.net/main/whisper/models/81f7c96c852ee8fc832187b0132e569d6c3065a3252ed18e56effd0b6a73e524/large-v2.pt"
    ["medium"]="https://openaipublic.azureedge.net/main/whisper/models/345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1/medium.pt"
    ["small"]="https://openaipublic.azureedge.net/main/whisper/models/9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794/small.pt"
    ["base"]="https://openaipublic.azureedge.net/main/whisper/models/465707469ff3a37a2b9b8d8f89f2f99de7299dac1de7e0bc3a15b73cf1c7a494/base.pt"
    ["tiny"]="https://openaipublic.azureedge.net/main/whisper/models/65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9/tiny.pt"
)

declare -A CHECKSUMS=(
    ["large-v3"]="e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb"
    ["large-v2"]="81f7c96c852ee8fc832187b0132e569d6c3065a3252ed18e56effd0b6a73e524"
    ["medium"]="345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1"
    ["small"]="9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794"
    ["base"]="465707469ff3a37a2b9b8d8f89f2f99de7299dac1de7e0bc3a15b73cf1c7a494"
    ["tiny"]="65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9"
)

download_model() {
    local size="$1"
    local url="${MODELS[$size]}"
    local checksum="${CHECKSUMS[$size]}"
    local target_file="$TARGET_DIR/${size}.pt"

    echo "📥 Downloading Whisper $size..."
    echo "   URL: $url"
    echo "   Target: $target_file"

    # Check if file already exists and matches checksum
    if [[ -f "$target_file" ]]; then
        echo "   ⏭️  File exists, verifying checksum..."
        if echo "$checksum  $target_file" | sha256sum -c --quiet 2>/dev/null; then
            echo "   ✅ Checksum verified, skipping download"
            return 0
        else
            echo "   ⚠️  Checksum mismatch, re-downloading..."
            rm -f "$target_file"
        fi
    fi

    # Download with progress bar
    if command -v wget >/dev/null 2>&1; then
        wget -O "$target_file" "$url" --progress=bar:force:noscroll
    elif command -v curl >/dev/null 2>&1; then
        curl -L -o "$target_file" "$url" --progress-bar
    else
        echo "❌ Error: Neither wget nor curl found. Please install one."
        exit 1
    fi

    # Verify checksum
    echo "   🔒 Verifying checksum..."
    if echo "$checksum  $target_file" | sha256sum -c --quiet; then
        echo "   ✅ Checksum verified successfully"
    else
        echo "   ❌ Checksum verification failed!"
        rm -f "$target_file"
        exit 1
    fi

    # Set permissions
    chmod 644 "$target_file"
    echo "   ✅ Downloaded successfully: $(du -h "$target_file" | cut -f1)"
    echo
}

# Main logic
if [[ "$MODEL_SIZE" == "all" ]]; then
    echo "📦 Downloading all Whisper models to $TARGET_DIR"
    echo
    for size in large-v3 medium small; do
        download_model "$size"
    done
    echo "✅ All models downloaded successfully!"
elif [[ -n "${MODELS[$MODEL_SIZE]:-}" ]]; then
    download_model "$MODEL_SIZE"
    echo "✅ Model downloaded successfully!"
else
    echo "❌ Error: Unknown model size '$MODEL_SIZE'"
    echo
    echo "Available sizes:"
    echo "  - large-v3 (1550M params, ~3GB) - Best quality"
    echo "  - large-v2 (1550M params, ~3GB) - Previous version"
    echo "  - medium   (769M params, ~1.5GB) - Good quality"
    echo "  - small    (244M params, ~488MB) - Fast"
    echo "  - base     (74M params, ~142MB) - Faster"
    echo "  - tiny     (39M params, ~73MB) - Fastest"
    echo "  - all      (downloads large-v3, medium, small)"
    echo
    echo "Usage:"
    echo "  $0 large-v3"
    echo "  $0 medium /custom/path"
    echo "  $0 all"
    exit 1
fi

# Display summary
echo
echo "📊 Downloaded files in $TARGET_DIR:"
ls -lh "$TARGET_DIR"/*.pt 2>/dev/null || echo "   (none found)"
echo
echo "Next steps:"
echo "  1. Verify models are in MODEL_PATH_ROOT: $TARGET_DIR"
echo "  2. Restart gateway: ./services/_universal-llm-gateway/scripts/start-gateway.sh debug"
echo "  3. Check logs: tail -f /tmp/logs/universal-llm-gateway/gateway.log | grep whisper"

