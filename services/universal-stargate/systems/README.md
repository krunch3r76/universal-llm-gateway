# Universal Stargate Systems

This directory contains the major subsystems of Universal Stargate.

## Systems

| System | Description | Entry Point |
|--------|-------------|-------------|
| `proxy/` | HTTP request handling, FastAPI app | `from systems.proxy import create_app` |
| `pipeline/` | Multi-model LLM workflows | `from systems.pipeline import PipelineExecutor` |
| `graphics/` | Image generation proxy | `from systems.graphics import router` |
| `audio/` | Audio processing and profiles | `from systems.audio import AudioProfileManager` |
| `routing/` | Gateway selection | `from systems.routing import DecisionEngine` |
| `transformations/` | Message/format transformations | `from systems.transformations import TransformationEngine` |
| `profiles/` | Generation parameter profiles | `from systems.profiles import ProfileManager` |

## Dependency Graph

```
proxy ──┬──→ pipeline ──→ routing
        ├──→ graphics ──→ routing
        ├──→ audio ────→ routing
        ├──→ transformations
        ├──→ profiles
        └──→ routing
```

- **transformations**: Leaf dependency (no imports from other systems)
- **profiles**: Leaf dependency (no imports from other systems)
- **routing**: Leaf dependency (no imports from other systems)
- **pipeline**: Depends on routing for model selection
- **graphics**: Depends on routing for gateway selection
- **audio**: Depends on routing for gateway selection
- **proxy**: Imports from all other systems

## Adding New Systems

1. Create `systems/{name}/` directory
2. Create `__init__.py` with public exports
3. Add system to `systems/__init__.py`
4. Update this README and project documentation

## Architecture Benefits

### Clear Boundaries
Each system has a well-defined responsibility:
- **proxy**: Accept HTTP requests, return HTTP responses
- **pipeline**: Orchestrate multi-step LLM workflows
- **graphics**: Proxy image generation requests to Gateway
- **audio**: Process audio streams and manage audio profiles
- **routing**: Select optimal gateway for model requests
- **transformations**: Convert message formats (messages ↔ prompt)
- **profiles**: Manage generation parameters (temperature, top_p, etc.)

### Acyclic Dependencies
The dependency graph is acyclic, preventing circular imports and making the system easier to reason about.

### Independent Understanding
Each system can be understood independently, with clear interfaces between systems.

### Expansion Ready
The structure easily accommodates new systems (e.g., `systems/vision/` for image processing).

## Import Patterns

### Preferred: System-Level Imports
```python
from systems.proxy import StargateProxy
from systems.pipeline import PipelineExecutor
from systems.graphics import router
from systems.audio import AudioProfileManager
from systems.routing import DecisionEngine
```

### Also Valid: Deep Imports
```python
from systems.routing.selection.decision.feasibility import FeasibilityTier
from systems.pipeline.core.dag import DAGBuilder
from systems.graphics.api.generations import router as graphics_router
from systems.audio.profiles.manager import AudioProfileManager
```

## System Details

### Proxy System (`systems/proxy/`)
HTTP API layer built on FastAPI. Handles:
- Request parsing and validation
- Response formatting and streaming
- Model loading coordination
- Non-streaming and streaming execution

### Pipeline System (`systems/pipeline/`)
Multi-model workflow orchestration. Features:
- DAG-based execution with dependencies
- Parallel execution of independent steps
- Model coordination (same-model serialization)
- Domain-specific extensions
- Execution summaries

### Audio System (`systems/audio/`)
Audio processing infrastructure. Components:
- VAD (Voice Activity Detection) profiles
- Whisper transcription profiles
- Audio API endpoints
- Future: Audio pipelines (VAD → Whisper → LLM)

### Routing System (`systems/routing/`)
Gateway selection and routing decisions. Capabilities:
- T0/T1/T2 feasibility evaluation
- Utility-based scoring
- Eviction planning
- Affinity-aware routing
- Capacity checking

### Transformations System (`systems/transformations/`)
Message and format conversion. Features:
- Template-based transformations
- Message filtering (system, truncation)
- Format conversion (messages → prompt)
- Model-specific prompting (e.g., CursorCore)

### Profiles System (`systems/profiles/`)
Generation parameter management. Features:
- Multi-engine support (llama-cpp, vLLM)
- Automatic parameter conversion
- Profile assignment (global, model-specific, request)
- System prompt injection
