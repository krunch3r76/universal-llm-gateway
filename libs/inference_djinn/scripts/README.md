# Inference Djinn Scripts

This directory contains scripts and tools for building, configuring, and testing inference engines.

## Directory Structure

### `config_generators/`
Model configuration generation tools that analyze models and generate gateway-compatible YAML configurations.

- **`gguf_model_config_generator.py`** - Entry point for GGUF model configuration generation
- **`vllm_model_config_generator.py`** - Entry point for vLLM model configuration generation
- **`gguf/`** - GGUF configuration generator package
  - `main.py` - Core configuration generation logic
  - `api_client.py` - API integration for pushing configurations
  - `caching.py` - Configuration caching utilities
  - `profiles.py` - Model profile management
  - `testing.py` - Model testing and resource measurement
  - `utils.py` - Shared utilities
- **`vllm/`** - vLLM configuration generator package (future modularization)

### `build/`
Build infrastructure for inference engines and dependencies.

#### `build/python_builders/`
Python-based build tools with unified configuration management.

- **`common/`** - Shared build utilities
  - `cmake_config.py` - Unified CMAKE configuration generator
  - `cpu_detector.py` - CPU capability detection
  - `gpu_detector.py` - GPU capability detection
  - `environment.py` - Build environment setup
  - `system_checker.py` - System compatibility checking
  - `utils.py` - Build utilities
- **`llama_cpp/`** - llama-cpp-python builder
  - `builder.py` - Main build orchestration
  - `build_llama_cpp.py` - Build script
- **`vllm/`** - vLLM builder
  - `builder.py` - Main build orchestration
  - `build_vllm.py` - Build script
  - `patches.py` - vLLM patches and fixes

#### `build/shell/`
Shell-based build scripts organized by engine.

- **`llama_cpp/`** - llama.cpp build scripts
  - `build_llama_cpp.sh` - Main build script
  - `openblas/` - OpenBLAS optimization scripts
- **`vllm/`** - vLLM build scripts
  - `build_vllm.sh` - Standard vLLM build
  - `build_vllm_blackwell_native.sh` - Blackwell-optimized build
  - `build_flash_attention.sh` - Flash Attention build
  - `VLLM_BUILD_SCRIPT_UPDATES.md` - Build script documentation
- **`exlamma/`** - ExLlama build scripts
  - `build_exllamav3_blackwell.sh` - ExLlama v3 Blackwell build

#### `build/install/`
Installation and dependency management scripts.

- **`install_pytorch_nightly.sh`** - PyTorch nightly installer with Blackwell support
- **`vllm/`** - vLLM installation utilities
  - `auto_install_dependencies.py` - Automatic dependency resolution

### `tests/`
Test scripts for configuration generators and model validation.

- **`gguf/`** - GGUF-related tests
  - `simple_cpu_only_memory_test.py` - CPU-only memory testing
  - `simple_gpu_layer_test.py` - GPU layer testing
- **`vllm/`** - vLLM-related tests
  - `vllm_memory_test.py` - vLLM memory usage testing

## Usage Examples

### Configuration Generation

```bash
# Generate GGUF model configuration
python config_generators/gguf_model_config_generator.py /path/to/model.gguf

# Alternative: Run as module (from project root)
python -m libs.inference_djinn.scripts.config_generators.gguf_model_config_generator /path/to/model.gguf

# Generate vLLM model configuration
python config_generators/vllm_model_config_generator.py /path/to/model/

# With specific options
python config_generators/gguf_model_config_generator.py /path/to/model.gguf --cpu-only --push
python config_generators/vllm_model_config_generator.py /path/to/model/ --yaml --contexts 8192,16384
```

### Build Tools

```bash
# Use unified CMAKE configuration
python build/python_builders/common/cmake_config.py --cpu-mode=avx2 --gpu-arch=89 --summary

# Build llama-cpp-python
python build/python_builders/llama_cpp/build_llama_cpp.py --portable

# Build vLLM
python build/python_builders/vllm/build_vllm.py --gpu-arch=120

# Install PyTorch nightly
bash build/install/install_pytorch_nightly.sh
```

### Testing

```bash
# Test GGUF model memory usage
python tests/gguf/simple_cpu_only_memory_test.py /path/to/model.gguf

# Test vLLM memory usage
python tests/vllm/vllm_memory_test.py /path/to/model/
```

## Integration with Docker

The Docker build system uses these scripts for consistent builds:

- `Dockerfile.gpu` copies `build/python_builders/` and `build/install/vllm/`
- `docker/build-gpu.sh` uses `build/python_builders/common/cmake_config.py` for configuration

## Migration Notes

This structure was reorganized from the previous flat layout:

| Old Path | New Path |
|----------|----------|
| `python_builders/` | `build/python_builders/` |
| `vllm/` (shell scripts) | `build/shell/vllm/` |
| `vllm/auto_install_dependencies.py` | `build/install/vllm/auto_install_dependencies.py` |
| `llama-cpp/` | `build/shell/llama_cpp/` |
| `exlamma/` | `build/shell/exlamma/` |
| `gguf_config_generator/` | `config_generators/gguf/` |
| `gguf_model_config_generator.py` | `config_generators/gguf_model_config_generator.py` |
| `vllm_model_config_generator.py` | `config_generators/vllm_model_config_generator.py` |
| `gguf_config_generator/test_scripts/` | `tests/gguf/` |
| `vllm_test_scripts/` | `tests/vllm/` |
| `install_pytorch_nightly.sh` | `build/install/install_pytorch_nightly.sh` |

All Docker references and import paths have been updated accordingly.
