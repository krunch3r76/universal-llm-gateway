# Contributing to Universal LLM Gateway

This repository is **TUI-first**. For most workflows (configure, build, start/stop, federation topology, model download/measurement), use:

```bash
./manage
```

## Prerequisites

- **Python 3.12+**
- **Docker** with Compose v2
- **NVIDIA Container Toolkit** (for GPU support)
- **Git**
- **Editor**: any (Neovim works great)

## Setup

```bash
# Clone
git clone https://github.com/krunch3r76/universal-llm-gateway.git
cd universal-llm-gateway

# Create shared virtual environment (used across the ecosystem)
python3.12 -m venv ~/.venvs/universal
source ~/.venvs/universal/bin/activate

# Install dependencies
pip install -r requirements.txt

# Verify ecosystem libraries are accessible (via sitecustomize.py)
python -c "import universal_logging; import universal_event_bus; import universal_protocol; print('OK')"
```

## Running (Development)

```bash
# Preferred (TUI-first)
./manage
```

If you need a non-interactive/manual run for debugging, use the service scripts:

- `services/universal-stargate/scripts/start-stargate.sh`
- `services/_universal-llm-gateway/scripts/start-gateway.sh`

Avoid `systemctl` for this project — use `./manage` or the direct scripts.

### Logs

```bash
tail -f /tmp/logs/universal-stargate/*.log       # Master Stargate
tail -f /tmp/logs/universal-llm-gateway/*.log    # Gateway
```

### Cleanup

```bash
pkill -f "universal-"
rm -f /tmp/universal-protocol/*.sock /tmp/process_ipc/*.sock
```

## Editor setup

### Cursor / VS Code (same setup)

Install:

1. **Ruff** (charliermarsh.ruff) — formatting and linting
2. **BasedPyright** (detachhead.basedpyright) — type checking

The repo includes `.vscode/settings.json` which configures formatting/linting behavior for both.

### Neovim (recommended)

You can get an excellent experience with:

- **pyright** (LSP) for type checking / IntelliSense
- **ruff** for formatting + linting (via conform.nvim / none-ls / a formatter runner)

Minimal approach: run `ruff` from CLI (below) and use pyright for in-editor types.

## Code Style

### Formatting and Linting

```bash
ruff format .              # Format
ruff check .               # Lint
ruff check --fix .         # Auto-fix
ruff check --select=UP --fix .  # Modernize to Python 3.12+ patterns
python -m compileall -q services/ libs/ pipelines.local/  # Compile check
```

IDE auto-formats on save via the Ruff extension.

### Standards

- **Line length**: 88 characters
- **Type hints**: Required on all function signatures
- **Python 3.12+ syntax**: `X | Y` not `Union[X, Y]`, `match/case` for dispatch
- **Docstrings**: Required for public functions and classes
- **Comments**: Explain "why", not "what"

Configuration is in `pyproject.toml`.

## Pipelines

If you changed pipeline definitions (or pipeline infrastructure), validate before committing:

```bash
python scripts/validate-pipeline.py pipelines.local/
```

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
