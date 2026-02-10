# Contributing to Universal Logging

Universal Logging is part of the **Universal LLM Ecosystem**.

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
2. **BasedPyright** (detachhead.basedpyright) - Type checking

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

### About Universal Logging

Universal Logging is a production-ready, auto-initializing logging framework for Python with zero-configuration setup, intelligent context detection, and comprehensive error handling. It provides enhanced formatters, colored output, FastAPI integration, and health monitoring.

### Key Files

- `pyproject.toml` - Tool configuration (ruff, BasedPyright)
- `requirements.txt` - External Python dependencies (NOT ecosystem components)
- `.vscode/settings.json` - IDE configuration for Cursor/VS Code (already in repo)
- `.gitignore` - Git ignore patterns (includes `.ruff_cache/` and `.vscode/*` pattern)

### Dependencies

**External** (in `requirements.txt`):
- `pyyaml>=6.0` - YAML configuration file parsing
- `fastapi>=0.100.0` - Optional, for FastAPI adapter support

**Ecosystem** (expected in shared venv):
- None (universal_logging is a foundational component with no ecosystem dependencies)

See main CONTRIBUTING.md for how to set up the ecosystem components.

### Development Workflow

1. **Format code**: `ruff format .`
2. **Fix linting issues**: `ruff check --fix .`
3. **Run tests**: `python test_basic.py && python test_colors.py && python test_truncation.py`
4. **Check health**: Use the included test files to verify logging functionality

### Testing

```bash
# Basic functionality
python test_basic.py

# Colored output
python test_colors.py

# Truncation and formatting
python test_truncation.py
```

### Configuration Files

Universal Logging searches for configuration in multiple locations:
1. `$HOME/.config/universal_logging/default.yaml` (user config)
2. `./config/default.yaml` (project config)
3. `./.universal_logging.yaml` (workspace config)
4. Environment variables (e.g., `LOG_LEVEL=DEBUG`)

See `config/default.yaml` for the default configuration structure.

## Questions?

- Main docs: `../universal-llm-gateway/CONTRIBUTING.md`
- Neovim setup: `../universal-llm-gateway/docs/NEOVIM_SETUP.md`
- Author: krunch3r76 ([@krunch3r76](https://github.com/krunch3r76))
- Email: biz@u26a4.com

