"""
Audio System - Audio processing, profiles, and pipelines.

Components:
- profiles: VAD and Whisper quality profile management
- api: HTTP endpoints for audio streaming
- streaming: VAD/Whisper processing (future)
- pipelines: Audio workflow orchestration (future)

Current Functionality:
    from systems.audio import AudioProfileManager

    manager = AudioProfileManager()
    vad_profile = manager.get_vad_profile("streaming-optimized")

Future Audio Pipelines:
    from systems.audio.pipelines import AudioPipelineExecutor

    # VAD → Whisper → LLM Translation
    pipeline = AudioPipelineExecutor(registry)
    result = await pipeline.execute(audio_stream)
"""

from .profiles.manager import AudioProfileManager

__all__ = [
    "AudioProfileManager",
]
