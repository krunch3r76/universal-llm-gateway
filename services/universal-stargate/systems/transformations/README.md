# Transformations System

Message and format transformations for LLM requests.

## Overview

Converts between OpenAI Chat Completions format (messages) and plain text prompts,
applies model-specific transformations based on `config/model_transformations.yaml`.

## Usage

### Initialization (Startup)

```python
from pathlib import Path
from systems.transformations import TransformationEngine, TransformationConfigLoader

# Load config at startup (only I/O happens here)
config_loader = TransformationConfigLoader(
    Path("config/model_transformations.yaml")
)

# Create engine with loaded config
engine = TransformationEngine(config_loader=config_loader)
```

### Request Handling

```python
from model_id import ModelId
from systems.transformations import OutputFormat

# Parse model at API boundary
model = ModelId.parse("wizard-vicuna-30b-awq")

# Transform (no I/O - uses in-memory config)
result = engine.transform(
    messages=[{"role": "user", "content": "Hello"}],
    model=model,
    target_format=OutputFormat.PROMPT,
)

print(result.format)              # OutputFormat.PROMPT
print(result.content)             # "USER: Hello\nASSISTANT: "
print(result.transformation_applied)  # True
```

### Filter-Only (Keep Messages Format)

```python
# For models with input_schema="messages" that need filtering
filtered = engine.apply_filters_only(messages, model)
```

## Architecture

- **core/engine.py**: TransformationEngine orchestrator
- **core/types.py**: OutputFormat, TransformationResult
- **registry/**: TransformationRegistry and config loaders
- **implementations/**: template_based, cursorcore, generic

## Dependencies

Leaf dependency - only imports from `model_id` and `universal_logging`.

## Configuration

See `config/model_transformations.yaml` for transformation definitions.
