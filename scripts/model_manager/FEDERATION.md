# model_manager Federation Support

## Overview

The `model_manager` tool is **federation-only** and routes all API requests through Stargate's federation proxy layer to reach isolated Gateways running in containers.

## Architecture

```
CLI → Stargate:9999 (/gateway/models)
    → Master/Relay forwards to Edge (/api/v1/federation/gateway/models)
    → Edge proxies to local Gateway (/api/v1/models)
```

## Usage

### Generate Catalog Entry via Stargate

```bash
export ROOT="/mnt/torus/projects/universal-llm-gateway"
export PY="$HOME/.venvs/universal/bin/python"
export STARGATE_URL="http://localhost:9999"
export DEST="/mnt/torus/models"

export MODEL_FILE="Llama-3-8B-Lexi-Uncensored.Q8_0.gguf"
export MODEL_PATH="$DEST/$MODEL_FILE"
export REPO="QuantFactory/Llama-3-8B-Lexi-Uncensored-GGUF"

# Generate catalog entry via Stargate (writes to static catalog)
$PY -m scripts.model_manager --stargate "$STARGATE_URL" generate "$MODEL_PATH" \
  --repo "$REPO" \
  --static \
  --add-verified \
  --network
```

### Available Options

| Flag | Description | Required |
|------|-------------|----------|
| `--stargate URL` | Stargate URL for federated access | Yes (or use --output) |
| `--output FILE` | Write directly to file (bypass API) | Alternative to --stargate |
| `--static` | Write to static catalog (maintainer mode) | No (default: dynamic) |
| `--add-verified` | Add to verified registry | No |
| `--network` | Allow HuggingFace network access | Required with --add-verified |

### File-Based Alternative

If you don't want to use the API:

```bash
$PY -m scripts.model_manager generate "$MODEL_PATH" \
  --repo "$REPO" \
  --output config/models/text_llm/llama-cpp/model-name.yaml \
  --add-verified \
  --network
```

## Commands

### `generate` - Federation-Ready ✅
Routes through Stargate to add models to catalog.

### `measure` - Federation-Ready ✅
Already uses federated endpoints correctly.

```bash
$PY -m scripts.model_manager measure model-id \
  --stargate http://localhost:9999 \
  --gpu \
  --update-catalog \
  --static
```

## Troubleshooting

### "Stargate not reachable at http://localhost:9999"

**Cause**: Stargate is not running or not accessible

**Fix**:
1. Check Stargate health: `curl http://localhost:9999/health`
2. Check federation status: `curl http://localhost:9999/api/v1/gateways/status/full`
3. Or use file-based catalog: `--output /path/to/file.yaml`

### "Model not written to catalog"

**Cause**: Gateway is isolated and `--stargate` flag not used

**Fix**: Always use `--stargate` for API-based operations:
```bash
$PY -m scripts.model_manager --stargate http://localhost:9999 generate ...
```

## Migration from Legacy --gateway Flag

The `--gateway` flag has been **removed**. All API operations now route through Stargate.

**Before (deprecated):**
```bash
# ❌ No longer supported
$PY -m scripts.model_manager --gateway http://localhost:9998 generate ...
```

**After:**
```bash
# ✅ Use --stargate
$PY -m scripts.model_manager --stargate http://localhost:9999 generate ... --static

# ✅ Or use file output
$PY -m scripts.model_manager generate ... --output config/models/path/model.yaml
```

## Environment Variable

Set default Stargate URL:

```bash
export STARGATE_URL="http://localhost:9999"
# Then omit --stargate flag (uses default)
```

## Endpoint Reference

| Operation | Endpoint | Flow |
|-----------|----------|------|
| Add Model | `POST /gateway/models` | Stargate → Edge → Gateway |
| Get Config | `GET /gateway/models/{id}/config` | Stargate → Edge → Gateway |
| Measure | `POST /gateway/jobs` | Stargate → Edge → Gateway |
| Status | `GET /gateway/status/resources` | Stargate → Edge → Gateway |

## See Also

- [Federation Reference](../services/universal-stargate/systems/federation/REFERENCE.md)
- [Relay Topology Deployment](../scripts/deploy-gpu-relay.sh)
- [Adding Model Workflow](../examples/adding-model-workflow-cpu.md)
