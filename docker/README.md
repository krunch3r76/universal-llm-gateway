# Docker Deployment Guide

This directory contains Docker configurations for deploying Universal LLM Gateway.

## Key Features

✅ **Platform-Agnostic**: No hard-coded CPU optimization settings  
✅ **Environment-First**: All configuration via environment variables  
✅ **Golem-Ready**: Optimized for Golem Network deployment  
✅ **CPU-Only**: Uses standard llama-cpp-python (no CUDA required)  
✅ **Flexible**: Supports gateway-only, gateway+stargate, or custom configurations  
✅ **Intel/AMD Support**: Optimized for x86_64 architectures (ARM not supported)  
✅ **GPU Support**: NVIDIA CUDA-enabled containers for local/cloud GPU deployments  
✅ **Windows Support**: GPU inference via Docker Desktop + WSL2  

## Files

**CPU-Only (Golem Network):**
- `dockerfiles/Dockerfile.golem-base` - Base image for Golem Network (runtime only, app deployed via tarball)
- `compose/golem-federated-test.yml` - Local federated testing (2 containers + master) - see `compose/README-golem-testing.md`
- `scripts/build/build-golem.sh` - Build script for CPU-only images (with AVX2/AVX512/generic options)
- `requirements.docker.txt` - Python dependencies (CPU-only)

**GPU-Enabled (Local/Cloud):**
- `dockerfiles/Dockerfile.gpu` - GPU-enabled Dockerfile with CUDA + vLLM support
- `compose/gateway-gpu.yml` - Gateway with GPU (Linux)
- `compose/gateway-gpu-windows.yml` - Gateway with GPU (Windows/WSL2)
- `scripts/build/build-gpu.sh` - Build script for GPU image

**Local Development (Unified Architecture):**
- `compose/local-remote.yml` - Local Remote Stargate + Gateway in Docker containers
- `compose/dev-local.yml` - Complete development stack (Remote + Gateway)

**Common:**
- `scripts/build/golem-start.sh` - Startup script with environment-based configuration
- `.dockerignore` - Exclude unnecessary files from build

## Build Architecture

### Multi-Stage Build

The Dockerfile uses a multi-stage build to minimize final image size:

**Stage 1: Builder**
- Base: `python:3.12-slim` + build tools (`build-essential`, `cmake`)
- Compiles `llama-cpp-python` from source with CPU-only flags:
  - `CMAKE_ARGS="-DGGML_BLAS=OFF -DGGML_CUDA=OFF -DGGML_METAL=OFF"`
  - `FORCE_CMAKE=1` (ensures source build, not prebuilt wheels)
- Installs all Python packages to `/build/packages`
- Size: ~3GB (includes compilers, build artifacts)

**Stage 2: Runtime**
- Base: `python:3.12-slim` (no build tools)
- Copies compiled packages from builder stage
- Includes only `curl` for runtime operations
- Size: ~1.5-2GB (50% smaller than single-stage build)

### Why CPU-Only Build Flags?

`llama-cpp-python` auto-detects GPU support during compilation. For Golem Network:
- Providers have unknown hardware (mostly CPU)
- CUDA/Metal dependencies increase image size significantly
- CPU-only build is portable across all x86_64 providers
- Explicit flags prevent accidental GPU detection on build machine

## Architecture Patterns

### Network-Isolated Gateway

All Gateway containers run with `network_mode: none` for security:
- No network access (only Unix socket communication)
- Prevents accidental network exposure
- Enforced in all compose files

### Remote Stargate Relay Pattern

Even for local development, requests flow through Remote Stargate:
- Master Stargate → Remote Stargate → Gateway (isolated)
- Uniform architecture locally and in production
- Same debugging/observability patterns everywhere

### Configuration Pattern

The Docker image uses a **default-config pattern** for robust deployments:

**At Build Time:**
- Configs baked into image at `config.default/` (immutable defaults)

**At Runtime (First Start):**
- `golem-start.sh` copies `config.default/` → `config/` (if not exists)
- Environment-specific overrides applied (e.g., Docker auth-disabled config)
- Services read from `config/` directory

**Benefits:**
- ✅ Defaults always available (immutable in image)
- ✅ Runtime configs isolated from image layers
- ✅ Config overrides never fail on missing directories
- ✅ Clean container restarts (configs persist in volumes)
- ✅ Environment-specific customization without image rebuilds

## Quick Start

### Build the Image (CPU-only for Golem Network)

#### Default Build (AVX2 Optimized - Recommended for Golem)

```bash
# From project root
cd /mnt/torus/projects/universal-llm-gateway

# Build with default AVX2 optimization (recommended for Golem Network)
docker/scripts/build/build-golem.sh
```

**Default CPU Optimization: AVX2** (x86-64-v3)
- ✅ 2-3x faster CPU inference vs generic build
- ✅ Broad compatibility (works on ~95% of servers from 2013+)
- ✅ **Recommended for Golem Network** (unknown provider hardware)
- ✅ Requires: Intel Haswell+ (2013+), AMD Excavator+ (2015+)

**Why CPU Optimization Matters Even More for CPU-only:**
- **No GPU fallback**: ALL processing happens on CPU
- **Performance critical**: 2-3x speedup makes CPU-only viable for many workloads
- **Quantized models**: AVX2 + FMA significantly speed up Q4/Q5/Q8 GGUF inference

#### Build with AVX-512 (Maximum Performance)

For known modern server deployments (NOT recommended for Golem):

```bash
# Build with AVX-512 optimization (4-6x faster, modern servers only)
CPU_OPTIMIZATION=avx512 docker/scripts/build/build-golem.sh
```

**AVX-512 CPU Optimization:** (x86-64-v4)
- ✅ 4-6x faster CPU inference vs generic build
- ✅ Includes AVX-512 VNNI for quantized models
- ✅ Best performance for CPU-only inference
- ⚠️ Requires: Intel Ice Lake+ (2019+), AMD Zen 4+ (2022+)
- ❌ **NOT recommended for Golem** (may not work on older provider hardware)

#### Build with Generic x86-64 (Maximum Portability)

For very old hardware or maximum compatibility:

```bash
# Build with generic x86-64 (slowest, maximum compatibility)
CPU_OPTIMIZATION=generic docker/scripts/build/build-golem.sh
```

**Generic CPU Optimization:** (x86-64 baseline)
- ⚠️ **Slow** CPU inference (no SIMD optimizations)
- ✅ Works on any x86-64 CPU
- ❌ Not recommended unless targeting very old hardware

**Build Process:**
- Multi-stage build for minimal image size
- **Builder stage**: Compiles `llama-cpp-python` with CPU-only flags + CPU optimizations
- **Runtime stage**: Only runtime dependencies, build tools removed
- Build time: ~5-10 minutes (depending on CPU, mostly llama-cpp-python compilation)
- Final image size: ~1.5-2GB (vs ~3GB with build tools included)

**Performance Comparison (CPU-only Inference):**

| Optimization | Speed vs Generic | Portability | Golem Recommended |
|-------------|------------------|-------------|-------------------|
| **AVX2** (default) | 2-3x faster | Intel 2013+, AMD 2015+ | ✅ Yes |
| **AVX-512** | 4-6x faster | Intel 2019+, AMD 2022+ | ❌ No (risky) |
| **Generic** | 1x (baseline) | Any x86-64 | ❌ No (too slow) |

### Local Testing

#### Golem-Style Federated Setup

Test locally with multiple containers simulating Golem nodes:

```bash
# Build base image
docker build -f docker/dockerfiles/Dockerfile.golem-base -t universal-llm-gateway:golem-base .

# Build application tarball
./scripts/build_golem_tarball.sh

# Quick start with helper script (recommended)
./scripts/test-golem-federated.sh start

# Or see full manual setup guide
cat docker/compose/README-golem-testing.md
```

This creates a 3-node federation (1 local master + 2 remote containers) that matches Golem's architecture (HTTP polling, no WebSocket).

**Adding models after build:**
```bash
# Copy model into running container
docker cp /path/to/model.gguf universal-gateway-golem:/app/models/

# Or bake models into image during build (see Golem Network Deployment section)
```

## Compose Files

### Local Remote Stargate (`local-remote.yml`)

For local development with the unified Remote Stargate architecture.

**Purpose**: Runs Remote Stargate and Gateway in Docker containers on the same machine as Master Stargate.

**Services**:
- `remote-stargate`: Local Remote Stargate (port 10999)
- `gateway`: Gateway in network-isolated container (`network_mode: none`)

**Usage**:
```bash
# Start local Remote + Gateway
docker compose -f docker/compose/local-remote.yml up -d

# View logs
docker compose -f docker/compose/local-remote.yml logs -f

# Stop
docker compose -f docker/compose/local-remote.yml down
```

**Configuration**:
- Remote Stargate exposes port 10999 for Master access
- Gateway uses Unix socket (`/sockets/gateway.sock`) shared via volume
- Gateway runs with `network_mode: none` (complete isolation)

**Use Case**: Local development requiring full federation path (Master → Remote → Gateway).

### Development Local Stack (`dev-local.yml`)

Complete local development stack including Master Stargate configuration.

**Purpose**: Full unified architecture on one machine (Master on host + Remote/Gateway in Docker).

**Services**:
- `remote-stargate`: Local Remote Stargate (port 10999)
- `gateway`: Network-isolated Gateway

**Usage**:
```bash
# Start via helper script (recommended)
./scripts/dev-start-local-remote.sh

# Or start directly
docker compose -f docker/compose/dev-local.yml up -d

# Stop
docker compose -f docker/compose/dev-local.yml down
pkill -f "universal-stargate"  # Stop Master on host
```

**Architecture**:
```
Client → Master Stargate (host:9999) → Remote Stargate (container:10999) → Gateway (isolated)
```

**Use Case**: Standard local development workflow.

**Adding models after build:**
```bash
# Copy model into running container
docker cp /path/to/model.gguf universal-gateway-golem:/app/models/

# Or bake models into image during build (see Golem Network Deployment section)
```

## Configuration

### Environment Variables

All configuration is via environment variables (no hard-coded settings):

#### Required

- `MODEL_PATH_ROOT`: Path to models directory (e.g., `/golem/models`)

#### Service Configuration

- `GATEWAY_HOST`: Gateway bind address (default: `0.0.0.0`)
- `GATEWAY_PORT`: Gateway port (default: `9998`)
- `STARGATE_HOST`: Stargate bind address (default: `0.0.0.0`)
- `STARGATE_PORT`: Stargate port (default: `9999`)
- `LOG_LEVEL`: Logging level (default: `info`)
- `ENVIRONMENT`: Environment mode (default: `default`, options: `debug`, `release`)

#### Resource Paths

- `WORKER_LOG_DIR`: Worker log directory (default: `/golem/logs/workers`)
- `SOCKET_DIR`: Socket directory (default: `/golem/output/sockets`)
- `MODEL_CACHE_DIR`: Model cache directory (default: `/golem/models`)

#### Features

- `ENABLE_MODEL_AVAILABILITY_CHECK`: Check model file availability (default: `true`)
  - When enabled, `/v1/models` only shows models with accessible file paths
  - Prevents "phantom" models from being advertised
  - Non-fatal: missing models are hidden, not an error
- `ENABLE_MANAGEMENT_API`: Enable management API (default: `true`)
- `DISABLE_HEALTH_CHECKING`: Disable health checks (default: `false`)
- `DEBUG_MODE`: Enable debug mode (default: `false`)
- `ENABLE_PROFILING`: Enable profiling (default: `false`)

#### Timeouts

- `PROCESS_STARTUP_TIMEOUT`: Process startup timeout in seconds (default: `300`)
- `GATEWAY_SHUTDOWN_GRACE`: Graceful shutdown timeout (default: `30`)

#### CPU Optimization (Optional - Advanced)

**These are NOT set by default** to allow libraries to auto-detect optimal values:

- `OMP_NUM_THREADS`: OpenMP threads (auto-detected if not set)
- `MKL_NUM_THREADS`: Intel MKL threads (auto-detected if not set, Intel CPUs only)
- `TOKENIZERS_PARALLELISM`: HuggingFace tokenizers parallelism (auto-detected if not set)

**Only set these if you need to override auto-detection for specific hardware.**

### Customizing Configuration

#### Method 1: Edit docker-compose.yml

```yaml
services:
  gateway:
    environment:
      - MODEL_PATH_ROOT=/custom/models
      - LOG_LEVEL=debug
      - GATEWAY_PORT=9998
```

#### Method 2: Override at Runtime

```bash
# Example with GPU compose
MODEL_PATH_ROOT=/custom/models \
LOG_LEVEL=debug \
docker compose -f docker/compose/gateway-gpu.yml up -d
```

#### Method 3: Custom Config Files

Mount a custom config directory to `/golem/config`:

```yaml
volumes:
  - ./my-custom-configs:/golem/config:ro
```

### Authentication and Security

**Docker/Golem deployments have authentication DISABLED by default** for isolated container environments.

#### Current Configuration

**Gateway:**
- WebSocket auth: **DISABLED** (`WS_AUTH_ENABLED=false`)
- All connections accepted without authentication

**Stargate:**
- API key auth: **DISABLED** (uses `stargate_config.docker.yaml`)
- All requests accepted without authentication

#### Why Authentication is Disabled

1. **Isolation**: Container runs in isolated environment
2. **Simplicity**: No API keys to manage for internal services
3. **Performance**: No auth overhead for internal communication

#### Network Security Notes

Docker networks use various IP ranges:
- Default bridge: `172.17.0.0/16`
- Custom networks: `172.18-31.x.x`
- Services in same container: communicate via `localhost`

If you need to re-enable auth for external-facing deployments, see the native Stargate config whitelists:
- `172.16.0.0/12` (covers all Docker bridge ranges)
- `10.0.0.0/8` (private network)
- `192.168.0.0/16` (private network)

#### Enabling Authentication (Advanced)

If you need authentication for external-facing deployments:

**Gateway:**
```yaml
environment:
  - WS_AUTH_ENABLED=true
  - GATEWAY_API_KEY=your-secret-key-here
```

**Stargate:**
```yaml
environment:
  - STARGATE_CONFIG=config/stargate_config.yaml  # Use specific config path
```

To use default config location, omit STARGATE_CONFIG entirely.
Then edit `services/universal-stargate/config/stargate_config.yaml` to set `authorization.enabled: true`.

## GPU Support

### Prerequisites

**Required:**
- **Single NVIDIA GPU** with CUDA Compute Capability 8.0+ (Ampere, Ada, Hopper, Blackwell)
  - Supported: RTX 3000/4000/5000 series, A100, H100, Jetson Orin
  - **NOT supported**: Volta (V100), Turing (RTX 2000, T4) - use CUDA 12.x builds
  - **Note**: Multi-GPU setups not yet supported by inference_djinn ecosystem
- NVIDIA Driver 545.23.08+ (CUDA 13.x compatible)
- NVIDIA Container Toolkit
- Docker 19.03+
- Docker Compose 1.28+ (with GPU support)

**IMPORTANT**: GPU support is **NOT** for Golem Network deployments. Golem remains CPU-only. Use GPU Docker images for:
- ✅ Local GPU workstations
- ✅ Cloud GPU instances (AWS/GCP/Azure)
- ❌ **NOT** for Golem Network tasks

### Installation

#### 1. Install NVIDIA Drivers

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y nvidia-driver-535

# Verify
nvidia-smi
```

#### 2. Install NVIDIA Container Toolkit

```bash
# Ubuntu/Debian
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | \
  sudo tee /etc/apt/sources.list.d/nvidia-docker.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

#### 3. Verify Installation

```bash
docker run --rm --gpus all nvidia/cuda:13.0.0-base-ubuntu22.04 nvidia-smi
```

### Build GPU Image

#### Default Build (AVX-512 Optimized for Hybrid Inference)

```bash
# From project root
cd /mnt/torus/projects/universal-llm-gateway

# Build with default AVX-512 optimization (recommended for modern servers)
docker/scripts/build/build-gpu.sh

# Test
docker/test-gpu.sh
```

**Default CPU Optimization: AVX-512** (x86-64-v4)
- ✅ 4-6x faster hybrid GPU+CPU inference vs generic build
- ✅ Optimized for quantized models (Q4, Q5, Q8 GGUF)
- ✅ Includes AVX-512 VNNI (Vector Neural Network Instructions)
- ✅ Requires: Intel Ice Lake+ (2019+), AMD Zen 4+ (2022+)

**Why This Matters:**
- **GPU-only inference**: CPU optimization doesn't matter much (GPU does all work)
- **Hybrid CPU/GPU inference**: **HUGE impact** - CPU processes layers not on GPU
- **Quantized models**: AVX-512 VNNI provides 2-4x speedup for INT8 operations

#### Build with AVX2 (Broader Compatibility)

For older CPUs (Intel Haswell 2013+, AMD Excavator 2015+):

```bash
# Build with AVX2 optimization (compatible with most servers since 2013)
docker build --build-arg CPU_OPTIMIZATION=avx2 -f docker/dockerfiles/Dockerfile.gpu -t universal-llm-gateway:gpu-avx2 .
```

**AVX2 CPU Optimization:** (x86-64-v3)
- ✅ 2-3x faster hybrid inference vs generic build
- ✅ Works on virtually all servers from 2013+
- ✅ Still much better than generic build
- ❌ Missing AVX-512 VNNI (slower quantized inference)

#### Build with Generic x86-64 (Maximum Portability)

For maximum portability (rare - use only if needed):

```bash
# Build with generic x86-64 (slowest, maximum compatibility)
docker build --build-arg CPU_OPTIMIZATION=generic -f docker/dockerfiles/Dockerfile.gpu -t universal-llm-gateway:gpu-generic .
```

**Generic CPU Optimization:** (x86-64 baseline)
- ⚠️ **Slow** hybrid GPU+CPU inference
- ✅ Works on any x86-64 CPU
- ❌ Not recommended unless you have very old hardware

#### Check Your CPU Compatibility

Before building, verify your CPU supports AVX-512:

```bash
# Check for AVX-512 support
lscpu | grep -i avx512

# Check for AVX-512 VNNI (most important for llama.cpp)
grep -i vnni /proc/cpuinfo

# If no AVX-512, check for AVX2 (almost all modern CPUs have this)
grep -i avx2 /proc/cpuinfo
```

**Recommendation:**
- **Modern server (2019+)**: Use default (AVX-512) for best performance
- **Older server (2013-2019)**: Use AVX2 build arg
- **Very old/unknown hardware**: Use generic build arg

**Performance Comparison (Hybrid GPU+CPU Inference):**

| Optimization | Quantized Models | Requires | Coverage |
|-------------|------------------|----------|----------|
| **AVX-512** (default) | 4-6x faster | Intel 2019+, AMD 2022+ | Modern servers |
| **AVX2** | 2-3x faster | Intel 2013+, AMD 2015+ | ~95% of servers |
| **Generic** | 1x (baseline) | Any x86-64 | 100% |

### Deploy with GPU

**Foreground mode** (shows console logs - recommended):
```bash
# Start gateway (foreground - shows logs including GPU detection)
docker compose -f docker/compose/gateway-gpu.yml up

# Test (in another terminal)
curl http://localhost:9998/health

# Stop: Ctrl+C in the running terminal
```

**Background mode** (daemon):
```bash
# Start gateway (background)
docker compose -f docker/compose/gateway-gpu.yml up -d

# Check logs
docker compose -f docker/compose/gateway-gpu.yml logs -f

# Verify GPU detection in logs
docker compose -f docker/compose/gateway-gpu.yml logs | grep -i "gpu"

# Check health endpoint
curl http://localhost:9998/health

# Stop
docker compose -f docker/compose/gateway-gpu.yml down
```

### GPU Container Configuration

The GPU Docker Compose configuration includes optimizations for performance and security:

**Shared Memory (IPC Configuration)**:

**Default setting (`ipc: host`) is RECOMMENDED - secure AND performant when network-isolated:**

✅ **Secure**: With network isolation enabled (`internal: true`), the container cannot access external systems. The security risk of `ipc: host` only applies when containers have outbound network access.

✅ **Performant**: Optimal for GPU workloads, multi-process communication, and GPU-CPU memory transfers.

**Why `ipc: host` is secure in this configuration:**
- Network isolation blocks all outbound connections
- Container cannot communicate with untrusted external systems
- Attack surface limited to host filesystem (via mounts) and inbound API
- IPC namespace sharing doesn't add meaningful risk in this isolated context

**Alternative IPC modes** (only needed for untrusted workloads or multi-tenant environments):

| Mode | Security (Isolated Network) | Performance | Use Case |
|------|----------|-------------|----------|
| `ipc: host` | ✅ Secure | ⭐⭐⭐ Excellent | **RECOMMENDED** - default for network-isolated GPU workloads |
| `ipc: shareable` | ✅ More Isolated | ⭐⭐ Good | Multi-container IPC sharing, paranoid security |
| `ipc: private` | 🔒 Most Isolated | ⭐ May impact GPU | Untrusted workloads, strict multi-tenant isolation |

To change IPC mode, edit `docker/compose/gateway-gpu.yml`:
```yaml
# High security: Use private IPC (may reduce performance)
ipc: private

# Balanced: Use shareable IPC
ipc: shareable

# Best performance: Use host IPC (default)
ipc: host
```

**What IPC affects:**
- Shared memory segments (e.g., `/dev/shm`)
- Message queues
- Semaphores
- GPU-CPU memory transfers
- Worker process communication

**When to use each mode:**
- **`host` (RECOMMENDED)**: Network-isolated environments (like this config), trusted workloads, maximum GPU performance
- **`shareable`**: Multi-container setups needing IPC sharing, extra-paranoid security posture
- **`private`**: Untrusted workloads with outbound network access, strict multi-tenant isolation

**Network Isolation** (`internal: true`):
- Blocks all outbound network connections
- Prevents internet/WAN access
- Allows inbound API connections (port 9998)
- Host mounts (NFS, local storage) work normally - container accesses files via host kernel

**No Resource Limits**:
- Container can use all available GPU memory
- No CPU or RAM restrictions
- Maximizes inference performance

**Security Note**: The container is isolated from external networks but can access files mounted from the host (including NFS mounts). All network operations for mounted filesystems happen at the host kernel level, invisible to the container.

### Testing GPU Deployment

**Basic Smoke Test** (verify GPU inference works):
```bash
# Ensure container is running
docker compose -f docker/compose/gateway-gpu.yml ps

# Check GPU detection in logs
docker compose -f docker/compose/gateway-gpu.yml logs | grep -i "gpu detected"

# Simple inference test (adjust model name to match your models)
curl -X POST http://localhost:9998/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "your-model-id",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 10
  }'

# Expected: JSON response with generated text
# Check logs for GPU usage confirmation
```

**Test Inference Modes** (GPU, Hybrid, CPU):
```bash
# GPU mode: all layers on GPU
curl -X POST http://localhost:9998/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "your-model-id",
    "messages": [{"role": "user", "content": "Test GPU"}],
    "extra_params": {"n_gpu_layers": -1}
  }'

# Hybrid mode: partial GPU offloading
curl -X POST http://localhost:9998/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "your-model-id",
    "messages": [{"role": "user", "content": "Test Hybrid"}],
    "extra_params": {"n_gpu_layers": 20}
  }'

# CPU mode: no GPU layers
curl -X POST http://localhost:9998/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "your-model-id",
    "messages": [{"role": "user", "content": "Test CPU"}],
    "extra_params": {"n_gpu_layers": 0}
  }'
```

### Hybrid GPU+CPU Inference

The GPU Docker image includes **both CUDA and CPU optimization libraries**, enabling flexible inference modes:

**Pure GPU Mode** (`n_gpu_layers: -1`):
- All layers offloaded to GPU
- Maximum performance for models that fit in VRAM
- Example: 13B model on 24GB GPU

**Hybrid Mode** (`n_gpu_layers: 35`):
- Partial GPU offloading (e.g., first 35 layers)
- Remaining layers run on CPU with OpenBLAS acceleration
- Enables large models on smaller GPUs
- Example: 70B model on 24GB GPU

**CPU-Only Mode** (`n_gpu_layers: 0`):
- All layers run on CPU
- Uses OpenBLAS for 2-3x speedup vs baseline CPU
- Fallback when GPU is busy or unavailable
- Same performance as CPU-only Docker build

**Configuration Example:**
```json
{
  "model": "llama-3-70b-q4",
  "messages": [...],
  "extra_params": {
    "n_gpu_layers": 35  // Hybrid: 35 layers GPU, rest CPU
  }
}
```

### vLLM Support (Phase 2) ✅

The GPU image includes **vLLM** for high-performance inference of safetensors/HuggingFace models:

**Supported Model Formats:**
- **GGUF models**: Use llama-cpp-python backend (existing functionality)
- **Safetensors models**: Use vLLM backend (Phase 2)
- **HuggingFace models**: Use vLLM backend with automatic model download

**Backend Selection:**
The gateway automatically selects the appropriate backend based on model format in `model_loaders.yaml`.

**Example model_loaders.yaml entry for vLLM:**
```yaml
meta-llama/Llama-3.1-70B-Instruct:
  loader_type: vllm
  loader_params:
    gpu_memory_utilization: 0.9
    max_model_len: 8192
    tensor_parallel_size: 1
```

**Performance Comparison:**
- **vLLM**: Optimized for safetensors, PagedAttention, continuous batching
- **llama-cpp-python**: Optimized for GGUF, quantized models, hybrid GPU+CPU

**Image Size:**
- Phase 1 (llama-cpp-python only): ~3-4 GB
- Phase 2 (+ vLLM + PyTorch): ~10-12 GB

**Included Versions (as of Feb 2026):**
- PyTorch 2.9.1+ with CUDA 13.0
- vLLM 0.15.0+
- transformers 4.56.0+ (< 5.0, required by vLLM 0.15.0)
- huggingface-hub 0.34.0+ (< 1.0, required by transformers 4.57.x)
- accelerate 1.12.0+
- llama-cpp-python 0.3.16+

### GPU vs CPU Comparison

| Feature | CPU Build | GPU Build (Phase 2) |
|---------|-----------|---------------------|
| Base Image | python:3.12-slim | nvidia/cuda:13.0.0 |
| llama-cpp-python | OpenBLAS (CPU) | CUDA + OpenBLAS |
| CPU Optimization | ✅ OpenBLAS | ✅ OpenBLAS |
| Hybrid Inference | ❌ N/A | ✅ Yes |
| vLLM Support | ❌ No | ✅ Yes |
| PyTorch | ❌ No | ✅ Yes (CUDA 13.0) |
| Image Size | ~1.5-2GB | ~10-12GB |
| Build Time | ~5-10 min | ~20-40 min |
| Inference Speed (GGUF, GPU) | N/A | 3-10x faster |
| Inference Speed (GGUF, CPU) | 1x (baseline) | 1x (same) |
| Inference Speed (GGUF, Hybrid) | N/A | 2-5x faster |
| Inference Speed (HF/safetensors) | N/A | 5-20x faster |
| Use Case | Golem, CPU servers | Local GPU, cloud GPU |

### GPU Troubleshooting

**GPU not detected:**
```bash
# Check NVIDIA driver
nvidia-smi

# Check Docker GPU access
docker run --rm --gpus all nvidia/cuda:13.0.0-base-ubuntu22.04 nvidia-smi

# Check container GPU access
docker compose -f docker/compose/gateway-gpu.yml exec gateway-gpu nvidia-smi
```

**CUDA version mismatch or older GPU (Volta/Turing):**
```bash
# Check host CUDA version
nvidia-smi | grep "CUDA Version"

# For older GPUs (V100, RTX 2000, T4) - rebuild with CUDA 12.x
CUDA_VERSION=12.6.0 docker/scripts/build/build-gpu.sh

# For newer GPUs (RTX 3000+, A100, H100) - use CUDA 13.0 (default)
docker/scripts/build/build-gpu.sh
```

**Multi-GPU system but only one GPU detected:**
```bash
# This is expected - multi-GPU not yet supported
# To use a specific GPU in multi-GPU system:
CUDA_VISIBLE_DEVICES=1 docker compose -f docker/compose/gateway-gpu.yml up

# Or run multiple containers (manual load distribution):
CUDA_VISIBLE_DEVICES=0 docker compose -f docker/compose/gateway-gpu.yml -p gateway-gpu-0 up -d
CUDA_VISIBLE_DEVICES=1 docker compose -f docker/compose/gateway-gpu.yml -p gateway-gpu-1 up -d
```

**Out of memory:**
- Reduce `gpu_memory_utilization` in model loader config
- Use hybrid inference: reduce `n_gpu_layers` (e.g., from -1 to 35)
- Use smaller models
- Monitor GPU memory: `docker exec gateway-gpu nvidia-smi`

**Hybrid inference slower than expected:**
- **Most common cause**: Image built without CPU optimizations
  - Default build (AVX-512): 4-6x faster hybrid inference
  - AVX2 build: 2-3x faster hybrid inference
  - Generic build: Slow (no SIMD optimizations)
  - **Solution**: Rebuild with AVX-512 or AVX2 (see "Build GPU Image" section)
- Check CPU portion is using OpenBLAS:
  ```bash
  docker exec gateway-gpu python3 -c "from llama_cpp import llama_cpp; print(llama_cpp.llama_print_system_info())"
  # Should show "BLAS = 1" or "OpenBLAS"
  # Should show "AVX512" or "AVX2" in system info
  ```
- Increase `n_threads` for CPU portion (default: 4, try 8-16)
- Verify VRAM isn't full (causing spillover to system RAM)

**Pure CPU mode in GPU container:**
- Set `n_gpu_layers: 0` in request
- Performance should match CPU-only Docker build (OpenBLAS acceleration)
- Useful for testing or when GPU is busy

### Windows GPU Support (WSL2)

GPU inference is supported on Windows via Docker Desktop with WSL2 backend.

#### Prerequisites (Windows)

- **Windows 10** (21H2+) or **Windows 11**
- **NVIDIA GPU** with CUDA support
- **NVIDIA Driver** with WSL2 support (Game Ready 470.76+ or Studio driver)
- **WSL2** with updated kernel: `wsl --update`
- **Docker Desktop** with WSL2 backend enabled (not Hyper-V)

#### Verify GPU Access (Windows)

```powershell
# In PowerShell or Command Prompt
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

If this shows your GPU, you're ready to proceed.

#### Deploy on Windows

Use the Windows-specific compose file which removes Linux-only features:

```powershell
# Set your models path (PowerShell)
$env:MODEL_PATH_ROOT = "C:\Users\YourName\.models"

# Start gateway
docker compose -f docker/compose/gateway-gpu-windows.yml up

# Test (in another terminal)
curl http://localhost:9998/health
```

Or with explicit path:

```powershell
docker compose -f docker/compose/gateway-gpu-windows.yml up `
  -e MODEL_PATH_ROOT=C:/Users/YourName/.models
```

#### Windows vs Linux Differences

| Feature | Linux (`docker-compose.gateway-gpu.yml`) | Windows (`docker-compose.gateway-gpu-windows.yml`) |
|---------|------------------------------------------|---------------------------------------------------|
| IPC | `ipc: host` (shared with host) | `shm_size: 8gb` (dedicated container memory) |
| Security | `seccomp:unconfined`, `apparmor:unconfined` | Removed (not applicable) |
| CUDA Default | 13.0.0 (Blackwell) | 12.6.0 (broader compatibility) |
| Performance | ~100% native | ~95-98% via WSL2 GPU-P |

#### Windows Troubleshooting

**"No GPU detected" in Docker:**
1. Ensure Docker Desktop is using WSL2 backend (Settings → General → Use WSL 2)
2. Update WSL: `wsl --update`
3. Install/update NVIDIA driver with WSL support
4. Restart Docker Desktop after driver update

**Slow performance:**
- WSL2 has ~2-5% overhead vs native Linux
- Ensure you're not running other GPU workloads in Windows simultaneously

**Volume path issues:**
- Use forward slashes: `C:/Users/Name/.models`
- Or use `~/.models` (Docker translates to WSL home)
- Avoid spaces in paths

## Golem Network Deployment

### 1. Build Image

#### Recommended Build (AVX2 - Broad Compatibility)

```bash
# Default build with AVX2 optimization (recommended for Golem Network)
docker/scripts/build/build-golem.sh
```

This builds with **AVX2 optimization** (x86-64-v3), which provides 2-3x faster CPU inference while maintaining broad compatibility across Golem provider hardware.

**Why AVX2 is recommended for Golem:**
- ✅ Works on ~95% of servers from 2013+ (covers most Golem providers)
- ✅ 2-3x faster than generic build
- ✅ Safer than AVX-512 for unknown provider hardware
- ✅ Best balance of performance and compatibility

#### Alternative Builds

```bash
# Maximum performance (AVX-512) - risky for Golem, use only if you control providers
CPU_OPTIMIZATION=avx512 docker/scripts/build/build-golem.sh

# Maximum compatibility (Generic) - slow, use only for very old hardware
CPU_OPTIMIZATION=generic docker/scripts/build/build-golem.sh
```

### 2. Test Locally

Test with Golem-like paths to verify configuration:

```bash
docker run -it --rm \
  -e MODEL_PATH_ROOT=/golem/models \
  -e LOG_LEVEL=debug \
  -v $(pwd)/test-models:/golem/models:ro \
  -v test-logs:/golem/logs \
  -p 9998:9998 \
  universal-llm-gateway:golem \
  /app/golem-start.sh gateway
```

### 3. Convert to GVMI

```bash
# Install gvmkit-build (if not already installed)
# pip install gvmkit-build

# Convert Docker image to Golem VM image
gvmkit-build universal-llm-gateway:golem
```

### 4. Publish to Golem

Follow [Golem's publishing workflow](https://docs.golem.network/docs/creators/python/guides/golem-images) to push the image to Golem's registry.

### 5. Deploy via Golem Requestor

Example Golem requestor script:

```python
from yapapi import Golem
from yapapi.payload import vm

async def main():
    package = await vm.repo(
        image_hash="your-image-hash-here",
    )
    
    async with Golem(budget=10.0, subnet_tag="public") as golem:
        cluster = await golem.run_service(
            package,
            env={
                "MODEL_PATH_ROOT": "/golem/models",
                "LOG_LEVEL": "info",
                # CPU optimization auto-detected (no hard-coded values)
            },
            init_payload=lambda ctx: [
                ctx.send_file("my-model.gguf", "/golem/models/my-model.gguf"),
            ],
            start_payload=lambda ctx: [
                ctx.run("/app/golem-start.sh", "gateway"),
            ],
        )
```

## Platform Support

This Docker setup supports **Intel and AMD x86_64 CPUs** with configurable CPU optimizations:

### CPU Optimization Levels

**Both CPU-only (Golem) and GPU images support three CPU optimization levels:**

| Level | Performance | Compatibility | Use Case |
|-------|------------|---------------|----------|
| **AVX-512** (x86-64-v4) | 4-6x faster | Intel 2019+, AMD 2022+ | GPU image default, modern servers |
| **AVX2** (x86-64-v3) | 2-3x faster | Intel 2013+, AMD 2015+ | **Golem default**, broad compatibility |
| **Generic** (x86-64) | 1x baseline | Any x86-64 | Maximum portability, slowest |

**Selection by use case:**
- **Golem Network (CPU-only)**: AVX2 (default) - balances performance and compatibility for unknown provider hardware
- **Local GPU servers**: AVX-512 (default) - maximum performance for hybrid CPU+GPU inference
- **Known old hardware**: Generic - use only when necessary

### Platform Features

- ✅ Works on Intel x86_64 CPUs (auto-detects MKL support)
- ✅ Works on AMD x86_64 CPUs (uses OpenMP, skips MKL)
- ✅ Configurable CPU SIMD optimizations (AVX-512, AVX2, generic)
- ✅ OpenMP and tokenizers libraries auto-detect optimal thread counts
- ✅ Users can override auto-detection if needed via environment variables
- ❌ ARM not supported (x86_64 only)

### Why Configurable CPU Optimizations?

1. **Performance**: 2-6x faster CPU inference vs generic build
2. **Flexibility**: Choose optimization level based on deployment target
3. **Golem Network**: AVX2 default provides good performance across most providers
4. **Local Deployments**: AVX-512 provides maximum performance for known hardware
5. **Compatibility**: Generic fallback for very old hardware

### GPU Support

**NVIDIA CUDA support available** for local GPU deployments:
- Separate CUDA-enabled Dockerfile (`docker/dockerfiles/Dockerfile.gpu`) for GPU inference
- Hybrid CPU+GPU inference support
- vLLM and llama-cpp-python backends
- Same CPU optimization levels for hybrid inference

### Overriding Auto-Detection (Advanced)

If you have specific performance requirements and know your target hardware:

```bash
# For Intel CPU with 32 cores
docker run -e OMP_NUM_THREADS=32 -e MKL_NUM_THREADS=32 ...

# For AMD CPU (MKL not used)
docker run -e OMP_NUM_THREADS=32 ...

# Disable tokenizers parallelism
docker run -e TOKENIZERS_PARALLELISM=false ...
```

## Volumes

Golem VMs use specific volume directories (per [Golem documentation](https://docs.golem.network/docs/creators/python/guides/golem-images)):

- `/golem/models` - Model files (transferred at runtime)
- `/golem/input` - Input data (optional)
- `/golem/output` - Output/results and runtime state
- `/golem/logs` - Logs (avoids 128MB tmpfs limit)

**Important**: Files copied into `VOLUME` directories during Docker build are shadowed at runtime. Volumes are mounted as empty directories.

## Troubleshooting

### Local Remote Stargate

#### Port 10999 Already in Use

**Issue**: Port 10999 already in use

```bash
# Find conflicting process
lsof -i:10999

# Stop and restart
docker compose -f docker/compose/dev-local.yml down
docker compose -f docker/compose/dev-local.yml up -d
```

#### Gateway Container Not Starting

**Issue**: Gateway container fails to start

```bash
# Check logs
docker compose -f docker/compose/dev-local.yml logs gateway

# Common causes:
# - Model path not mounted correctly
# - Socket permissions issue
# - NVIDIA runtime not available (for GPU)

# Verify model mount
docker compose -f docker/compose/dev-local.yml exec gateway ls -la /models

# Verify socket
docker compose -f docker/compose/dev-local.yml exec gateway ls -la /sockets
```

#### Master Can't Reach Remote Stargate

**Issue**: Master Stargate can't connect to Remote

```bash
# Verify Remote is running
docker compose -f docker/compose/dev-local.yml ps

# Check Remote logs for errors
docker compose -f docker/compose/dev-local.yml logs remote-stargate

# Verify Master config has correct Remote URL
grep -A5 "federation:" config/stargate_config.dev-localhost-only.yaml

# Test Remote health directly
curl http://localhost:10999/health
```

#### Remote Stargate Health Check Failing

**Issue**: Remote container unhealthy

```bash
# Check health check output
docker inspect local-remote-stargate | jq '.[0].State.Health'

# Common causes:
# - Gateway not responding (check gateway logs)
# - Socket not accessible (check volume mounts)
# - Federation config invalid

# Restart both containers
docker compose -f docker/compose/dev-local.yml restart
```

### Build Issues

#### llama-cpp-python Build Fails

If the build fails during `llama-cpp-python` installation:

```
ERROR: Failed building wheel for llama-cpp-python
```

**Solutions:**

1. **Ensure Docker has enough resources:**
   - Memory: At least 4GB RAM
   - CPU: At least 2 cores
   - Disk: At least 10GB free space

2. **Check build logs for specific errors:**
   ```bash
   docker build --no-cache --progress=plain -t universal-llm-gateway:golem -f docker/dockerfiles/Dockerfile.golem . 2>&1 | tee build.log
   ```

3. **Verify CMAKE_ARGS are being applied:**
   - Look for `-- GGML_BLAS: OFF` in build output
   - Look for `-- GGML_CUDA: OFF` in build output

4. **Try building with verbose output:**
   ```bash
   docker build --build-arg CMAKE_ARGS="-DGGML_BLAS=OFF -DGGML_CUDA=OFF -DGGML_METAL=OFF" \
     --progress=plain -t universal-llm-gateway:golem -f docker/dockerfiles/Dockerfile.golem .
   ```

#### Build is Extremely Slow

`llama-cpp-python` compilation can take 5-10 minutes on slower machines:
- This is normal - the library is compiling C++ code
- Use `--progress=plain` to see detailed progress
- Consider using a machine with more CPU cores for faster builds

#### "golem-start.sh not found" Error

This was a common issue - now fixed with `.dockerignore` exception:
- The `docker/` directory is excluded from build context
- **Exception:** `!docker/golem-start.sh` allows the startup script through
- If issue persists, verify `.dockerignore` has the exception line

### Models Not Found

Ensure `MODEL_PATH_ROOT` matches the volume mount:

```yaml
environment:
  - MODEL_PATH_ROOT=/golem/models
volumes:
  - /your/local/models:/golem/models:ro
```

### Logs Not Appearing

Logs are written to `/golem/logs`. Mount this volume or check container logs:

```bash
docker logs universal-gateway -f
```

### Service Won't Start

Check environment configuration:

```bash
docker exec universal-gateway /app/golem-start.sh status
docker exec universal-gateway cat /golem/logs/gateway.log
```

## References

- [Golem VM Images Guide](https://docs.golem.network/docs/creators/python/guides/golem-images)
- [Golem Images FAQ](https://docs.golem.network/docs/creators/python/guides/golem-images-faq)
- [Universal LLM Gateway Documentation](../README.md)

