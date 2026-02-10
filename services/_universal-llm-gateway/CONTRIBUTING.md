# Contributing to Universal LLM Gateway

Thank you for your interest in contributing to Universal LLM Gateway! This document provides guidelines for development, testing, and contributing to the Universal LLM Ecosystem.

## Universal LLM Ecosystem

Universal LLM Gateway is the core component of the **Universal LLM Ecosystem**, which includes:

- **universal-llm-gateway** - Core gateway service (this project)
- **universal-stargate** - Middleware proxy with intelligent routing
- **universal_logging** - Structured logging framework
- **universal_event_bus** - Event messaging and coordination
- **universal_transport** - Transport layer abstraction
- **universal_protocol** - Protocol layer for RPC patterns
- **process_ipc** - Process lifecycle and IPC
- **inference_djinn** - Inference engine integration

All ecosystem components share common development standards and workflows.

## Development Environment Setup

### Prerequisites

- **Python 3.12+** (required for all ecosystem components)
- **Git**
- **Virtual Environment Tools** (venv)

### Shared Virtual Environment

**All Universal LLM Ecosystem components use a shared virtual environment:**

```bash
# Create the shared virtual environment (if not already created)
python3.12 -m venv $HOME/.venvs/universal

# Activate
source $HOME/.venvs/universal/bin/activate

# Verify Python version
python --version  # Should be Python 3.12.x or higher
```

**Important**: The shared virtual environment `$HOME/.venvs/universal` is used across all ecosystem components. This ensures:
- Consistent Python versions across all projects
- Ecosystem components can import each other
- Single source of truth for dependency versions
- Simplified development workflow

### Installing Dependencies

```bash
# Activate the shared virtual environment
source $HOME/.venvs/universal/bin/activate

# Install universal-llm-gateway dependencies
cd /mnt/torus/projects/_universal-llm-gateway
pip install -r requirements.txt

# Install other ecosystem components as needed
cd /mnt/torus/projects/universal-stargate
pip install -r requirements.txt

# Continue for other ecosystem components...
```

**Note**: `requirements.txt` files only include external PyPI packages. Ecosystem components import each other directly from the shared virtual environment.

## Code Style and Tools

The Universal LLM Ecosystem uses modern Python development tools:

- **Ruff**: All-in-one formatter and linter (replaces black, flake8, isort)
- **BasedPyright**: Type checker (actively maintained fork of Pyright)

### Tool Installation

**Option 1: Via IDE Extensions (Recommended)**

IDE extensions bundle the tools - no system installation required.

**Required Extensions (Cursor/VS Code)**:
1. **Ruff** (charliermarsh.ruff) - Formatting and linting
2. **BasedPyright** (detachhead.basedpyright) - Type checking

**Note**: Pylance is deprecated and has been subsumed by BasedPyright.

**Option 2: Via System Installation (Optional)**

For command-line use only:

```bash
# Install ruff and basedpyright
pip install --user ruff basedpyright

# Verify installation
ruff --version
basedpyright --version
```

### IDE Configuration

#### Cursor/VS Code

The repository includes `.vscode/settings.json` which configures:
- **Ruff** for formatting and linting (auto-format on save)
- **BasedPyright** for type checking
- Auto-fix and auto-organize imports on save

Simply install the required extensions and the configuration will be applied automatically.

#### Neovim (kickstart/lazy)

For complete Neovim setup instructions, see **[docs/NEOVIM_SETUP.md](docs/NEOVIM_SETUP.md)** (if available).

**Quick Summary**:

Use kickstart.nvim's built-in plugins with minimal Python-specific extensions:

**Tools**:
- **pyright** (LSP) - Type checking
- **conform.nvim** (kickstart default) - Formatting with Ruff
- **nvim-lint** (kickstart optional) - Linting with Ruff

**Configuration** (in `init.lua`):
```lua
-- Enable pyright LSP server
-- Configure conform.nvim for Python
python = { 'ruff_format', 'ruff_organize_imports' },

-- Enable kickstart's lint plugin
require 'kickstart.plugins.lint',

-- In lua/custom/plugins/lint-config.lua: Add Ruff linting
lint.linters_by_ft['python'] = { 'ruff' }
```

**How It Works**:
- **mason.nvim** auto-installs Pyright and Ruff
- All tools read `pyproject.toml` automatically
- **conform.nvim** handles formatting (format-on-save)
- **nvim-lint** provides real-time linting diagnostics
- **pyright** provides type checking and IntelliSense

### Tool Configuration

All tool configuration is centralized in `pyproject.toml`:

```toml
[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP"]
ignore = []

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.pyright]
reportExpectedIndentBlock = "none"
```

All IDEs (Cursor, VS Code, Neovim) read this file automatically for consistent behavior.

## Code Formatting and Linting

### Formatting Code

```bash
# Activate shared virtual environment
source $HOME/.venvs/universal/bin/activate

# Format all Python files
ruff format .

# Fix auto-fixable linting issues and organize imports
ruff check --fix .
```

### Checking Code

```bash
# Check for linting issues
ruff check .

# Type checking (if basedpyright is installed)
basedpyright
```

### Pre-commit Standards

Before committing code:

1. **Format code**: `ruff format .`
2. **Fix linting issues**: `ruff check --fix .`
3. **Verify no remaining issues**: `ruff check .`
4. **Type check** (optional): `basedpyright`

## Project Structure

```
/mnt/torus/projects/_universal-llm-gateway/
├── src/                    # Source code
│   ├── app/                # FastAPI application
│   ├── core/               # Core functionality
│   ├── routers/            # API endpoints
│   ├── schemas/            # Pydantic models
│   └── utils/              # Utilities
├── scripts/                # Management scripts
├── config/                 # Configuration files
├── docs/                   # Documentation
├── tests/                  # Test files (optional)
├── tmp/                    # Temporary working files
│   ├── prompts/            # Development prompts
│   ├── summaries/          # Task summaries
│   └── proposed-docs/      # Documentation drafts
├── pyproject.toml          # Tool configuration
├── requirements.txt        # External dependencies
├── LICENSE                 # MIT License
├── README.md               # Project documentation
└── CONTRIBUTING.md         # This file
```

## Development Workflow

### Running the Gateway

```bash
# Stop systemd service first
systemctl --user stop universal-llm-gateway

# Start manually for testing
./scripts/start-llm-gateway-pid.sh
```

### Logs

- **Gateway logs**: `${DATA_DIR}/tmp/logs/universal-llm-gateway/gateway.log` (DATA_DIR defaults to `/tmp`)
- **Client logs**: `${DATA_DIR}/logs/universal-stargate/stargate.log`

### Testing

```bash
# Activate shared virtual environment
source $HOME/.venvs/universal/bin/activate

# Run tests (if available)
pytest tests/
```

## Dependencies

### External Dependencies (in requirements.txt)

External PyPI packages:
- `fastapi>=0.100.0` - Web framework
- `uvicorn>=0.23.0` - ASGI server
- `pydantic>=2.0` - Data validation
- `pyyaml>=6.0` - YAML parsing
- `psutil>=5.9.0` - Process management
- `python-dotenv>=1.0.0` - Environment configuration

### Ecosystem Dependencies (in shared venv)

Universal LLM Ecosystem components (NOT in requirements.txt):
- `universal_logging` - Structured logging framework
- `universal_event_bus` - Event messaging and coordination
- `universal_transport` - Transport layer abstraction
- `universal_protocol` - Protocol layer for RPC patterns
- `process_ipc` - Process lifecycle and IPC
- `inference_djinn` - Inference engine integration

These components are expected to be in the Python library search path (shared venv or PYTHONPATH).

## Coding Standards

### File Organization

- **Modular structure**: Keep files focused and under 300 lines
- **Domain organization**: Organize into domain-specific packages and subdomain-specific modules
- **Clear separation**: API layer, core logic, utilities

### Documentation

- **Docstrings**: Use clear, descriptive docstrings for functions and classes
- **Type hints**: Use type hints for function parameters and return values
- **Comments**: Minimal inline comments - let code be self-documenting

### Error Handling

- **Layer-appropriate errors**: Use errors from the correct architectural layer
- **Structured error info**: Include context (operation, timeout, correlation_id)
- **No error suppression**: Always log and properly handle errors

### Logging

- **Use universal_logging**: `from universal_logging import get_logger`
- **Structured logging**: Use `get_logger(__name__)` — all output is JSON
- **Consistent format**: Follow ecosystem logging standards

## Breaking Changes

**Note**: We are the sole maintainers and consumers of the Universal LLM Ecosystem.

- **No backward compatibility shims**: Implement breaking changes directly when beneficial
- **Document clearly**: Mention breaking changes explicitly in documentation and summaries
- **Update consumers**: Update all ecosystem consumers in the same change set

## Questions and Support

- **Main docs**: This file and `README.md`
- **Architecture docs**: See `docs/` directory
- **Author**: krunch3r76 ([@krunch3r76](https://github.com/krunch3r76))
- **Email**: biz@u26a4.com

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

