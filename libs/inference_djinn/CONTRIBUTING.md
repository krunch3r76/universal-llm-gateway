# Contributing to Inference Djinn

Inference Djinn is part of the **Universal LLM Ecosystem**.

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

### About Inference Djinn

Inference Djinn is a multi-engine LLM inference framework that provides:
- Automatic model format detection
- Engine abstraction for GGUF (llama.cpp), vLLM, and ExLlamaV3
- OpenAI-compatible API
- Performance benchmarking and optimization tools

### Key Files

- `pyproject.toml` - Tool configuration (ruff, BasedPyright)
- `requirements.txt` - External Python dependencies (NOT ecosystem components)
- `.vscode/settings.json` - IDE configuration for Cursor/VS Code
- `engines/` - Backend implementations (GGUF, vLLM, ExLlamaV3)
- `utils/` - Shared utilities (format detection, streaming, types)
- `benchmark/` - Performance testing framework
- `diagnostics/` - Engine-specific testing utilities

### Dependencies

**External** (in `requirements.txt`):
- `pyyaml` - YAML configuration parsing
- `psutil` - System resource monitoring
- `nvidia-ml-py3` - GPU memory monitoring via NVML (for diagnostics and capacity testing)
- `gguf` - GGUF metadata parsing
- `llama-cpp-python` - GGUF model inference
- `gguf-parser` - Fast GGUF metadata extraction (optional)

**Optional Engine Dependencies**:
- `vllm` + `torch` - For vLLM engine (HuggingFace models)
- `exllamav3` - For ExLlamaV3 engine (GPTQ/EXL3 models)

**Ecosystem** (expected in shared venv):
- `universal_logging` - Structured logging
- `universal-event-bus` - Event messaging
- `universal_transport` - Transport layer
- `universal_protocol` - Protocol layer

See main CONTRIBUTING.md for how to set up the ecosystem components.

### Development Workflow

1. **Format Detection**: `utils/format_detector.py` identifies model format
2. **Engine Selection**: Appropriate engine instantiated based on format
3. **Model Loading**: Engine loads model with optimized configuration
4. **Inference**: Regular or streaming generation via engine-specific implementation

### Testing

Each engine has diagnostic scripts in `diagnostics/{engine_name}/`:

```bash
# Test GGUF engine
cd diagnostics/gguf
python cpu_inference_test.py --model /path/to/model.gguf

# Test vLLM engine
cd diagnostics/vllm
python vllm_memory_test.py --model /path/to/model

# Test ExLlamaV3 engine
cd diagnostics/exl3
python exl3_inference_test.py --model /path/to/model
```

### Benchmarking

Run benchmarks to test performance:

```bash
cd benchmark
python run_benchmark.py --config configs/gpu/llama_chat.yaml
```

### Configuration Generation

Generate optimized GGUF configurations:

```bash
python scripts/gguf_model_config_generator.py \
    /path/to/model.gguf \
    --test-gpu \
    --test-cpu \
    --output config.yaml
```

See [scripts/README_GGUF_CONFIG_GENERATOR.md](scripts/README_GGUF_CONFIG_GENERATOR.md) for details.

## Code Style

All code must follow the ecosystem standards:
- **Line length**: 88 characters
- **Quote style**: Double quotes
- **Import organization**: Ruff's isort rules
- **Type hints**: BasedPyright type checking

Run before committing:

```bash
# Format code
ruff format .

# Fix auto-fixable issues
ruff check --fix .

# Check for remaining issues
ruff check .
```

## Architecture Principles

1. **Engine Abstraction**: All engines implement `BaseEngine` interface
2. **Modular Design**: Each engine has dedicated modules for loading, inference, parameters, formatting
3. **Clean Separation**: Transport (bytes) → Protocol (semantics) → Application (business logic)
4. **Async-First**: All inference operations are async for better concurrency
5. **OpenAI Compatibility**: Responses match OpenAI API format
6. **No Defaults**: Engines never apply defaults - parameters used exactly as provided

## Questions?

- Main docs: `../universal-llm-gateway/CONTRIBUTING.md`
- Neovim setup: `../universal-llm-gateway/docs/NEOVIM_SETUP.md`
- Author: krunch3r76 ([@krunch3r76](https://github.com/krunch3r76))
- Email: biz@u26a4.com

