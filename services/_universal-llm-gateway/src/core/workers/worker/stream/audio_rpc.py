"""Audio RPC handlers for Whisper streaming + transcription (worker-side)."""

from typing import Any

from universal_logging import get_logger
from universal_protocol.errors import EngineError

logger = get_logger(__name__)


class WhisperStreamingHandlers:
    """Mix-in class providing Whisper-specific RPC handler methods.

    Assumes: self.engine, self.model_loaded exist.
    """

    async def handle_create_stream_session(self, params: dict[str, Any]) -> str:
        """Handle create_stream_session RPC for Whisper streaming."""
        if not self.engine or not self.engine.is_loaded():
            raise EngineError(code="MODEL_NOT_LOADED", message="Model not loaded")

        if not hasattr(self.engine, "create_stream_session"):
            raise EngineError(
                code="UNSUPPORTED_OPERATION",
                message="Model does not support streaming sessions",
            )

        config = params.get("config", {})
        session_id = await self.engine.create_stream_session(config)
        logger.info(f"📡 [worker] Created streaming session: {session_id}")
        return session_id

    async def handle_process_audio_chunk(self, params: dict[str, Any]) -> list:
        """Handle process_audio_chunk RPC for Whisper streaming."""
        if not self.engine or not self.engine.is_loaded():
            raise EngineError(code="MODEL_NOT_LOADED", message="Model not loaded")

        if not hasattr(self.engine, "process_audio_chunk"):
            raise EngineError(
                code="UNSUPPORTED_OPERATION",
                message="Model does not support audio chunk processing",
            )

        session_id = params.get("session_id")
        if not session_id:
            raise EngineError(code="INVALID_PARAMS", message="session_id is required")

        audio_bytes = params.get("audio_bytes")
        if not audio_bytes:
            raise EngineError(code="INVALID_PARAMS", message="audio_bytes is required")

        results = await self.engine.process_audio_chunk(session_id, audio_bytes)

        if results:
            logger.debug(
                f"📡 [worker] Session {session_id}: {len(results)} transcriptions"
            )

        return results

    async def handle_close_stream_session(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle close_stream_session RPC for Whisper streaming."""
        if not self.engine:
            return {"success": True, "pending_results": []}

        if not hasattr(self.engine, "close_stream_session"):
            return {"success": True, "pending_results": []}

        session_id = params.get("session_id")
        if not session_id:
            raise EngineError(code="INVALID_PARAMS", message="session_id is required")

        pending_results = await self.engine.close_stream_session(session_id)

        if pending_results:
            logger.info(
                f"📡 [worker] Closed streaming session: {session_id} "
                f"(flushed {len(pending_results)} pending results)"
            )
        else:
            logger.info(f"📡 [worker] Closed streaming session: {session_id}")

        return {"success": True, "pending_results": pending_results}

    async def handle_transcribe_file(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle transcribe_file RPC for Whisper file transcription."""
        if not self.engine or not self.engine.is_loaded():
            raise EngineError(code="MODEL_NOT_LOADED", message="Model not loaded")

        audio_path = params.get("audio_file_path")
        if not audio_path:
            raise EngineError(code="INVALID_PARAMS", message="audio_file_path required")

        language = params.get("language")
        prompt = params.get("prompt")
        temperature = params.get("temperature", 0.0)
        word_timestamps = params.get("word_timestamps", True)

        logger.info(f"🎤 [worker] Transcribing file: {audio_path}")

        result = await self.engine.generate(
            {
                "audio_file_path": audio_path,
                "language": language,
                "prompt": prompt,
                "temperature": temperature,
                "word_timestamps": word_timestamps,
            }
        )

        logger.info(
            f"✅ [worker] Transcription complete: {len(result.get('text', ''))} chars"
        )
        return result
