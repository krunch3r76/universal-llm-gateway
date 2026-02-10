# Contributing to universal_transport

universal_transport is part of the **Universal LLM Ecosystem**.

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

### About universal_transport

universal_transport is the foundation layer for async communication in the Universal LLM Ecosystem. It provides:

- **Length-prefixed framing protocol**: Eliminates readline buffer limits, handles 100MB+ messages
- **Async-only design**: Clean asyncio patterns with no legacy sync code
- **Pluggable serialization**: JSON, MessagePack, Protobuf, Raw Binary
- **Unix and TCP transports**: High-performance socket implementations
- **Client/Server patterns**: High-level API for easy integration

### Key Files

- `pyproject.toml` - Tool configuration (ruff, BasedPyright)
- `requirements.txt` - External Python dependencies (NOT ecosystem components)
- `.vscode/settings.json` - IDE configuration for Cursor/VS Code (already in repo)
- `core/` - Core transport and protocol implementations
- `specialized/` - Ecosystem-specific patterns (e.g., monitoring)
- `adapters/` - Integration adapters (e.g., event_bus)

### Dependencies

**External** (in `requirements.txt`):
- `pydantic>=2.0.0` - Data validation and settings
- `typing-extensions>=4.0.0` - Type hints backports
- `pytest`, `pytest-asyncio`, `pytest-cov` - Testing framework
- `ruff>=0.1.0` - Linting and formatting
- `basedpyright>=1.0.0` - Type checking

**Ecosystem** (expected in shared venv):
- `universal_logging` - Structured logging for ecosystem
- `universal-event-bus` - Event messaging and coordination
- Other ecosystem components as needed

See main CONTRIBUTING.md for how to set up the ecosystem components.

## Development Workflow

### Running Tests

```bash
# Activate shared venv
source $HOME/.venvs/universal/bin/activate

# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=universal_transport --cov-report=html

# Run integration tests
pytest tests/integration/
```

### Code Formatting

```bash
# Format all Python files
ruff format .

# Fix auto-fixable linting issues and organize imports
ruff check --fix .

# Check for remaining issues
ruff check .
```

### Type Checking

```bash
# Run BasedPyright type checking
basedpyright
```

## Architecture Notes

### Transport Layer Separation

universal_transport provides the **transport layer** (socket management, serialization, connection reliability). It does NOT implement:

- **Protocol Layer** (RPC patterns, correlation, streaming) - handled by `universal_protocol`
- **Process Layer** (process lifecycle, health monitoring) - handled by `process_ipc`
- **Application Layer** (business logic) - handled by applications

### Design Principles

1. **Async-only**: No sync code, clean asyncio patterns
2. **Length-prefixed framing**: Eliminates readline buffer limits
3. **Pluggable serialization**: Support multiple formats (JSON, MessagePack, etc.)
4. **Clean interfaces**: Abstract base classes for extensibility
5. **Ecosystem integration**: Works seamlessly with other Universal components

### Key Components

- **`core/transport/`**: Transport implementations (Unix, TCP)
- **`core/protocol/`**: Protocol implementations (length-prefixed, serialization)
- **`core/client_server/`**: High-level client/server API
- **`specialized/`**: Ecosystem-specific patterns (monitoring, etc.)
- **`adapters/`**: Integration with other ecosystem components

## Questions?

- Main docs: `../universal-llm-gateway/CONTRIBUTING.md`
- Neovim setup: `../universal-llm-gateway/docs/NEOVIM_SETUP.md`
- Author: krunch3r76 ([@krunch3r76](https://github.com/krunch3r76))
- Email: biz@u26a4.com

