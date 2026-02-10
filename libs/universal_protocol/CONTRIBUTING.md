# Contributing to universal_protocol

universal_protocol is part of the **Universal LLM Ecosystem**.

## Development Guidelines

All ecosystem components share common development standards and workflows:

**📖 [Universal LLM Ecosystem - CONTRIBUTING.md](../universal-llm-gateway/CONTRIBUTING.md)**

This includes:
- Python 3.12+ requirement
- Shared virtual environment (`$HOME/.venvs/universal`)
- Ruff + BasedPyright tooling
- Development environment setup
- IDE configuration (Cursor, VS Code, Neovim)
- Code style standards
- Testing and workflow

## IDE Setup

### Cursor/VS Code

See main [CONTRIBUTING.md](../universal-llm-gateway/CONTRIBUTING.md) for `.vscode/settings.json` configuration.

**Required Extensions**:
1. **Ruff** (charliermarsh.ruff) - Formatting and linting
2. **BasedPyright** (detachhead.basedpyright) - Type checking (Pylance is deprecated)

The `.vscode/settings.json` file in this repository is already configured for these extensions.

### Neovim (kickstart.nvim)

**📖 [Complete Neovim Setup Guide](../universal-llm-gateway/docs/NEOVIM_SETUP.md)**

Step-by-step instructions for configuring Neovim with Pyright + Ruff using kickstart's built-in plugins.

## Quick Start

```bash
# All ecosystem components are in /mnt/torus/projects/
cd /mnt/torus/projects/

# Activate shared virtual environment
source $HOME/.venvs/universal/bin/activate

# See universal-llm-gateway/CONTRIBUTING.md for full setup instructions
```

## Component-Specific Notes

### About universal_protocol

**universal_protocol** is a high-performance, transport-agnostic protocol library that provides:
- JSON-RPC 2.0 client and server implementation
- WebSocket streaming over Unix domain sockets
- Server-sent events (SSE) formatting and parsing
- State channel protocol with automatic reconnection
- ASGI application with Starlette
- Built-in observability and metrics

### Key Files

- `pyproject.toml` - Tool configuration (ruff, BasedPyright)
- `requirements.txt` - External Python dependencies (NOT ecosystem components)
- `.vscode/settings.json` - IDE configuration for Cursor/VS Code (already in repo)
- `config.yaml` - Runtime configuration

### Dependencies

**External** (in `requirements.txt`):
- `pyyaml>=6.0` - YAML configuration support
- `starlette>=0.27.0` - ASGI web framework
- `httpx>=0.24.0` - HTTP client with Unix socket support
- `websockets>=11.0` - WebSocket library

**Ecosystem** (expected in shared venv):
- `universal_logging` - Structured logging for the ecosystem
- `universal-event-bus` - Event-driven messaging (optional)

See main CONTRIBUTING.md for how to set up the ecosystem components.

### Running Tests

```bash
# Activate shared venv
source $HOME/.venvs/universal/bin/activate

# Run all tests
pytest

# Run with coverage
pytest --cov=universal_protocol --cov-report=html

# Run specific test file
pytest tests/test_rpc_client.py

# Run with verbose output
pytest -v
```

### Code Style

This project follows the Universal LLM Ecosystem code style standards:

- **Line length**: 88 characters (Black-compatible)
- **Quote style**: Double quotes
- **Import organization**: Automatic via ruff
- **Type hints**: Required for public APIs
- **Docstrings**: Google style for public functions/classes

Format code before committing:

```bash
# Format all Python files
ruff format .

# Fix auto-fixable linting issues
ruff check --fix .

# Check for remaining issues
ruff check .
```

### Development Workflow

1. **Create a branch** for your feature/fix
2. **Make changes** following the code style
3. **Run tests** to ensure nothing breaks
4. **Format code** with ruff
5. **Check linting** with ruff and BasedPyright
6. **Commit changes** with descriptive message
7. **Push** and create pull request

### Architecture Overview

```
universal_protocol/
├── rpc/              # JSON-RPC 2.0 client and handlers
│   ├── client.py     # RPCClient and AsyncRPCClient
│   ├── handlers.py   # RPC method handlers
│   └── types.py      # Type definitions
├── ws/               # WebSocket streaming
│   ├── client.py     # StreamClient
│   ├── handlers.py   # Stream handlers
│   ├── stream_queue.py   # Unbounded queue
│   ├── bounded_queue.py  # Backpressure queue
│   └── lifecycle.py  # Stream lifecycle
├── sse/              # Server-sent events
│   └── core.py       # SSE formatting/parsing
├── state_channel/    # State synchronization
│   ├── channel.py    # Basic channel
│   ├── resilient_channel.py  # Auto-reconnect
│   └── protocol.py   # Protocol types
├── server/           # ASGI server
│   ├── asgi_app.py   # Starlette app
│   ├── startup.py    # Server startup
│   └── uds_security.py   # Unix socket security
├── observability.py  # Metrics and debugging
├── config.py         # Configuration management
├── errors.py         # Error types
└── ids.py           # ID generation
```

### Common Tasks

#### Adding a New RPC Method

1. Add handler in `rpc/handlers.py`
2. Register in `RPC_METHODS` dict in `server/asgi_app.py`
3. Add type definition in `rpc/types.py` if needed
4. Add tests in `tests/`

#### Adding Metrics

1. Add metric tracking in `observability.py`
2. Update Prometheus export in `server/asgi_app.py` metrics handler
3. Document in README.md

#### Updating Configuration

1. Update defaults in `config.py` `DEFAULTS` dict
2. Update `config.yaml` example
3. Document in README.md

## Questions?

- Main docs: `../universal-llm-gateway/CONTRIBUTING.md`
- Neovim setup: `../universal-llm-gateway/docs/NEOVIM_SETUP.md`
- Author: krunch3r76 ([@krunch3r76](https://github.com/krunch3r76))
- Email: biz@u26a4.com

