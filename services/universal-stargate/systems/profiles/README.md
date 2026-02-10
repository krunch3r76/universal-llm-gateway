# Profiles System

Generation parameter profiles for multi-engine LLM inference.

## Overview

Manages profiles that define generation parameters (temperature, top_p, etc.)
with automatic conversion between inference engines (llama-cpp, vLLM).

## Key Behaviors (Fail-Fast)

- **Missing `profiles.yaml`** → Startup fails immediately (no empty fallback)
- **Unknown request profile** → ValueError raised (no warning)
- **Unknown profile in set_*()** → ValueError raised
- **User params** → Never overridden by profiles (fill-only semantics)

## Usage

### Initialization (Startup)

```python
from pathlib import Path
from systems.profiles import ProfileManager, ProfileConfigLoader

# Load config at startup - FAILS if profiles.yaml missing
config_loader = ProfileConfigLoader(Path("config/profiles.yaml"))

# Create manager with loaded config
profile_manager = ProfileManager(config_loader=config_loader)
```

### Request Handling

```python
# Get complete profile for a model
profile_data = profile_manager.get_complete_profile(
    model_id="qwen2-5-coder-32b-awq",
    user_params={"temperature": 0.5},  # User params never overridden
    request_profile="creative",         # Raises ValueError if unknown!
    model_info={"format": "awq"},        # For engine detection
)

print(profile_data.params)           # Engine-specific parameters
print(profile_data.system_prompt)    # System prompt if defined
print(profile_data.actions)          # What was applied
```

### Profile Assignment

```python
# Set global profile (applies to all models)
profile_manager.set_global_profile("general-chat")  # Raises if unknown

# Set model-specific profile (overrides global)
profile_manager.set_model_profile("qwen2-5-coder-32b-awq", "code-generation")

# Auto-assignment via basename matching (automatic)
profile_manager.ensure_profile_assigned("deepseek-coder-33b-awq")
```

## Architecture

- **core/manager.py**: Thin orchestrator (~80 SLOC)
- **core/resolution.py**: Profile chain resolution
- **core/assignment.py**: Global/model assignment state
- **core/types.py**: ProfileData dataclass
- **config/loader.py**: ProfileConfigLoader - startup YAML loading
- **conversion/**: EngineMapper, ParameterConverter

## Dependencies

Leaf dependency - only imports from `utils.model_basename` and `universal_logging`.

## Configuration

- `config/profiles.yaml` - Profile definitions (REQUIRED - startup fails without it)
- `config/model_profiles.yaml` - Basename → profile name mappings

Note: Engine format mappings (gguf→llama_cpp, awq→vllm, etc.) are hardcoded
in `EngineMapper` since they're stable and don't require external configuration.
