## Vision Model Addition Workflow (Static Catalog)

Maintainer workflow for adding and measuring a GPU vision model (VLM) with mmproj support.

**Current Implementation Status:**
- ✅ `--mmproj`: Fully implemented (passed to measurement job and inference engine)
- ✅ `--vision-architecture`: Fully implemented (stored in catalog, used by inference engine)
- ⚠️ `--tokens-per-image`: CLI flag exists but **not yet implemented** in gateway workflow
  - The flag stores metadata in the catalog but is not used during measurement or inference
  - Omit this flag until gateway implementation is complete

### Quick env setup
```bash
export ROOT="/mnt/torus/projects/universal-llm-gateway"
export PY="$HOME/.venvs/universal/bin/python"
export GATEWAY_URL="http://localhost:9998"
export DEST="/mnt/torus/models"

export MODEL_FILE="ggml-model-Q8_0.gguf"
export MMPROJ_FILE="mmproj-model-f16.gguf"
export MODEL_PATH="$DEST/$MODEL_FILE"
export MMPROJ_PATH="$DEST/$MMPROJ_FILE"
export REPO="openbmb/MiniCPM-V-2_6-gguf"
export VISION_ARCH="minicpm_v"      # vision architecture

# NOTE: MODEL_ID will be set AFTER step 3 (generate)
# It must match the catalog entry key (lowercase-hyphenated)
# Example: export MODEL_ID="ggml-model-q8-0"
```

**Important**: Do not set `MODEL_ID` until after Step 3. The catalog generator creates a lowercase-hyphenated ID from the filename, and you must use that exact ID for measurement commands.

### Optional: download model files
- From Hugging Face (both model and mmproj):
```bash
huggingface-cli download "$REPO" "$MODEL_FILE" "$MMPROJ_FILE" \
  --local-dir "$DEST" --local-dir-use-symlinks False
```

- Or download mmproj separately if needed:
```bash
$HOME/.venvs/universal/bin/huggingface-cli download "$REPO" "$MMPROJ_FILE" \
  --local-dir "$DEST" --local-dir-use-symlinks False
```

**Verify mmproj downloaded correctly** (should be several GB, not KB):
```bash
ls -lh "$MMPROJ_PATH"
file "$MMPROJ_PATH"  # Should show "data" or "GGUF", not "HTML document"
```

### Pre-test: start services from workspace code
```bash
cd "$ROOT"
systemctl --user stop super-universal-llm-gateway super-universal-stargate
./services/_universal-llm-gateway/scripts/start-gateway.sh debug &
./services/universal-stargate/scripts/start-stargate.sh debug &
```

Check readiness:
```bash
curl -s "$GATEWAY_URL/api/v1/models" | head -20
curl -s http://localhost:9999/api/v1/health
```

### Step 1: Verify model origin
```bash
cd "$ROOT"
$PY -m scripts.model_manager verify "$MODEL_PATH" \
  --repo "$REPO" \
  --network
```

Expected: ✅ Verified.

### Step 2: Measure without catalog update (test only)
```bash
cd "$ROOT"
$PY -m scripts.model_manager measure "$MODEL_PATH" \
  --gpu \
  --mmproj "$MMPROJ_PATH" \
  --vision-architecture "$VISION_ARCH" \
  --contexts 32768,16384,8192,4096
```

Expected: VRAM usage printed per context; no catalog update.

**Note on `--safety-margin`**: For hybrid mode (partial GPU offload), the system applies a safety margin by default (-2 layers from max found). Add `--safety-margin 0` to use the maximum layers that fit, or `--safety-margin 1` for a smaller margin. This only applies to the first hybrid context measurement.

### Step 3: Add to catalog (static)
Check if present:
```bash
$PY -m scripts.model_manager list --static | grep "$MODEL_ID" || true
```

If missing, add:
```bash
cd "$ROOT"
$PY -m scripts.model_manager --gateway "$GATEWAY_URL" generate "$MODEL_PATH" \
  --repo "$REPO" \
  --static \
  --add-verified \   # optional: populate verified_models.json for future downloads
  --network \
  --mmproj "$MMPROJ_PATH" \
  --vision-architecture "$VISION_ARCH" \
  --tokens-per-image 2880   # Required for generate command; use architecture default
```

Expected: base entry in static catalog (`config/model_catalog.yaml`). If `--add-verified` is included, also records download info in `/mnt/torus/models/verified_models.json`.

**IMPORTANT**: After generating the catalog entry, identify the catalog ID that was created. This is a lowercase-hyphenated identifier derived from the model filename (e.g., `qwen2-vl-ocr-2b-instruct-q8-0` for `Qwen2-VL-OCR-2B-Instruct.Q8_0.gguf`).

Check the generated entry:
```bash
grep "^  [a-z]" "$ROOT/config/model_catalog.yaml" | tail -5
```

Then export the catalog ID (NOT the filename):
```bash
export MODEL_ID="qwen2-vl-ocr-2b-instruct-q8-0"  # Use the catalog ID from the grep output
```

**Note**: The catalog ID must match the key in the YAML file. Using the filename instead will fail with "model not found in catalog".

### Step 4: Measure with catalog update (static)
```bash
cd "$ROOT"
$PY -m scripts.model_manager --gateway "$GATEWAY_URL" measure "$MODEL_ID" \
  --mmproj "$MMPROJ_PATH" \
  --vision-architecture "$VISION_ARCH" \
  --update-catalog \
  --static
```

Expected: profiles saved to static catalog with vision metadata; activated GPU contexts updated; no warnings.

**Notes**:
- GPU mode with hybrid offload is now the default. The `--gpu` flag is no longer needed.
- The `--tokens-per-image` flag is **required** when using `--mmproj` in the `generate` command (CLI validation). While this value is stored in the catalog, it's not yet used by the gateway during measurement or inference. Use the default value for your architecture (see Vision Architecture Options below).
- For hybrid mode measurements, add `--safety-margin 0` if you want to use the maximum GPU layers found (no safety buffer). Default is `-2` layers for stability.

### Step 5: Review catalog entry
```bash
$PY -m scripts.model_manager --gateway "$GATEWAY_URL" show "$MODEL_ID" --static
```

Verify:
- `gpu-batch512` exists with contexts and VRAM values
- `activated_gpu_contexts` includes highest measured context
- `metadata.is_vision_model` = `true`
- `metadata.vision_architecture` = `$VISION_ARCH`
- `configurations.base_loader.clip_model_path` = `$MMPROJ_PATH`
- `configurations.base_loader.vision_architecture` = `$VISION_ARCH`
- `download.huggingface.repo` = `$REPO`; `file` = `$MODEL_FILE`
- `quant` = 8 (for Q8_0)

### Step 6: Confirm catalog location
```bash
ls -lh "$ROOT/config/model_catalog.yaml"
grep -A 60 "$MODEL_ID:" "$ROOT/config/model_catalog.yaml" | head -80
```

Expected: entry present at workspace-root catalog with vision parameters.

### Notes
- Static catalog: `$ROOT/config/model_catalog.yaml`
- Dynamic catalog (user-writable): `$HOME/.gateway/catalog.yaml`
- Verified registry (optional, for `model-manager download`): `/mnt/torus/models/verified_models.json`
- **Model IDs are lowercase with hyphens** (e.g., `ggml-model-q8-0`, `qwen2-vl-ocr-2b-instruct-f16`)
  - These are catalog keys, NOT filenames
  - The `generate` command creates these from filenames automatically
  - Always use the catalog ID (from Step 3) for measurement and show commands
  - Using the filename instead will fail with "model not found in catalog"

### Vision Architecture Options

Supported architectures (auto-detectable from model ID in most cases):
- `minicpm_v` - MiniCPM-V models
- `qwen2_vl` - Qwen2-VL models
- `llava_1_5` - LLaVA 1.5
- `llava_1_6` - LLaVA 1.6 / Next
- `moondream` - Moondream models

If `--vision-architecture` is omitted, the system attempts auto-detection from the model ID. If auto-detection fails, specify the architecture explicitly.

---

## Alternative Example: Directory-based Model (AWQ/Safetensors)

Some vision models (e.g., AWQ format) use multiple files and need directory-based downloads.

### Quick env setup (directory-based)
```bash
export ROOT="/mnt/torus/projects/universal-llm-gateway"
export PY="$HOME/.venvs/universal/bin/python"
export GATEWAY_URL="http://localhost:9998"
export DEST="/mnt/torus/models"

export REPO="cyankiwi/ERNIE-4.5-VL-28B-A3B-Thinking-AWQ-4bit"
export MODEL_ID="ernie-45-vl-28b-a3b-awq-4bit"
export MODEL_DIR="$DEST/$MODEL_ID"
export MODEL_FILE="model.safetensors"   # main weight file
export VISION_ARCH="ernie4_5_vl"        # adjust to actual architecture
```

### Download entire repository
```bash
huggingface-cli download "$REPO" \
  --local-dir "$MODEL_DIR" \
  --local-dir-use-symlinks False
```

### Generate catalog entry (static)
```bash
cd "$ROOT"
$PY -m scripts.model_manager --gateway "$GATEWAY_URL" generate "$MODEL_DIR" \
  --repo "$REPO" \
  --file "$MODEL_FILE" \
  --static \
  --add-verified \   # optional
  --network
```

### Measure with vision support
```bash
cd "$ROOT"
$PY -m scripts.model_manager --gateway "$GATEWAY_URL" measure "$MODEL_ID" \
  --gpu \
  --vision-architecture "$VISION_ARCH" \
  --update-catalog \
  --static
```

Note: Some vision models (like vLLM-based ones) may not require a separate mmproj file. In such cases, omit the `--mmproj` flag and `--vision-architecture`. For hybrid mode, add `--safety-margin 0` to use maximum layers found (default: `-2`).

---

## For Subsequent Users: Download via Tooling

Once the model is in the verified registry (after adding with `--add-verified`), other users can download it via:

```bash
~/.venvs/universal/bin/python -m scripts.model_manager \
  download "$MODEL_ID" \
  --dest /mnt/torus/models \
  --network
```

---

## Alternative: Promote Existing Catalog Entry

If a model is already in the catalog (static or dynamic) but not in the verified registry:

```bash
~/.venvs/universal/bin/python -m scripts.model_manager \
  --gateway http://localhost:9998 \
  promote-to-verified "$MODEL_ID" \
  --network
```

This fetches the model's download metadata from the catalog and adds it to the verified registry.

---

## Example: LLaVA 1.6 13B Q8_0

Complete workflow for adding `llava-v1.6-vicuna-13b.Q8_0.gguf`:

### Quick env setup
```bash
export ROOT="/mnt/torus/projects/universal-llm-gateway"
export PY="$HOME/.venvs/universal/bin/python"
export GATEWAY_URL="http://localhost:9998"
export DEST="/mnt/torus/models"

export MODEL_FILE="llava-v1.6-vicuna-13b.Q8_0.gguf"
export MMPROJ_FILE="mmproj-model-f16.gguf"
export MODEL_PATH="$DEST/$MODEL_FILE"
export MMPROJ_PATH="$DEST/$MMPROJ_FILE"
export REPO="cjpais/llava-v1.6-vicuna-13b-gguf"
export VISION_ARCH="llava_1_6"
```

### Download model and mmproj
```bash
huggingface-cli download "$REPO" "$MODEL_FILE" "$MMPROJ_FILE" \
  --local-dir "$DEST" --local-dir-use-symlinks False
```

### Verify download
```bash
ls -lh "$MODEL_PATH" "$MMPROJ_PATH"
file "$MMPROJ_PATH"  # Should show "data" or "GGUF", not "HTML document"
```

### Generate catalog entry
```bash
cd "$ROOT"
$PY -m scripts.model_manager --gateway "$GATEWAY_URL" generate "$MODEL_PATH" \
  --repo "$REPO" \
  --static \
  --add-verified \
  --network \
  --mmproj "$MMPROJ_PATH" \
  --vision-architecture "$VISION_ARCH" \
  --tokens-per-image 2880
```

### Get the catalog ID
```bash
# The catalog ID will be: llava-v1-6-vicuna-13b-q8-0
export MODEL_ID="llava-v1-6-vicuna-13b-q8-0"
```

### Measure with catalog update
```bash
cd "$ROOT"
$PY -m scripts.model_manager --gateway "$GATEWAY_URL" measure "$MODEL_ID" \
  --gpu \
  --mmproj "$MMPROJ_PATH" \
  --vision-architecture "$VISION_ARCH" \
  --update-catalog \
  --static \
  --contexts 4096,2048,1024
```

**Note**: Use the model's actual `training_context_length` (4096 for this model) as the primary context. The vision registry default (8192) may not match your specific model - always check the catalog's `training_context_length` field.
