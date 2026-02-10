"""Whisper speech-to-text engine implementing BaseEngine interface."""

import asyncio
import base64
from universal_logging import get_logger
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import numpy as np
import torch
import whisper

from inference_djinn.engines.base import BaseEngine

from .audio import load_and_preprocess

logger = get_logger(__name__)


class WhisperEngine(BaseEngine):
    """
    Whisper speech-to-text engine implementing BaseEngine interface.

    Supports:
    - Batch file transcription
    - Real-time streaming transcription via WebSocket
    - Multiple Whisper model sizes (large-v3, medium, small, etc.)
    - GPU and CPU inference
    """

    def __init__(self, model_path: str, **kwargs):
        """
        Initialize Whisper engine (OpenAI Whisper / PyTorch backend).

        Args:
            model_path: Whisper model path or identifier
            **kwargs: Engine configuration including:
                - device: "cuda" or "cpu" (default: "cuda")
                - compute_type: Precision/quantization ("float16", "int8", "float32")
                - fp16: DEPRECATED - Use compute_type instead
                - beam_size: Beam search width (default: 5)
                - cpu_threads: Number of CPU threads (relevant for CPU inference)
        """
        super().__init__(model_path, **kwargs)
        self.engine_type = "whisper"

        # Configuration with defaults
        self.device = kwargs.get("device", "cuda")
        self.cpu_threads = kwargs.get("cpu_threads", 4 if self.device == "cuda" else 8)

        # Handle compute_type (new) or fp16 (legacy)
        if "compute_type" in kwargs:
            # Normalize to lowercase for consistent comparison
            self.compute_type = kwargs["compute_type"].lower()
            # Map compute_type to fp16 for whisper.transcribe()
            self.fp16 = self._compute_type_to_fp16(self.compute_type, self.device)
        elif "fp16" in kwargs:
            # Legacy fp16 parameter
            self.fp16 = kwargs["fp16"] if self.device == "cuda" else False
            self.compute_type = "float16" if self.fp16 else "float32"
        else:
            # Defaults
            if self.device == "cuda":
                self.compute_type = "float16"
                self.fp16 = True
            else:
                self.compute_type = "int8"
                self.fp16 = False

        self.beam_size = kwargs.get("beam_size", 5)

        # Model reference (loaded in load())
        self.model = None

        # Streaming service (initialized after model loads)
        self._streaming_service = None

    @staticmethod
    def _compute_type_to_fp16(compute_type: str, device: str) -> bool:
        """
        Map compute_type to fp16 parameter for whisper.transcribe().

        Args:
            compute_type: Precision string ("float16", "int8", "float32")
            device: Device string ("cuda" or "cpu")

        Returns:
            fp16 boolean for whisper API

        Raises:
            ValueError: If compute_type is not a supported value
        """
        # Validate compute_type
        valid_types = {"float16", "float32", "int8"}
        if compute_type not in valid_types:
            raise ValueError(
                f"Unsupported compute_type: {compute_type}. "
                f"Must be one of: {', '.join(valid_types)}"
            )

        if device == "cpu":
            return False
        return compute_type == "float16"

    @staticmethod
    def _validate_checkpoint_path(model_path: str) -> Path:
        """
        Validate checkpoint file exists at configured path.

        Args:
            model_path: Path to Whisper checkpoint file

        Returns:
            Validated Path object

        Raises:
            FileNotFoundError: If checkpoint doesn't exist with setup instructions
        """
        checkpoint_path = Path(model_path)

        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Whisper model not found: {checkpoint_path}\n"
                f"For privacy-focused deployment, download directly:\n"
                f"  wget https://openaipublic.azureedge.net/main/whisper/models/...\n"
                f"  Or use: python -c \"import whisper; whisper.load_model('large-v3', download_root='/mnt/torus/models/whisper-checkpoints')\"\n"
                f"Then configure explicit path in model catalog."
            )

        return checkpoint_path

    async def load(self) -> None:
        """Load Whisper model directly from local checkpoint file."""
        logger.info(f"🔧 Loading Whisper model: {self.model_path}")
        logger.info(
            f"   Device: {self.device}, Compute Type: {self.compute_type}, "
            f"CPU Threads: {self.cpu_threads}"
        )

        # Privacy-focused: Use explicit configured path only (no HuggingFace cache)
        checkpoint_path = self._validate_checkpoint_path(self.model_path)
        logger.info(f"   ✅ Found model checkpoint: {checkpoint_path}")

        def _load_sync():
            # Load model directly from checkpoint file
            # whisper.load_model() accepts a path to a .pt file
            model = whisper.load_model(
                str(checkpoint_path),
                device=self.device,
                download_root=None,  # Prevent any downloads
            )
            return model

        loop = asyncio.get_event_loop()
        self.model = await loop.run_in_executor(None, _load_sync)
        self.loaded = True

        # Initialize streaming service
        from ..streaming.service import StreamingASRService

        self._streaming_service = StreamingASRService(
            self.model, beam_size_func=lambda: self.beam_size
        )

        logger.info("✅ Whisper model loaded successfully")
        logger.info("✅ Streaming service initialized")

    async def generate(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Transcribe audio file (batch mode).

        Args:
            data: Request data with:
                - audio_file_path: Path to audio file
                - language: Optional language code (e.g., "en", "fa")
                - temperature: Sampling temperature (default: 0.0)
                - word_timestamps: Whether to include word-level timestamps

        Returns:
            OpenAI-compatible transcription response
        """
        if not self.loaded or self.model is None:
            raise RuntimeError("Whisper model not loaded")

        audio_path = data.get("audio_file_path")
        if not audio_path:
            raise ValueError("audio_file_path required")

        language = data.get("language")
        beam_size = data.get("beam_size", self.beam_size)
        temperature = data.get("temperature", 0.0)
        word_timestamps = data.get("word_timestamps", True)

        def _transcribe_sync():
            # Load and preprocess audio using audio.py utilities
            audio, duration = load_and_preprocess(audio_path)

            # Transcribe using openai-whisper
            result = self.model.transcribe(
                audio,
                language=language,
                beam_size=beam_size,
                temperature=temperature,
                word_timestamps=word_timestamps,
                fp16=self.fp16,
            )

            return result, duration

        loop = asyncio.get_event_loop()
        result, duration = await loop.run_in_executor(None, _transcribe_sync)

        # Format response (OpenAI-compatible)
        # openai-whisper already returns the correct format
        return {
            "text": result["text"],
            "segments": result.get("segments", []),
            "language": result.get("language", language or "en"),
            "duration": duration,
        }

    async def generate_stream(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming not applicable for batch file transcription."""
        raise NotImplementedError(
            "Whisper batch transcription does not support streaming. "
            "Use WebSocket /v1/audio/live_transcribe for real-time streaming."
        )
        # Yield statement required for AsyncGenerator type
        yield {}  # pragma: no cover

    async def unload(self) -> None:
        """Unload model and free GPU memory."""
        logger.info("🧹 Unloading Whisper model...")

        # Close all streaming sessions
        if self._streaming_service is not None:
            for session_id in self._streaming_service.get_session_ids():
                pending_results = await self._streaming_service.close_session(session_id)
                if pending_results:
                    logger.warning(
                        f"⚠️ Session {session_id} had {len(pending_results)} pending "
                        f"results that were discarded during unload"
                    )
            self._streaming_service = None

        if self.model is not None:
            del self.model
            self.model = None

        # Clear GPU cache
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        self.loaded = False
        logger.info("✅ Whisper model unloaded")

    # === Streaming Session Methods ===

    async def create_stream_session(self, config: dict) -> str:
        """
        Create a new streaming session.

        Args:
            config: Streaming configuration dict with:
                - language: Optional language code (e.g., "en", "fa")
                - vad_method: VAD method ("silero", "webrtc", "energy")
                - min_window_duration: Minimum window before boundary check
                - max_window_duration: Maximum window before forced processing

        Returns:
            session_id: Unique identifier for this session
        """
        if not self._streaming_service:
            raise RuntimeError("Streaming service not initialized")

        session_id = await self._streaming_service.create_session(config)
        logger.info(f"📡 Created streaming session: {session_id}")
        return session_id

    async def process_audio_chunk(
        self,
        session_id: str,
        audio_bytes: bytes | str,
    ) -> list[dict]:
        """
        Process an audio chunk for a streaming session.

        Args:
            session_id: Session identifier
            audio_bytes: Raw PCM audio (16-bit, 16kHz) or base64-encoded string

        Returns:
            List of transcription results as dicts (may be empty)
        """
        if not self._streaming_service:
            raise RuntimeError("Streaming service not initialized")

        # Handle base64 encoded audio
        if isinstance(audio_bytes, str):
            audio_bytes = base64.b64decode(audio_bytes)

        # Convert bytes to numpy array
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)

        # Process through streaming service
        results = await self._streaming_service.process_audio(session_id, audio_array)

        # Convert to dict format for RPC
        return [result.to_dict() for result in results]

    async def close_stream_session(self, session_id: str) -> list[dict]:
        """
        Close a streaming session and cleanup resources.

        Args:
            session_id: Session identifier

        Returns:
            List of pending transcription results (may be empty)
        """
        if not self._streaming_service:
            return []

        # Capture pending overlap-corrected results before closing
        pending_results = await self._streaming_service.close_session(session_id)
        logger.info(
            f"📡 Closed streaming session: {session_id} "
            f"(flushed {len(pending_results)} pending results)"
        )

        # Convert to dict format for RPC
        return [result.to_dict() for result in pending_results]

    def get_model_info(self) -> dict[str, Any]:
        """Get model information."""
        return {
            "engine_type": self.engine_type,
            "model_path": self.model_path,
            "device": self.device,
            "compute_type": self.compute_type,
            "beam_size": self.beam_size,
            "loaded": self.loaded,
            "supports_streaming": True,  # Via WebSocket (Phase 2)
            "supports_batch": True,
        }

    async def count_tokens_for_messages(
        self,
        messages_or_prompt: list[dict[str, Any]] | str,
        use_cpu: bool = True,
        context_length: int | None = None,
    ):
        """Not applicable for audio models."""
        raise NotImplementedError("Token counting not applicable for audio models")
