# Contributing to process_ipc

Process IPC is part of the **Universal LLM Ecosystem**.

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

### Neovim (kickstart.nvim)
**📖 [Complete Neovim Setup Guide](../universal-llm-gateway/docs/NEOVIM_SETUP.md)**

Step-by-step instructions for configuring Neovim with Pyright + Ruff using kickstart's built-in plugins.

## Quick Start

```bash
# All ecosystem components are in /mnt/torus/projects/
cd /mnt/torus/projects/process_ipc

# Activate shared virtual environment
source $HOME/.venvs/universal/bin/activate

# Install dependencies
pip install -r requirements.txt

# See universal-llm-gateway/CONTRIBUTING.md for full setup instructions
```

## Component-Specific Notes

### About process_ipc

Process IPC provides robust inter-process communication for the Universal LLM Ecosystem. It implements a supervisor-worker process architecture with health monitoring, crash detection, resource monitoring, and type-safe message handling.

### Key Files

- `pyproject.toml` - Tool configuration (ruff, BasedPyright)
- `requirements.txt` - External Python dependencies (NOT ecosystem components)
- `.vscode/settings.json` - IDE configuration for Cursor/VS Code (already in repo)
- `README.md` - Project overview and installation instructions
- `LICENSE` - MIT License

### Dependencies

**External** (in `requirements.txt`):
- `PyYAML>=6.0` - YAML configuration support
- `psutil>=5.9.0` - Resource monitoring
- `asyncio-mqtt>=0.16.0` - Async MQTT support
- `pytest>=7.0.0` - Testing framework
- `pytest-asyncio>=0.21.0` - Async testing support

**Ecosystem** (expected in shared venv):
- universal_logging
- universal-event-bus
- universal-transport

See main CONTRIBUTING.md for how to set up the ecosystem components.

### Project Structure

```
process_ipc/
├── core/              # Core types, interfaces, messages, state management
├── process/           # Supervisor and worker implementations
├── services/          # Bootstrap, logging, resource monitoring
├── transport/         # Communication abstractions
├── utils/             # Helper utilities
├── examples/          # Usage examples and demonstrations
├── tests/             # Test suite
└── config/            # Configuration files
```

### Running Examples

```bash
# Activate shared virtual environment
source $HOME/.venvs/universal/bin/activate

# Run example demonstrations
python examples/simple_worker_demo.py
python examples/crash_detection_demo.py
python examples/resource_monitoring_demo.py
python examples/async_streaming_demo.py
```

### Running Tests

```bash
# Activate shared virtual environment
source $HOME/.venvs/universal/bin/activate

# Run test suite
pytest tests/
```

## Code Quality

This project uses:
- **Ruff** for formatting and linting (replaces black, flake8, isort)
- **BasedPyright** for type checking (actively maintained fork of Pyright)

All tools read configuration from `pyproject.toml`.

### Manual Formatting

```bash
# Format all Python files
ruff format .

# Fix auto-fixable linting issues and organize imports
ruff check --fix .

# Check for remaining issues
ruff check .
```

## Questions?

- Main docs: `../universal-llm-gateway/CONTRIBUTING.md`
- Neovim setup: `../universal-llm-gateway/docs/NEOVIM_SETUP.md`
- Author: krunch3r76 ([@krunch3r76](https://github.com/krunch3r76))
- Email: biz@u26a4.com
