# model_manager CLI

CLI tooling for model catalog management. Supports both API-driven and file-based operations.

## Structure

```
scripts/model_manager/
├── cli.py              # Entry point, global args (--gateway, --catalog, etc.)
├── cli_parsers.py      # Subcommand argument definitions
├── api_client.py       # GatewayAPIClient - GET /api/v1/catalog/*
├── config.py           # Config paths, defaults
├── registry.py         # File-based Catalog/VerifiedRegistry classes
├── huggingface.py      # HF metadata extraction
└── commands/
    ├── catalog.py      # discover, generate, list, info
    ├── local_catalog.py # init, validate, update, remove, export
    ├── measure.py      # measure, remeasure (API-driven jobs)
    └── verify.py       # verify, download
```

## Key Commands

| Command | Description | API? |
|---------|-------------|------|
| `generate <path>` | Generate catalog entry from model file | File only* |
| `list` | List models | Yes |
| `info <id>` | Show model details | Yes |
| `measure <id>` | Measure VRAM/RAM profiles | Yes (jobs API) |
| `remeasure --all` | Re-measure all models | Yes (jobs API) |
| `discover <dir>` | Scan directory for uncataloged models | No |
| `verify <path>` | Verify model against HuggingFace | No |
| `download <id>` | Download from verified registry | No |

*`generate` needs API support for `POST /api/v1/models` - currently file-only.

## API Client

`GatewayAPIClient` in `api_client.py`:
- `health_check()` → `GET /health`
- `list_models()` → `GET /api/v1/catalog/models/list`
- `get_model(id)` → `GET /api/v1/catalog/models/{id}`
- **TODO**: `add_model()` → `POST /api/v1/models`

## Usage

```bash
# Run from project root
cd /mnt/torus/projects/universal-llm-gateway

# List models via API
python -m scripts.model_manager --gateway http://localhost:9998 list

# Generate catalog entry (stdout)
python -m scripts.model_manager generate /path/to/model.gguf

# Generate and write to file
python -m scripts.model_manager generate /path/to/model.gguf \
  -o config/model_catalog.yaml --append

# Remeasure all GGUF models
python -m scripts.model_manager --gateway http://localhost:9998 \
  remeasure --all --gguf-only
```

## Entry Generation

`generate` uses `inference_djinn.catalog.CatalogEntryGenerator` to extract:
- Architecture, parameters, context length from GGUF metadata
- HuggingFace repo/file tracing with `--repo`/`--file`
- SHA256 hash for verification

## Federated Deployment

In federated (relay topology) deployments, measurements must be run **on the host** where the Edge container is running.

### Why Local Execution?

1. **Gateway Isolation**: Gateway runs inside Edge container, not directly accessible
2. **Catalog Locality**: Static catalog lives on Edge's filesystem
3. **Federation Invariant**: All Gateway access routes through Edge Stargate

### Workflow

```bash
# SSH to the host running the Edge container
ssh user@edge-host

# Run measurement through local Stargate
./scripts/model_manager.py measure my-model-16k --stargate http://localhost:9999

# Measurement routes: Stargate → Edge → Gateway
# Catalog updates apply to Edge's static catalog
```

### Multi-Host Measurement

For measuring across multiple hosts:

```bash
# Host 1 (localhost Edge)
ssh user@localhost
./scripts/model_manager.py measure model-a --stargate http://localhost:9999

# Host 2 (jupiter Edge)
ssh user@jupiter
./scripts/model_manager.py measure model-b --stargate http://localhost:9999
```

### Architecture

```
┌────────────────────────────────────────────────────────────┐
│ Host (where you run measure.py)                            │
│                                                            │
│  measure.py → Master/Relay Stargate :9999                  │
│                       │                                    │
│                       │ Unix socket                        │
│                       ▼                                    │
│  ┌────────────────────────────────────────────────────┐   │
│  │ Edge Container                                     │   │
│  │  Edge Stargate → Gateway :9998                     │   │
│  │                      │                             │   │
│  │                      ▼                             │   │
│  │            Static Catalog (updated here)           │   │
│  └────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

## Gateway Endpoints Used

- `GET /health` - Availability check
- `GET /api/v1/catalog/models/list` - List models
- `GET /api/v1/catalog/models/{id}` - Model details
- `POST /api/v1/jobs` - Create measurement job
- `GET /api/v1/jobs/{id}/logs` - Stream job logs (SSE)
- `POST /api/v1/models` - Add model (not yet in CLI)
