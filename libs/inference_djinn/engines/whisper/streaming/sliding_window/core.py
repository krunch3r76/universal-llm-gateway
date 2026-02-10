"""Async SlidingWindowBuffer class with composition.

Thread Safety: Not needed for async methods. Audio capture integration
uses asyncio.run_coroutine_threadsafe() to safely bridge from audio
callback thread to async event loop.
"""

import asyncio
from universal_logging import get_logger
import time
from collections.abc import Callable

import numpy as np

from ..config import EnhancedConfig
from ..types import TranscriptionResult
from .audio_management import AudioManager, calculate_adaptive_preservation_ms
from .boundary_finder import BoundaryFinder
from .boundary_selection import select_boundary
from .second_pass_searcher import SecondPassSearcher
from .transcription import Transcriber
from .vad_detection import VADDetector

logger = get_logger(__name__)

SAMPLE_RATE = 16000


class AsyncSlidingWindowBuffer:
    """
    Async buffer with multi-window processing and smart boundary detection.

    Thread Safety: Not needed. All async operations run in single event loop.
    Audio capture integration uses run_coroutine_threadsafe() for thread-safe
    bridging from audio callback thread.

    Uses composition to delegate to specialized handlers:
    - AudioManager: Buffer operations
    - VADDetector: Voice activity detection
    - Transcriber: Whisper transcription
    - BoundaryFinder: Speech boundary detection
    """

    def __init__(
        self,
        client_id: str,
        config: EnhancedConfig,
        whisper_model=None,
        beam_size_func: Callable[[], int] | None = None,
        event_loop: asyncio.AbstractEventLoop | None = None,
    ):
        """Initialize async sliding window buffer.

        Args:
            client_id: Client identifier for logging
            config: Streaming configuration
            whisper_model: faster-whisper model instance
            beam_size_func: Function returning beam size (default: returns 5)
            event_loop: Event loop for thread-safe audio capture (auto-detected if None)
        """
        self.client_id = client_id
        self.config = config

        # Event loop for thread-safe audio capture bridging
        self._event_loop = event_loop

        # Async queue for audio chunks (created in start() when loop is available)
        self._audio_queue: asyncio.Queue[np.ndarray] | None = None

        # Processing state (no lock needed - single async context)
        self._processing = False
        self._processor_task: asyncio.Task | None = None

        # Window size calculations
        self.min_window_size = int(SAMPLE_RATE * config.min_window_duration)
        self.max_window_size = int(SAMPLE_RATE * config.max_window_duration)

        # Result tracking
        self.next_result_id = 0
        self._last_boundary_confidence = 0.0
        self._pending_results: list[TranscriptionResult] = []

        # Initialize component handlers
        self.audio_manager = AudioManager(self)
        self.vad_detector = VADDetector(self, config)
        self.boundary_finder = BoundaryFinder(self)
        self.second_pass_searcher = SecondPassSearcher(self)

        # Transcriber requires whisper model
        def default_beam_size() -> int:
            return 5

        if beam_size_func is None:
            beam_size_func = default_beam_size

        self.transcriber = Transcriber(self, whisper_model, beam_size_func)

        # Set forced language if provided
        if config.language:
            self.transcriber.set_forced_language(config.language)

        logger.info(f"🚀 Async streaming ASR buffer created for {client_id}")
        logger.info(
            f"   Window: {config.min_window_duration}s - {config.max_window_duration}s"
        )
        logger.info(f"   VAD: {config.vad_method.value.upper()}")

    async def start(self) -> None:
        """Start async processing loop."""
        if self._processing:
            return

        self._processing = True

        # Store event loop for thread-safe audio capture
        if self._event_loop is None:
            self._event_loop = asyncio.get_running_loop()

        # Create audio queue now that event loop is running
        if self._audio_queue is None:
            self._audio_queue = asyncio.Queue(maxsize=100)

        # Start background processor
        self._processor_task = asyncio.create_task(
            self._process_audio_loop(), name=f"AudioProcessor-{self.client_id}"
        )

        logger.info(f"Started async audio processor for {self.client_id}")

    async def stop(self) -> None:
        """Stop async processing loop."""
        self._processing = False

        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
            self._processor_task = None

        logger.info(f"Stopped async audio processor for {self.client_id}")

    def add_audio_from_thread(self, audio_data: np.ndarray) -> None:
        """Add audio from callback thread (thread-safe).

        This method is called from audio capture callback threads (OS/driver threads).
        It uses run_coroutine_threadsafe() to safely bridge to the async event loop.

        Args:
            audio_data: Audio samples as numpy array (int16)
        """
        if self._event_loop is None or self._event_loop.is_closed():
            logger.warning("Event loop not available for audio capture")
            return

        # Schedule async add in event loop (thread-safe)
        asyncio.run_coroutine_threadsafe(
            self._enqueue_audio(audio_data.copy()), self._event_loop
        )

    async def add_audio(self, audio_data: np.ndarray) -> list[TranscriptionResult]:
        """Add audio and get results (async - for async callers).

        Args:
            audio_data: Audio samples as numpy array

        Returns:
            List of transcription results (may be empty)
        """
        await self._enqueue_audio(audio_data)
        return self.get_pending_results()

    async def _enqueue_audio(self, audio_data: np.ndarray) -> None:
        """Enqueue audio chunk for processing.

        No lock needed - asyncio.Queue is thread-safe for put operations.
        """
        if self._audio_queue is None:
            logger.warning(
                f"Audio queue not initialized for {self.client_id}, dropping chunk"
            )
            return

        try:
            self._audio_queue.put_nowait(audio_data)
        except asyncio.QueueFull:
            logger.warning(f"Audio queue full for {self.client_id}, dropping chunk")

    async def _process_audio_loop(self) -> None:
        """Background loop that processes audio from queue.

        No lock needed - single async context.
        """
        if self._audio_queue is None:
            logger.error(f"Audio queue not initialized for {self.client_id}")
            return

        while self._processing:
            try:
                # Wait for audio chunk with timeout
                try:
                    audio_data = await asyncio.wait_for(
                        self._audio_queue.get(), timeout=0.5
                    )
                except TimeoutError:
                    continue

                # Process the audio chunk
                results = self._process_audio_chunk(audio_data)

                # Store results for retrieval
                if results:
                    self._pending_results.extend(results)

                self._audio_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing audio: {e}")

    def _process_audio_chunk(self, audio_data: np.ndarray) -> list[TranscriptionResult]:
        """Process single audio chunk (synchronous - no lock needed).

        Called only from _process_audio_loop in single async context.
        """
        t0 = time.perf_counter()
        self.audio_manager.add_audio(audio_data)
        results = self._process_buffer()
        t1 = time.perf_counter()

        if results:
            logger.info(
                f"⏱️  Chunk processing: {t1-t0:.3f}s for {len(results)} result(s)"
            )

        return results

    def _process_buffer(self) -> list[TranscriptionResult]:
        """Process buffer and return any transcription results."""
        t0 = time.perf_counter()
        results: list[TranscriptionResult] = []

        if not self.audio_manager.has_minimum_audio(self.min_window_size):
            return results

        if not self.boundary_finder.should_check_boundary():
            return results

        # Select boundary using Silero probability analysis
        t1 = time.perf_counter()
        boundary_offset, confidence = select_boundary(self)
        t2 = time.perf_counter()

        if boundary_offset is None:
            return results

        # Store confidence for overlap correction
        self._last_boundary_confidence = confidence

        # Process if boundary found
        t3 = time.perf_counter()
        result = self._process_up_to_boundary(boundary_offset)
        t4 = time.perf_counter()

        if result:
            results.append(result)
            self.boundary_finder.reset_boundary_timer()
            logger.debug(
                f"⏱️  Buffer processing breakdown: "
                f"boundary_detect={t2-t1:.3f}s, "
                f"transcribe={t4-t3:.3f}s, "
                f"total={t4-t0:.3f}s"
            )

        return results

    def _process_up_to_boundary(
        self,
        boundary_offset: int,
    ) -> TranscriptionResult | None:
        """Process audio up to boundary offset."""
        audio_mgr = self.audio_manager
        vad = self.vad_detector

        # Validate boundary
        if boundary_offset <= 0 or boundary_offset > audio_mgr.get_buffer_bytes():
            return None

        # Align to frame boundary
        frame_size = vad.get_frame_size_samples(self.config.vad_method)
        aligned_offset = audio_mgr.align_to_frame_boundary(boundary_offset, frame_size)

        # Check minimum transcription size
        min_bytes = self.min_window_size * 2 + frame_size * 2
        if aligned_offset < min_bytes:
            logger.debug(
                f"Segment too short: {aligned_offset} bytes < {min_bytes} bytes"
            )
            return None

        # Extract and transcribe
        audio_bytes = audio_mgr.extract_audio_up_to(aligned_offset)
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)

        start_time = audio_mgr.get_start_time()

        result = self.transcriber.transcribe_segment(
            audio_array,
            start_time,
            use_whisper_vad=self.config.use_whisper_vad,
            whisper_vad_params=(
                {
                    "threshold": self.config.whisper.threshold,
                    "min_silence_duration_ms": self.config.whisper.min_silence_duration_ms,
                    "speech_pad_ms": self.config.whisper.speech_pad_ms,
                }
                if self.config.use_whisper_vad
                else None
            ),
        )

        if result:
            # Apply configured boundary preservation mode
            if self.config.boundaries.adaptive_preservation_enabled:
                preservation_ms = calculate_adaptive_preservation_ms(result)
                logger.debug(f"Adaptive preservation: {preservation_ms}ms")
            else:
                preservation_ms = 0
                logger.debug("Boundary preservation disabled (exact cutoff)")

            audio_mgr.remove_processed_audio(aligned_offset, preservation_ms)
            self.next_result_id += 1
            logger.debug(f"Transcribed: '{result.text[:50]}...'")

        return result

    def get_pending_results(self) -> list[TranscriptionResult]:
        """Get and clear pending transcription results.

        No lock needed - single async context.
        """
        results = self._pending_results[:]
        self._pending_results.clear()
        return results

    async def flush(self) -> list[TranscriptionResult]:
        """Process remaining buffer as complete utterance.

        Returns:
            List of final transcription results
        """
        # Wait for queue to drain
        if self._audio_queue is not None:
            await self._audio_queue.join()

        # Process any remaining audio
        result = self._process_complete_utterance()
        if result:
            return [result]
        return []

    def _process_complete_utterance(self) -> TranscriptionResult | None:
        """Process entire buffer as complete utterance."""
        audio_mgr = self.audio_manager

        buffer_bytes = audio_mgr.get_buffer_bytes()
        if buffer_bytes < self.min_window_size * 2:
            return None

        audio_array = audio_mgr.get_buffer_audio_int16()
        start_time = audio_mgr.get_start_time()

        result = self.transcriber.transcribe_segment(
            audio_array,
            start_time,
            use_whisper_vad=self.config.use_whisper_vad,
        )

        if result:
            samples = len(audio_array)
            audio_mgr.advance_stream_time(samples)
            audio_mgr.clear_buffer()
            self.next_result_id += 1

        return result

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()


# Backward compatibility alias
SlidingWindowBuffer = AsyncSlidingWindowBuffer
