"""
Universal Stargate Systems - Major Subsystems

Subsystems:
- pipeline: Multi-model LLM workflow orchestration (DAG-based)
- audio: Audio processing (VAD, Whisper, profiles, pipelines)
- routing: Model selection and routing decisions
- transformations: Message/format transformations
- profiles: Generation parameter profiles
- proxy: HTTP request/response handling

Usage:
    from systems.pipeline import PipelineExecutor
    from systems.audio import AudioProfileManager
    from systems.routing import DecisionEngine
    from systems.transformations import TransformationEngine
    from systems.profiles import ProfileManager
"""

# Modules available but not auto-imported to avoid runtime dependencies
# Use direct imports as shown above

__all__ = []
