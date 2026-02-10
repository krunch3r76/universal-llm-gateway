# Contributing to Universal LLM Gateway

## Prerequisites

- **Python 3.12+**
- **Docker** with Compose v2
- **NVIDIA Container Toolkit** (for GPU support)
- **Git**
- **IDE**: Cursor (recommended), VS Code, or Neovim

### IDE Extensions

For Cursor or VS Code, install:
1. **Ruff** (charliermarsh.ruff) — formatting and linting
2. **BasedPyright** (detachhead.basedpyright) — type checking

Extensions bundle their own tooling — no system install needed. The project's `.vscode/settings.json` configures them automatically.

## Setup

```bash
# Clone
git clone https://github.com/krunch3r76/universal-llm-gateway.git
cd universal-llm-gateway

# Create virtual environment
python3.12 -m venv ~/.venvs/universal
source ~/.venvs/universal/bin/activate

# Install dependencies
pip install -r requirements.txt

# Verify ecosystem libraries are accessible (via sitecustomize.py)
python -c "import universal_logging; import universal_event_bus; import universal_protocol; print('OK')"
```

## Running (Development)

```bash
# Start local development stack
./scripts/dev-start.sh

# This starts:
#   Master Stargate (host process, port 9999)
#   Remote Stargate (Docker container)
#   Gateway (Docker container, network_mode: "none")

# Test
curl http://localhost:9999/health

# Chat completion
curl -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "hermes3-llama3.1-8b-16384",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'

# Stop
docker compose -f docker/compose/dev-local.yml down
pkill -f "universal-stargate"
```

### Logs

```bash
tail -f /tmp/logs/universal-stargate/*.log       # Master Stargate
tail -f /tmp/logs/universal-llm-gateway/*.log    # Gateway
docker compose -f docker/compose/dev-local.yml logs -f  # Containers
```

### Cleanup

```bash
pkill -f "universal-"
rm -f /tmp/universal-protocol/*.sock /tmp/process_ipc/*.sock
```

## Code Style

### Formatting and Linting

```bash
ruff format .              # Format
ruff check .               # Lint
ruff check --fix .         # Auto-fix
ruff check --select=UP --fix .  # Modernize to Python 3.12+ patterns
```

IDE auto-formats on save via the Ruff extension.

### Standards

- **Line length**: 88 characters
- **Type hints**: Required on all function signatures
- **Python 3.12+ syntax**: `X | Y` not `Union[X, Y]`, `match/case` for dispatch
- **Docstrings**: Required for public functions and classes
- **Comments**: Explain "why", not "what"

Configuration is in `pyproject.toml`.

## Project Structure

```
universal-llm-gateway/
├── services/
│   ├── _universal-llm-gateway/    # Gateway (port 9998)
│   └── universal-stargate/        # Stargate (port 9999)
├── libs/
│   ├── inference_djinn/           # LLM engines (llama.cpp, vLLM, Whisper, Flux)
│   ├── process_ipc/              # Process supervision
│   ├── universal_protocol/       # RPC protocol
│   ├── universal_transport/      # Transport layer
│   ├── universal_event_bus/      # Event messaging
│   └── universal_logging/        # Structured logging
├── config/                       # Configuration and model catalog
├── docker/                       # Dockerfiles, Compose, build scripts
└── scripts/                      # Utility scripts
```

Libraries in `libs/` are automatically available via `sitecustomize.py` — no pip install needed.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
