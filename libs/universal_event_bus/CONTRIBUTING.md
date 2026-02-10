# Contributing to Universal Event Bus

Universal Event Bus is part of the **Universal LLM Ecosystem**.

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

This repository includes `.vscode/settings.json` which automatically configures:
- **Ruff** for formatting and linting (auto-format on save)
- **BasedPyright** for type checking
- Auto-fix and auto-organize imports on save

**Required Extensions**:
1. **Ruff** (charliermarsh.ruff) - Formatting and linting
2. **BasedPyright** (detachhead.basedpyright) - Type checking (Pylance is deprecated)

See main [CONTRIBUTING.md](../universal-llm-gateway/CONTRIBUTING.md) for detailed setup instructions.

### Neovim (kickstart.nvim)

**📖 [Complete Neovim Setup Guide](../universal-llm-gateway/docs/NEOVIM_SETUP.md)**

Step-by-step instructions for configuring Neovim with Pyright + Ruff using kickstart's built-in plugins.

## Quick Start

```bash
# All ecosystem components are in /mnt/torus/projects/
cd /mnt/torus/projects/universal_event_bus

# Activate shared virtual environment
source $HOME/.venvs/universal/bin/activate

# Verify Python version
python --version  # Should be Python 3.12.x or higher

# See universal-llm-gateway/CONTRIBUTING.md for full setup instructions
```

## Component-Specific Notes

### About Universal Event Bus

Universal Event Bus provides event-driven messaging and coordination infrastructure for the Universal LLM Ecosystem. It implements publish-subscribe patterns, network transports, and inter-process event communication.

### Key Files

- `pyproject.toml` - Tool configuration (ruff, BasedPyright)
- `requirements.txt` - External Python dependencies (none - only ecosystem deps)
- `.vscode/settings.json` - IDE configuration for Cursor/VS Code (already in repo)
- `events/event_bus.py` - Core EventBus implementation
- `events/event.py` - Event dataclass and utilities
- `transports/udp_transport.py` - UDP network transport
- `bridges/udp_bridge.py` - High-level UDP bridge

### Dependencies

**External** (in `requirements.txt`):
- None - this project has no external PyPI dependencies

**Ecosystem** (expected in shared venv):
- `universal_logging` - Structured logging infrastructure

See main CONTRIBUTING.md for how to set up the ecosystem components.

### Development Tools

Install development tools (optional - IDE extensions include them):

```bash
# Activate shared virtual environment
source $HOME/.venvs/universal/bin/activate

# Install development tools
pip install ruff basedpyright pytest pytest-asyncio
```

### Code Formatting

All Python code follows the ecosystem standard:

```bash
# Format code
ruff format .

# Fix linting issues and organize imports
ruff check --fix .

# Check for remaining issues
ruff check .
```

### Running Tests

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=universal_event_bus
```

## Questions?

- Main docs: `../universal-llm-gateway/CONTRIBUTING.md`
- Neovim setup: `../universal-llm-gateway/docs/NEOVIM_SETUP.md`
- Author: krunch3r76 ([@krunch3r76](https://github.com/krunch3r76))
- Email: biz@u26a4.com

