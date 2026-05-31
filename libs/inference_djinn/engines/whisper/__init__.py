"""Whisper speech-to-text engine for inference_djinn."""


# Lazy import to avoid loading torch when only importing config/types
# This allows Stargate (proxy) to import config classes without torch dependency
def __getattr__(name: str):
    if name == "WhisperEngine":
        from .engine.engine import WhisperEngine

        return WhisperEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["WhisperEngine"]
