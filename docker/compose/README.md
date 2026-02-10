# Docker Compose Configurations

This directory contains Docker Compose configurations for various deployment scenarios.

## Files

### Shared Configuration

- **`engine-optimizations.env`** - Shared inference engine optimization environment variables
  - vLLM optimizations (startup speed, CPU usage)
  - OpenMP optimizations (llama.cpp threading)
  - Automatically loaded by all GPU-enabled containers
  - Override any setting in deployment-specific `.env` files

### GPU Edge Deployments (Federated Architecture)

- **`gpu-edge-localhost.yml`** - Edge node running on localhost
- **`gpu-edge-jupiter.yml`** - Edge node running on jupiter host
- **`gpu-edge-template.yml`** - Template for creating new edge node configurations

Edge nodes are **passive** - they accept incoming connections from Master/Relay nodes via Unix sockets. Network isolated for security.

### Standalone Gateway

- **`gateway-gpu.yml`** - Standalone GPU gateway (TCP mode, no federation)
- **`gateway-gpu-windows.yml`** - Windows-specific GPU gateway configuration

### Development

- **`dev-local.yml`** - Local development setup
- **`unix-socket.yml`** - Unix socket testing configuration
- **`local-remote.yml`** - Local master/remote testing

### Testing

- **`golem-federated-test.yml`** - Federated topology testing (3-node setup: 1 master + 2 edge containers)
  - See **[README-golem-testing.md](./README-golem-testing.md)** for full setup guide
  - Each node container runs **Gateway + Edge Stargate (TCP)**; Master initiates WS telemetry (no HTTP polling)

## Creating a New Edge Node

### 1. Copy the Template

```bash
cd docker/compose
cp gpu-edge-template.yml gpu-edge-<hostname>.yml
```

### 2. Replace Placeholders

Edit `gpu-edge-<hostname>.yml` and replace all `<REPLACEME>` placeholders:

- `<HOSTNAME>` - Short hostname (e.g., `mars`, `venus`)
- `<HOSTNAME_UPPER>` - Uppercase hostname for env vars (e.g., `MARS`, `VENUS`)
- `<HOST_MODELS_PATH>` - Path to models directory on host (e.g., `/mnt/models`)

### 3. Create Environment File

```bash
# Copy from existing edge config
cp ../../.env.gpu-edge-localhost ../../.env.gpu-edge-<hostname>

# Edit federation keys
vi ../../.env.gpu-edge-<hostname>
```

Required variables in `.env.gpu-edge-<hostname>`:
```bash
# Federation authentication key (generate with: openssl rand -hex 32)
FEDERATION_KEY_EDGE_<HOSTNAME_UPPER>=<generated-key>

# Optional overrides (defaults from engine-optimizations.env)
# LOG_LEVEL=DEBUG
# OMP_NUM_THREADS=16  # Override auto-detection if needed
```

### 4. Create Stargate Configuration

```bash
# Copy from existing edge config
cp ../../config/stargate_config.gpu-edge-localhost.yaml \
   ../../config/stargate_config.gpu-edge-<hostname>.yaml

# Update socket path and remote_id
vi ../../config/stargate_config.gpu-edge-<hostname>.yaml
```

Update in stargate config:
```yaml
mode: edge
bind:
  socket: /tmp/universal-protocol/edge-<hostname>.sock
remote_id: edge-<hostname>
```

### 5. Test the Configuration

```bash
# Start the edge node
docker compose -f docker/compose/gpu-edge-<hostname>.yml up -d

# Check logs
docker logs edge-<hostname> --tail 50

# Verify socket exists
ls -la /tmp/universal-protocol/edge-<hostname>.sock

# Check environment variables
docker exec edge-<hostname> env | grep -E "VLLM|OMP" | sort
```

## Environment Variable Precedence

Environment variables are loaded in this order (later overrides earlier):

1. **`engine-optimizations.env`** - Shared inference optimizations (base defaults)
2. **`.env.gpu-edge-<hostname>`** - Deployment-specific configuration (federation keys, overrides)
3. **`environment` section** - Direct inline overrides in docker-compose.yml

### Example Override

To override vLLM attention backend for a specific node:

**In `.env.gpu-edge-mars`:**
```bash
VLLM_ATTENTION_BACKEND=FLASHINFER  # Override from FLASH_ATTN
```

## Shared Inference Optimizations

All GPU containers automatically load `engine-optimizations.env` which includes:

### vLLM Optimizations
- `VLLM_SLEEP_WHEN_IDLE=1` - Reduces idle CPU usage
- `VLLM_ENABLE_INDUCTOR_MAX_AUTOTUNE=0` - Prevents 30-60s startup delays
- `VLLM_ENABLE_INDUCTOR_COORDINATE_DESCENT_TUNING=0` - Prevents tuning delays
- `VLLM_ATTENTION_BACKEND=FLASH_ATTN` - Optimized attention
- `VLLM_USE_TRITON_FLASH_ATTN=0` - Native CUDA (more stable)

### OpenMP Optimizations (llama.cpp)
- `OMP_SCHEDULE=static` - Static work distribution
- `OMP_DYNAMIC=false` - Disable dynamic adjustment
- `OMP_NESTED=false` - Disable nested parallelism
- Thread counts (`OMP_NUM_THREADS`, etc.) - Auto-detected by default

### When to Override

Override in deployment-specific `.env` files when:
- Different hardware requires different thread counts
- Specific models benefit from different attention backends
- Testing alternative configurations

## Architecture Overview

### Edge Node (Passive)
```
Master/Relay Stargate
  └─> Unix Socket
      └─> Edge Stargate (in container)
          └─> Gateway (internal TCP 9998)
              └─> Worker Processes
```

### Standalone Gateway (Active)
```
Client
  └─> HTTP/TCP (port 9998)
      └─> Gateway
          └─> Worker Processes
```

## Troubleshooting

### Socket not appearing

```bash
# Check container logs
docker logs edge-<hostname> --tail 100

# Verify socket directory is mounted
docker exec edge-<hostname> ls -la /tmp/universal-protocol/

# Check golem-start.sh is running
docker exec edge-<hostname> ps aux | grep golem
```

### Federation authentication failing

```bash
# Verify federation key is set
docker exec edge-<hostname> env | grep FEDERATION_KEY

# Check key matches in connecting relay/master .env file
```

### Environment variables not applied

```bash
# Check that engine-optimizations.env is loaded
docker exec edge-<hostname> env | grep VLLM

# Verify env_file paths in docker-compose.yml
docker compose -f gpu-edge-<hostname>.yml config | grep -A5 env_file
```

## Event Monitoring

### Quick Setup

```bash
# Enable socket AND persistence
export DEBUG_EVENT_SOCKET=/sockets/events.sock
export DEBUG_EVENT_PERSIST=true

docker compose -f docker/compose/gateway-gpu.yml up -d
```

### Real-Time Monitoring

```bash
nc -U /tmp/universal-sockets/events.sock | jq -c '.'
```

### Post-Mortem Debugging (Agent-Friendly)

Events are persisted to `/tmp/stargate-events/` inside container:

```bash
# Copy events from container
docker cp CONTAINER:/tmp/stargate-events/current.jsonl ./events.jsonl

# Or exec into container
docker exec CONTAINER cat /tmp/stargate-events/current.jsonl | jq -c '.'
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG_EVENT_SOCKET` | (none) | Unix socket path for real-time streaming |
| `DEBUG_EVENT_PERSIST` | `false` | Enable file persistence |
| `DEBUG_EVENT_PERSIST_DIR` | `/tmp/stargate-events` | Persistence directory |

**Note**: GPU relay configs enable persistence by default. Environment variables override YAML config.

**Full documentation:** `.cursor/rules/event-debugging_ws.mdc`

## Related Documentation

- **Deployment Guide**: `../../examples/vps-deployment-guide.md`
- **Federation Architecture**: `../../services/universal-stargate/systems/federation/REFERENCE.md`
- **Edge Node Setup**: `../../scripts/deploy-gpu-relay.sh`
- **Engine Optimizations**: `./engine-optimizations.env` (inline documentation)
