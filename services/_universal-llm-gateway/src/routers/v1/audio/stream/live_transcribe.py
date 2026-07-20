"""WebSocket route handler for live audio streaming transcription."""

import asyncio
import uuid

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..params import (
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
from ..session_utils import (
    DEFAULT_INACTIVITY_TIMEOUT_S,
    DEFAULT_SESSION_TIMEOUT_S,
    MAX_ALLOWED_INACTIVITY_TIMEOUT_S,
    MAX_ALLOWED_SESSION_TIMEOUT_S,
    build_session_config,
    build_timeout_info,
    clamp_timeout,
    cleanup_session,
)
from .deps import logger, router
from .session_bootstrap import bootstrap_stream_session
from .streaming_loop import run_streaming_loop
from .websocket_errors import send_websocket_error


@router.websocket("/audio/live_transcribe")
async def audio_live_transcribe(  # noqa: PLR0913
    websocket: WebSocket,
    model: str = get_model_param(),
    session_timeout: int | None = get_session_timeout_param(),
    inactivity_timeout: int | None = get_inactivity_timeout_param(),
    language: str | None = get_language_param(),
    beam_size: int | None = get_beam_size_param(),
    temperature: str | None = get_temperature_param(),
    condition_on_previous_text: bool | None = get_condition_on_previous_text_param(),
    vad_method: str | None = get_vad_method_param(),
    min_window_duration: float | None = get_min_window_duration_param(),
    max_window_duration: float | None = get_max_window_duration_param(),
    silero_threshold: float | None = get_silero_threshold_param(),
    silero_min_silence_ms: int | None = get_silero_min_silence_ms_param(),
    energy_threshold: float | None = get_energy_threshold_param(),
    speech_pad_ms: int | None = get_speech_pad_ms_param(),
    silence_retention_ratio: float | None = get_silence_retention_ratio_param(),
    speech_preservation_ms: int | None = get_speech_preservation_ms_param(),
    overlap_duration_ms: int | None = get_overlap_duration_ms_param(),
    use_whisper_vad: bool | None = get_use_whisper_vad_param(),
    overlap_correction_enabled: bool | None = get_overlap_correction_enabled_param(),
    overlap_hold_word_count: int | None = get_overlap_hold_word_count_param(),
    overlap_max_time_gap_ms: int | None = get_overlap_max_time_gap_ms_param(),
    overlap_min_prefix_ratio: float | None = get_overlap_min_prefix_ratio_param(),
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
    min_word_probability: float | None = get_min_word_probability_param(),
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

    worker_controller = websocket.app.state.worker_controller
    session_id = None
    request_id = str(uuid.uuid4())[:8]
    loop = asyncio.get_running_loop()
    session_start = loop.time()

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

    timeout_log, timeout_limits, is_monitor_mode = build_timeout_info(
        effective_session_timeout, effective_inactivity_timeout
    )
    logger.info(f"[{request_id}] {timeout_log}")

    try:
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

        ready_message = {
            "type": "ready",
            "model": model,
            "config": session_config,
            "limits": timeout_limits,
        }
        if is_monitor_mode:
            ready_message["monitor_mode"] = True

        session_id = await bootstrap_stream_session(
            websocket=websocket,
            worker_controller=worker_controller,
            request_id=request_id,
            model=model,
            session_config=session_config,
            ready_message=ready_message,
        )
        if session_id is None:
            return

        await run_streaming_loop(
            websocket=websocket,
            worker_controller=worker_controller,
            request_id=request_id,
            model=model,
            session_id=session_id,
            loop=loop,
            session_start=session_start,
            effective_session_timeout=effective_session_timeout,
            effective_inactivity_timeout=effective_inactivity_timeout,
        )

    except WebSocketDisconnect:
        logger.info(f"[{request_id}] Client disconnected")
    except Exception as exc:
        logger.error(f"[{request_id}] Streaming error: {exc}")
        await send_websocket_error(websocket, "streaming_error", str(exc))
    finally:
        if session_id:
            try:
                pending_results = await cleanup_session(
                    worker_controller, model, session_id, request_id
                )

                if (
                    pending_results
                    and websocket.client_state == WebSocketState.CONNECTED
                ):
                    for result in pending_results:
                        try:
                            await websocket.send_json(
                                {"type": "transcription", **result}
                            )
                        except Exception as exc:
                            logger.warning(
                                f"[{request_id}] Failed to send pending result: {exc}"
                            )
                            break
            except Exception as exc:
                logger.error(f"[{request_id}] Session cleanup error: {exc}")

        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception:
            pass
