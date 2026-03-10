## CPU Model Addition Workflow (Static Catalog)

Maintainer workflow for adding and measuring a CPU-profiled model in federated deployment.

**Architecture Context:**
- Static catalog writes go **directly to host filesystem** (`config/models/`)
- API operations (measurement jobs, model config fetch) route through **Stargate** (localhost:9999)
- Gateway is isolated in Docker container with read-only config mount

### Quick env setup
```bash
export ROOT="$PWD" # project directory
export PY="$HOME/.venvs/universal/bin/python"
export STARGATE_URL="http://localhost:9999"  # Master/Relay Stargate endpoint
export MODELS_DIR="$HOME/.models"

export MODEL_FILE="gemma-3-1b-it-Q6_K.gguf"
export REPO="bartowski/gemma-3-1b-it-GGUF"
export MODEL_PATH="$MODELS_DIR/$MODEL_FILE"
export MODEL_ID="gemma-3-1b-it-q6-k"
```

### Step 0: Download model (optional)
```bash
cd "$ROOT"
$PY -m scripts.model_manager download \
  --repo "$REPO" \
  --file "$MODEL_FILE" \
  --dest "$MODELS_DIR" \
  --network
```

Expected: file at `$MODEL_PATH`.

### Step 1: Verify model file
```bash
cd "$ROOT"
$PY -m scripts.model_manager verify "$MODEL_PATH" \
  --repo "$REPO" --network
```

Expected: ✅ Verified.

### Step 2: Generate catalog entry with static write

```bash
cd "$ROOT"
$PY -m scripts.model_manager generate "$MODEL_PATH" \
  --repo "$REPO" \
  --static \
  --add-verified
```

**What happens:**
- Writes directly to `config/models/text_llm/llama-cpp/$MODEL_ID.yaml` (host filesystem)
- **No API call** - `--stargate` flag is ignored for static writes
- Entry created with placeholder profiles (`profiles: {}`)

**Expected output:**
```
✅ gemma-3-1b-it-q6-k (created at /path/to/universal-llm-gateway/config/models/text_llm/llama-cpp/gemma-3-1b-it-q6-k.yaml)

1 model(s) written to static catalog
```

**Note:** Empty profiles are normal at this stage - measurement (Step 4) will populate them.

### Step 3: Verify model is in static catalog

```bash
cd "$ROOT"
# Check file exists
ls -lh "$ROOT/config/models/text_llm/llama-cpp/$MODEL_ID.yaml"

# List via catalog command
$PY -m scripts.model_manager list --static | grep -i "$MODEL_ID"
```

Expected: File exists and appears in static catalog list.

### Step 4: Measure and update profiles

**For CPU-only measurement:**
```bash
cd "$ROOT"
$PY -m scripts.model_manager measure "$MODEL_ID" \
  --stargate "$STARGATE_URL" \
  --cpu \
  --contexts 32768,16384,8192,4096 \
  --update-catalog \
  --static
```

**For GPU hybrid mode (default):**
```bash
cd "$ROOT"
$PY -m scripts.model_manager measure "$MODEL_ID" \
  --stargate "$STARGATE_URL" \
  --update-catalog \
  --static
```

**What happens:**
1. Measurement job runs on Gateway (via Stargate API)
2. CLI fetches results from Gateway
3. CLI writes profiles **directly to host filesystem** (`config/models/...`)

**Expected output:**
```
Job started: <job-id>
Streaming logs from /gateway/jobs/<job-id>/logs...
[measurement logs...]
✅ Job completed successfully

📝 Updating catalog with measurement results...
   Updated static catalog: /path/to/universal-llm-gateway/config/models/text_llm/llama-cpp/gemma-3-1b-it-q6-k.yaml
✅ Catalog updated successfully
```

**Note:** The `--safety-margin` option only applies to GPU hybrid mode measurements.

### Step 5: Review catalog entry

```bash
cd "$ROOT"
$PY -m scripts.model_manager show "$MODEL_ID" --full
```

Verify:
- `devices.cpu.profiles` has contexts with RAM values (if CPU measured)
- `devices.gpu.profiles` has contexts with VRAM values (if GPU measured)
- `download.huggingface.repo` = `$REPO`; `file` = `$MODEL_FILE`
- `metadata.quant` matches the model quantization

### Step 6: Confirm catalog location

```bash
cat "$ROOT/config/models/text_llm/llama-cpp/$MODEL_ID.yaml"
```

Expected: Entry present in split catalog structure under domain/engine path.

### Step 7: Reload catalog in Gateway

For static catalog changes to take effect, reload the Gateway:

```bash
# Option A: Restart via TUI (./manage → Services → Stop/Start Stargate)
# Option B: Manual restart (if baremetal)
pkill -f "universal-"; rm -f /tmp/universal-protocol/*.sock /tmp/process_ipc/*.sock
# Then start Stargate and Gateway again
```

### Step 8: Test inference

```bash
cd "$ROOT"
curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL_ID\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
    \"max_tokens\": 50
  }"
```

Expected: Response with generated text.

## Architecture: Static vs Local Catalog

| Operation | Static Catalog | Local Catalog |
|-----------|----------------|---------------|
| Write location | Host: `config/models/` | Host: `~/.gateway/catalog/` |
| Write method | CLI direct file write (metadata-only) | CLI direct file write (full entry) |
| Persistence | Version-controlled (git) | Per-install (`~/.gateway/`, gitignored) |
| Use case | Model metadata + provenance | Operational entries with measured profiles |
| Reload needed | Yes (trigger catalog reload) | Yes (trigger catalog reload) |

## Troubleshooting

**Issue:** Model not appearing in Gateway after static write

**Cause:** Gateway has read-only mount of `config/` - changes require reload

**Fix:** Restart Gateway/Stargate to pick up new static catalog entries

---

**Issue:** `HTTP 400: static_catalog_via_api_not_supported`

**Cause:** Attempted to use `--stargate` with `--static` (old behavior)

**Fix:** Remove `--stargate` flag when using `--static` for generate/measure catalog updates. Static writes bypass API entirely.

---

**Issue:** Renamed model file before verification - getting "File not found in repo" or "Hash mismatch" errors

**Cause:** The `verify` and `generate` commands try to match the local file against HuggingFace metadata using the filename. If you renamed the file from its original HuggingFace name, the tool can't find it in the repo to verify the hash.

**Fix Option A (Recommended):** Keep original filename until after catalog generation, then rename:

```bash
# 1. Use original HF filename during verification and generation
export HF_FILENAME="Gemma-The-Writer-J.GutenBerg-10B-D_AU-Q4_k_m.gguf"
export MODEL_PATH="$MODELS_DIR/$HF_FILENAME"

# 2. Verify and generate with original name
$PY -m scripts.model_manager verify "$MODEL_PATH" --repo "$REPO" --network
$PY -m scripts.model_manager generate "$MODEL_PATH" --repo "$REPO" --static --add-verified --network

# 3. Rename the file after catalog generation
export NEW_NAME="Gemma-The-Writer-10B-D_AU-Q4_k_m.gguf"
mv "$MODEL_PATH" "$MODELS_DIR/$NEW_NAME"

# 4. Update the catalog YAML to reference the new filename
# Edit config/models/text_llm/llama-cpp/<model-id>.yaml
# Change download.huggingface.file to your new filename
```

**Fix Option B:** Use `--file` flag but manually fix the catalog afterward:

```bash
# 1. Local file already renamed
export MODEL_FILE="Gemma-The-Writer-10B-D_AU-Q4_k_m.gguf"
export MODEL_PATH="$MODELS_DIR/$MODEL_FILE"
export HF_FILENAME="Gemma-The-Writer-J.GutenBerg-10B-D_AU-Q4_k_m.gguf"

# 2. Verify and generate using --file to specify HF filename for hash verification
$PY -m scripts.model_manager verify "$MODEL_PATH" --repo "$REPO" --file "$HF_FILENAME" --network
$PY -m scripts.model_manager generate "$MODEL_PATH" --repo "$REPO" --file "$HF_FILENAME" --static --add-verified --network

# 3. IMPORTANT: Manually update the generated catalog YAML
# The catalog will have 'file: <HF_FILENAME>' but needs 'file: <MODEL_FILE>'
# Edit config/models/text_llm/llama-cpp/<model-id>.yaml
# Change download.huggingface.file from HF_FILENAME to MODEL_FILE
```

**Note:** The `download.huggingface.file` field tells the Gateway where to find the model on disk. It must match your actual local filename, not the HuggingFace filename (unless they're the same).
