"""WebSocket endpoint for real-time audio streaming transcription."""

import asyncio
import base64
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from universal_logging import get_logger

from .error_mapper import map_worker_error
from .params import (
    get_adaptive_preservation_enabled_param,
    get_beam_size_param,
    get_condition_on_previous_text_param,
    get_energy_threshold_param,
    get_inactivity_timeout_param,
    get_language_param,
    get_max_window_duration_param,
    get_max_window_leave_behind_ms_param,
    get_min_window_duration_param,
    get_min_word_probability_param,
    get_model_param,
    get_overlap_correction_enabled_param,
    get_overlap_duration_ms_param,
    get_overlap_hold_word_count_param,
    get_overlap_max_time_gap_ms_param,
    get_overlap_min_prefix_ratio_param,
    get_second_pass_enabled_param,
    get_second_pass_leave_behind_ms_param,
    get_second_pass_max_search_depth_ms_param,
    get_second_pass_min_silence_duration_ms_param,
    get_second_pass_scan_step_ms_param,
    get_second_pass_vad_method_param,
    get_session_timeout_param,
    get_silence_retention_ratio_param,
    get_silero_min_silence_ms_param,
    get_silero_threshold_param,
    get_speech_pad_ms_param,
    get_speech_preservation_ms_param,
    get_temperature_param,
    get_use_whisper_vad_param,
    get_vad_method_param,
)
from .session_utils import (
    DEFAULT_INACTIVITY_TIMEOUT_S,
    DEFAULT_SESSION_TIMEOUT_S,
    MAX_ALLOWED_INACTIVITY_TIMEOUT_S,
    MAX_ALLOWED_SESSION_TIMEOUT_S,
    MAX_CHUNK_BYTES,
    MAX_CONSECUTIVE_ERRORS,
    MIN_CHUNK_BYTES,
    MONITOR_MODE_RECEIVE_TIMEOUT_S,
    build_session_config,
    build_timeout_info,
    clamp_timeout,
    cleanup_session,
)

logger = get_logger(__name__)
router = APIRouter()


@router.websocket("/audio/live_transcribe")
async def audio_live_transcribe(  # noqa: PLR0913, PLR0912, PLR0915
    websocket: WebSocket,
    model: str = get_model_param(),
    # Timeout configuration (monitor mode by default)
    session_timeout: int | None = get_session_timeout_param(),
    inactivity_timeout: int | None = get_inactivity_timeout_param(),
    language: str | None = get_language_param(),
    # Whisper transcription parameters (passthrough from Stargate - normalized types)
    beam_size: int | None = get_beam_size_param(),
    temperature: str | None = get_temperature_param(),
    condition_on_previous_text: bool | None = get_condition_on_previous_text_param(),
    vad_method: str | None = get_vad_method_param(),
    # Window size configuration
    min_window_duration: float | None = get_min_window_duration_param(),
    max_window_duration: float | None = get_max_window_duration_param(),
    # Silero VAD tuning
    silero_threshold: float | None = get_silero_threshold_param(),
    silero_min_silence_ms: int | None = get_silero_min_silence_ms_param(),
    # Energy VAD tuning
    energy_threshold: float | None = get_energy_threshold_param(),
    # General
    speech_pad_ms: int | None = get_speech_pad_ms_param(),
    # NEW: Additional VAD params for streaming optimization
    silence_retention_ratio: float | None = get_silence_retention_ratio_param(),
    speech_preservation_ms: int | None = get_speech_preservation_ms_param(),
    overlap_duration_ms: int | None = get_overlap_duration_ms_param(),
    use_whisper_vad: bool | None = get_use_whisper_vad_param(),
    # Overlap correction parameters
    overlap_correction_enabled: bool | None = get_overlap_correction_enabled_param(),
    overlap_hold_word_count: int | None = get_overlap_hold_word_count_param(),
    overlap_max_time_gap_ms: int | None = get_overlap_max_time_gap_ms_param(),
    overlap_min_prefix_ratio: float | None = get_overlap_min_prefix_ratio_param(),
    # Second-pass boundary search parameters
    second_pass_enabled: bool | None = get_second_pass_enabled_param(),
    second_pass_min_silence_duration_ms: int | None = (
        get_second_pass_min_silence_duration_ms_param()
    ),
    second_pass_scan_step_ms: int | None = get_second_pass_scan_step_ms_param(),
    second_pass_max_search_depth_ms: int | None = (
        get_second_pass_max_search_depth_ms_param()
    ),
    second_pass_leave_behind_ms: int | None = get_second_pass_leave_behind_ms_param(),
    second_pass_vad_method: str | None = get_second_pass_vad_method_param(),
    # Word probability filtering
    min_word_probability: float | None = get_min_word_probability_param(),
    # Boundary preservation (mutually exclusive)
    adaptive_preservation_enabled: bool
    | None = get_adaptive_preservation_enabled_param(),
    max_window_leave_behind_ms: int | None = get_max_window_leave_behind_ms_param(),
):
    """
    Real-time audio transcription via WebSocket streaming.

    Protocol:
    1. Client connects with model, optional language, and VAD method
    2. Gateway creates streaming session with Whisper worker
    3. Client sends raw PCM audio bytes (16-bit, 16kHz)
    4. Gateway streams transcription results back as JSON

    Audio Format:
    - Raw PCM, 16-bit signed integer, 16kHz sample rate
    - No headers, just raw samples
    - Chunk size: 100-2000ms (1600-32000 samples, max 64KB)

    VAD Fallback Chain:
    - silero (default): Best quality, requires CUDA GPU
    - webrtc: Good quality, CPU-only
    - energy: Always available, basic energy-based detection

    Response Format (JSON):
    {
        "type": "transcription",
        "text": "Complete utterance text",
        "start_time": 0.0,
        "end_time": 2.5,
        "duration": 2.5,
        "probability": 0.85,
        "language": "en",
        "words": [{"word": "...", "start": 0.0, "end": 0.5, "probability": 0.9}]
    }

    Error Responses:
    - {"type": "error", "code": "...", "message": "..."}

    Timeouts (Monitor Mode by Default):
    - session_timeout: Max total session duration
      (None = server default, 0 = unlimited)
    - inactivity_timeout: Max silence/no-data duration
      (None = server default, 0 = unlimited)
    - Server defaults: 0 (unlimited) for both = monitor mode (no disconnects)
    - Server caps: session max 24h, inactivity max 1h (prevents resource pinning)
    - Omit params to use server policy; set explicitly to override

    Examples:
    - Monitor mode (default): no params or session_timeout=0&inactivity_timeout=0
    - 4-hour max session: session_timeout=14400
    - 5-min idle cutoff: inactivity_timeout=300
    - Both limits: session_timeout=3600&inactivity_timeout=120

    Limits:
    - Max chunk size: 64KB (2 seconds at 16kHz 16-bit)
    - Max session timeout: 24 hours (server enforced)
    - Max inactivity timeout: 1 hour (server enforced)
    """
    await websocket.accept()

    # Get worker controller from app state
    app = websocket.app
    worker_controller = app.state.worker_controller

    session_id = None
    request_id = str(uuid.uuid4())[:8]
    consecutive_errors = 0
    loop = asyncio.get_running_loop()
    session_start = loop.time()

    # Apply server-side clamping to timeout values
    effective_session_timeout = clamp_timeout(
        session_timeout,
        DEFAULT_SESSION_TIMEOUT_S,
        MAX_ALLOWED_SESSION_TIMEOUT_S,
        "session_timeout",
    )
    effective_inactivity_timeout = clamp_timeout(
        inactivity_timeout,
        DEFAULT_INACTIVITY_TIMEOUT_S,
        MAX_ALLOWED_INACTIVITY_TIMEOUT_S,
        "inactivity_timeout",
    )

    # Build timeout info for logging and ready message
    timeout_log, timeout_limits, is_monitor_mode = build_timeout_info(
        effective_session_timeout, effective_inactivity_timeout
    )
    logger.info(f"[{request_id}] {timeout_log}")

    async def send_error(code: str, message: str, close: bool = False):
        """Send error message and optionally close."""
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json(
                    {"type": "error", "code": code, "message": message}
                )
                if close:
                    await websocket.close(code=1008)  # Policy violation
        except Exception:
            pass

    try:
        # Ensure Whisper model is loaded
        logger.info(f"[{request_id}] Loading model: {model}")
        if not await worker_controller.ensure_model_loaded(model):
            await send_error(
                "model_load_failed", f"Failed to load model: {model}", close=True
            )
            return

        # Build session config (passthrough - Stargate has already applied defaults)
        session_config = build_session_config(
            language=language,
            beam_size=beam_size,
            temperature=temperature,
            condition_on_previous_text=condition_on_previous_text,
            vad_method=vad_method,
            min_window_duration=min_window_duration,
            max_window_duration=max_window_duration,
            silero_threshold=silero_threshold,
            silero_min_silence_ms=silero_min_silence_ms,
            energy_threshold=energy_threshold,
            speech_pad_ms=speech_pad_ms,
            silence_retention_ratio=silence_retention_ratio,
            speech_preservation_ms=speech_preservation_ms,
            overlap_duration_ms=overlap_duration_ms,
            use_whisper_vad=use_whisper_vad,
            overlap_correction_enabled=overlap_correction_enabled,
            overlap_hold_word_count=overlap_hold_word_count,
            overlap_max_time_gap_ms=overlap_max_time_gap_ms,
            overlap_min_prefix_ratio=overlap_min_prefix_ratio,
            second_pass_enabled=second_pass_enabled,
            second_pass_min_silence_duration_ms=second_pass_min_silence_duration_ms,
            second_pass_scan_step_ms=second_pass_scan_step_ms,
            second_pass_max_search_depth_ms=second_pass_max_search_depth_ms,
            second_pass_leave_behind_ms=second_pass_leave_behind_ms,
            second_pass_vad_method=second_pass_vad_method,
            min_word_probability=min_word_probability,
            adaptive_preservation_enabled=adaptive_preservation_enabled,
            max_window_leave_behind_ms=max_window_leave_behind_ms,
        )

        logger.info(f"[{request_id}] Session config: {session_config}")

        try:
            session_id = await worker_controller.call_rpc(
                model_id=model,
                method="create_stream_session",
                params={"config": session_config},
            )
        except Exception as e:
            await send_error(
                "session_create_failed", f"Failed to create session: {e}", close=True
            )
            return

        logger.info(f"[{request_id}] Created session: {session_id}")

        # Send ready message with effective timeout values
        ready_message = {
            "type": "ready",
            "session_id": session_id,
            "model": model,
            "config": session_config,
            "limits": timeout_limits,
        }

        # Add monitor mode indicator
        if is_monitor_mode:
            ready_message["monitor_mode"] = True

        await websocket.send_json(ready_message)

        # Main streaming loop
        while True:
            # Check session duration limit (only if timeout > 0)
            if effective_session_timeout > 0:
                elapsed = loop.time() - session_start
                if elapsed > effective_session_timeout:
                    await send_error(
                        "session_timeout",
                        f"Max session duration ({effective_session_timeout}s) exceeded",
                        close=True,
                    )
                    break

            # Receive audio bytes with timeout (detect dead connections)
            try:
                if effective_inactivity_timeout > 0:
                    # Inactivity timeout enabled - disconnect on timeout
                    audio_bytes = await asyncio.wait_for(
                        websocket.receive_bytes(), timeout=effective_inactivity_timeout
                    )
                else:
                    # Monitor mode: use liveness check timeout (no disconnect)
                    # Prevents zombie connections, allows silent clients
                    try:
                        audio_bytes = await asyncio.wait_for(
                            websocket.receive_bytes(),
                            timeout=MONITOR_MODE_RECEIVE_TIMEOUT_S,
                        )
                    except TimeoutError:
                        # Not an error - just loop back to check session timeout
                        # and try receiving again. Dead connections will raise
                        # WebSocketDisconnect on the next receive attempt.
                        continue

            except TimeoutError:
                # Inactivity timeout (only when effective_inactivity_timeout > 0)
                logger.info(
                    f"[{request_id}] Inactivity timeout "
                    f"({effective_inactivity_timeout}s) - no audio"
                )
                await send_error(
                    "inactivity_timeout",
                    f"No audio for {effective_inactivity_timeout}s",
                    close=True,
                )
                break
            except WebSocketDisconnect:
                logger.info(f"[{request_id}] Client disconnected")
                break

            # === VALIDATE CHUNK ===
            if len(audio_bytes) > MAX_CHUNK_BYTES:
                consecutive_errors += 1
                await send_error(
                    "chunk_too_large",
                    f"Chunk size {len(audio_bytes)} exceeds limit {MAX_CHUNK_BYTES}",
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    await send_error(
                        "too_many_errors", "Too many consecutive errors", close=True
                    )
                    break
                continue

            if len(audio_bytes) < MIN_CHUNK_BYTES:
                consecutive_errors += 1
                await send_error(
                    "chunk_too_small",
                    f"Chunk size {len(audio_bytes)} below minimum {MIN_CHUNK_BYTES}",
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    await send_error(
                        "too_many_errors", "Too many consecutive errors", close=True
                    )
                    break
                continue

            # Validate chunk is valid PCM (even number of bytes for 16-bit audio)
            if len(audio_bytes) % 2 != 0:
                consecutive_errors += 1
                await send_error(
                    "invalid_audio_format",
                    "Audio bytes must be 16-bit PCM (even number of bytes)",
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    await send_error(
                        "too_many_errors", "Too many consecutive errors", close=True
                    )
                    break
                continue

            # Reset error counter on valid chunk
            consecutive_errors = 0

            # Forward to worker for processing
            try:
                results = await worker_controller.call_rpc(
                    model_id=model,
                    method="process_audio_chunk",
                    params={
                        "session_id": session_id,
                        "audio_bytes": base64.b64encode(audio_bytes).decode(),
                    },
                )
            except Exception as e:
                logger.warning(f"[{request_id}] RPC error: {e}")
                error_code, error_message, should_close = map_worker_error(e)
                await send_error(error_code, error_message, close=should_close)
                if should_close:
                    break
                continue

            # Stream results back to client
            for result in results:
                await websocket.send_json({"type": "transcription", **result})

    except WebSocketDisconnect:
        logger.info(f"[{request_id}] Client disconnected")
    except Exception as e:
        logger.error(f"[{request_id}] Streaming error: {e}")
        await send_error("streaming_error", str(e))
    finally:
        # Close session and emit any pending results BEFORE closing websocket
        if session_id:
            try:
                pending_results = await cleanup_session(
                    worker_controller, model, session_id, request_id
                )

                # Send pending results to client before closing
                if (
                    pending_results
                    and websocket.client_state == WebSocketState.CONNECTED
                ):
                    for result in pending_results:
                        try:
                            await websocket.send_json(
                                {"type": "transcription", **result}
                            )
                        except Exception as e:
                            logger.warning(
                                f"[{request_id}] Failed to send pending result: {e}"
                            )
                            break
            except Exception as e:
                logger.error(f"[{request_id}] Session cleanup error: {e}")

        # Close websocket after emitting pending results
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception:
            pass
