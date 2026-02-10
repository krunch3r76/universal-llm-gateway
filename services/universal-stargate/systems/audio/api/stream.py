"""WebSocket proxy endpoint for audio streaming transcription."""

import asyncio
from typing import cast
from urllib.parse import urlparse

import websockets
from fastapi import APIRouter, HTTPException, WebSocket
from model_id import ModelId
from starlette.websockets import WebSocketState
from universal_logging import get_logger
from websockets.legacy.client import WebSocketClientProtocol

from systems.proxy.dependencies import get_proxy

from ..profiles.manager import AudioProfileManager
from ..utils.websocket_connect import connect_websocket
from .config_resolver import resolve_audio_config
from .forwarding import forward_client_to_gateway, forward_gateway_to_client, send_error
from .parameters import (
    MAX_ALLOWED_INACTIVITY_TIMEOUT_S,
    MAX_ALLOWED_SESSION_TIMEOUT_S,
    clamp_timeout,
    get_energy_threshold_param,
    get_inactivity_timeout_param,
    get_language_param,
    get_min_word_probability_param,
    get_model_param,
    get_overlap_correction_enabled_param,
    get_session_timeout_param,
    get_silero_min_silence_ms_param,
    get_silero_threshold_param,
    get_speech_pad_ms_param,
    get_use_whisper_vad_param,
    get_vad_method_param,
    get_vad_profile_param,
    get_webrtc_aggressiveness_param,
    get_webrtc_voice_threshold_param,
    get_whisper_beam_size_param,
    get_whisper_condition_on_previous_text_param,
    get_whisper_profile_param,
    get_whisper_temperature_param,
)
from .url_builder import build_gateway_url

logger = get_logger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])


@router.on_event("startup")
async def preload_audio_profiles() -> None:
    """Preload audio profiles at startup to avoid first-request YAML I/O."""
    AudioProfileManager()


# Gateway connection settings
GATEWAY_CONNECT_TIMEOUT = 5.0  # seconds
GATEWAY_MAX_SIZE = 10 * 1024 * 1024  # 10MB max message size
GATEWAY_PING_INTERVAL = 20  # ping every 20s to detect dead connections


@router.websocket("/live_transcribe")
async def audio_live_transcribe_proxy(  # noqa: PLR0913
    websocket: WebSocket,
    model: str = get_model_param(),
    session_timeout: int | None = get_session_timeout_param(),
    inactivity_timeout: int | None = get_inactivity_timeout_param(),
    language: str | None = get_language_param(),
    whisper_profile: str | None = get_whisper_profile_param(),
    whisper_beam_size: int | None = get_whisper_beam_size_param(),
    whisper_temperature: str | None = get_whisper_temperature_param(),
    whisper_condition_on_previous_text: bool
    | None = get_whisper_condition_on_previous_text_param(),
    profile: str | None = get_vad_profile_param(),
    vad_method: str | None = get_vad_method_param(),
    silero_threshold: float | None = get_silero_threshold_param(),
    silero_min_silence_ms: int | None = get_silero_min_silence_ms_param(),
    webrtc_aggressiveness: int | None = get_webrtc_aggressiveness_param(),
    webrtc_voice_threshold: float | None = get_webrtc_voice_threshold_param(),
    energy_threshold: float | None = get_energy_threshold_param(),
    speech_pad_ms: int | None = get_speech_pad_ms_param(),
    use_whisper_vad: bool | None = get_use_whisper_vad_param(),
    overlap_correction_enabled: bool | None = get_overlap_correction_enabled_param(),
    min_word_probability: float | None = get_min_word_probability_param(),
) -> None:
    """
    Proxy WebSocket audio streaming to Gateway.

    Accepts client connection on Stargate (9999), proxies to Gateway (9998).
    Bidirectionally forwards all messages (binary audio + JSON transcriptions).

    Supports VAD profiles (sensitive/balanced/aggressive) and Whisper quality
    profiles (quality/balanced/fast) which can be overridden by individual parameters.

    Whisper Quality Profiles:

    Streaming-optimized (default, single-temp, low latency):
    - quality (default): Best streaming quality (beam=5, temp=0.0, Farsi)
    - balanced: Good quality, faster (beam=3, temp=0.0, English)
    - fast: Fastest (beam=1, temp=0.0, minimal latency)

    File-optimized (multi-temp, quality priority, higher latency):
    - quality-file: Best quality with retries (beam=5, multi-temp)
    - balanced-file: Good quality with retries (beam=3, multi-temp)

    Use file-optimized profiles for difficult/noisy streaming audio where
    quality is more important than latency.

    Timeout Configuration (Monitor Mode by Default):
    - session_timeout: Maximum total session duration (default: 0 = unlimited)
    - inactivity_timeout: Maximum silence/no-data duration (default: 0 = unlimited)
    - Both default to 0 = monitor mode (no disconnects)
    - Set explicitly to impose limits (e.g., session_timeout=3600 for 1 hour max)
    """
    await websocket.accept()

    # Resolve audio configuration from profiles and overrides
    audio_profiles = AudioProfileManager()
    config = resolve_audio_config(
        audio_profiles=audio_profiles,
        model=model,
        websocket_id=id(websocket),
        profile=profile,
        whisper_profile=whisper_profile,
        vad_method=vad_method,
        silero_threshold=silero_threshold,
        silero_min_silence_ms=silero_min_silence_ms,
        webrtc_aggressiveness=webrtc_aggressiveness,
        webrtc_voice_threshold=webrtc_voice_threshold,
        energy_threshold=energy_threshold,
        speech_pad_ms=speech_pad_ms,
        whisper_beam_size=whisper_beam_size,
        whisper_temperature=whisper_temperature,
        whisper_condition_on_previous_text=whisper_condition_on_previous_text,
        overlap_correction_enabled=overlap_correction_enabled,
        min_word_probability=min_word_probability,
    )

    config.log_config()
    session_id = config.session_id

    # Parse at API boundary
    parsed_model = ModelId.parse(model)

    # Ensure model is loaded
    proxy = get_proxy()
    if proxy.resource_aware_model_manager is None:
        logger.error(f"[{session_id}] ResourceAwareModelManager not initialized")
        await send_error(websocket, "service_unavailable", "Model manager not ready")
        await websocket.close(code=1011, reason="Service unavailable")
        return

    try:
        logger.info(f"[{session_id}] Ensuring model {parsed_model} is loaded...")
        gateway_instance = await proxy.resource_aware_model_manager.ensure_model_loaded(
            parsed_model  # ModelId object
        )
        gateway_name = gateway_instance.config.name
        logger.info(f"[{session_id}] Model {parsed_model} ready on {gateway_name}")
    except HTTPException as http_exc:
        # Handle structured HTTP errors from model loading
        error_detail = (
            http_exc.detail.get("error", {})
            if isinstance(http_exc.detail, dict)
            else {}
        )
        error_code = error_detail.get("code", "model_load_failed")
        error_msg = error_detail.get("message", str(http_exc.detail))

        # Check if this is a definitive failure (OOM, resource, etc.)
        if error_code in ("model_oom", "model_resource_constraint"):
            logger.error(
                f"[{session_id}] Model loading DEFINITIVE FAILURE: "
                f"{error_code} - {error_msg}"
            )
            # Return clear error to client indicating server issue
            await send_error(
                websocket, error_code, f"Server resource issue: {error_msg}"
            )
            await websocket.close(
                code=1011, reason=f"Server resource error: {error_code}"
            )
        else:
            logger.error(f"[{session_id}] Model loading failed: {error_msg}")
            await send_error(websocket, error_code, error_msg)
            await websocket.close(code=1011, reason="Model loading failed")
        return
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[{session_id}] Model loading failed: {error_msg}")
        await send_error(websocket, "model_load_failed", error_msg)
        await websocket.close(code=1011, reason="Model loading failed")
        return

    # Extract gateway connection details
    # When using Unix sockets, base_url might be minimal (just for path construction)
    # so provide sensible defaults for host/port (they won't be used anyway)
    if gateway_instance.config.socket_path:
        # Unix socket mode - host/port not used but needed for URL construction
        gateway_host = "localhost"
        gateway_port = 9998
    else:
        # TCP mode - extract from base_url
        parsed_url = urlparse(gateway_instance.config.base_url)
        gateway_host = parsed_url.hostname or "localhost"
        gateway_port = parsed_url.port or 9998

    # Clamp timeout values
    clamped_session_timeout = clamp_timeout(
        session_timeout, MAX_ALLOWED_SESSION_TIMEOUT_S, "session_timeout"
    )
    clamped_inactivity_timeout = clamp_timeout(
        inactivity_timeout, MAX_ALLOWED_INACTIVITY_TIMEOUT_S, "inactivity_timeout"
    )

    # Extract Whisper parameters
    beam_size, temperature, context = config.extract_whisper_params()

    # Build Gateway WebSocket URL
    gateway_url = build_gateway_url(
        host=gateway_host,
        port=gateway_port,
        model=model,
        language=language,
        session_timeout=clamped_session_timeout,
        inactivity_timeout=clamped_inactivity_timeout,
        beam_size=beam_size,
        temperature=temperature,
        condition_on_previous_text=context,
        vad_method=config.effective_vad_params.get("vad_method"),
        min_window_duration=config.effective_vad_params.get("min_window_duration"),
        max_window_duration=config.effective_vad_params.get("max_window_duration"),
        silero_threshold=config.effective_vad_params.get("silero_threshold"),
        silero_min_silence_ms=config.effective_vad_params.get("silero_min_silence_ms"),
        webrtc_aggressiveness=config.effective_vad_params.get("webrtc_aggressiveness"),
        webrtc_voice_threshold=config.effective_vad_params.get(
            "webrtc_voice_threshold"
        ),
        energy_threshold=config.effective_vad_params.get("energy_threshold"),
        speech_pad_ms=config.effective_vad_params.get("speech_pad_ms"),
        silence_retention_ratio=config.effective_vad_params.get(
            "silence_retention_ratio"
        ),
        speech_preservation_ms=config.effective_vad_params.get(
            "speech_preservation_ms"
        ),
        overlap_duration_ms=config.effective_vad_params.get("overlap_duration_ms"),
        use_whisper_vad=use_whisper_vad,
        # Overlap correction (minimal - safety net only)
        overlap_correction_enabled=config.overlap_cfg.get("enabled"),
        overlap_hold_word_count=config.overlap_cfg.get("hold_word_count"),
        overlap_max_time_gap_ms=config.overlap_cfg.get("max_time_gap_ms"),
        overlap_min_prefix_ratio=config.overlap_cfg.get("min_prefix_ratio"),
        # Second-pass boundary search
        second_pass_enabled=config.second_pass_cfg.enabled,
        second_pass_min_silence_duration_ms=config.second_pass_cfg.min_silence_duration_ms,
        second_pass_scan_step_ms=config.second_pass_cfg.scan_step_ms,
        second_pass_max_search_depth_ms=config.second_pass_cfg.max_search_depth_ms,
        second_pass_leave_behind_ms=config.second_pass_cfg.leave_behind_ms,
        second_pass_vad_method=config.second_pass_cfg.vad_method,
        # Word probability filtering
        min_word_probability=config.min_word_probability,
        # Boundary preservation (mutually exclusive)
        adaptive_preservation_enabled=config.boundary_cfg.get(
            "adaptive_preservation_enabled"
        ),
        max_window_leave_behind_ms=config.boundary_cfg.get(
            "max_window_leave_behind_ms"
        ),
        # Boundary defer strategy
        boundary_defer_enabled=config.boundary_cfg.get("defer_enabled"),
        boundary_defer_max_ms=config.boundary_cfg.get("defer_max_ms"),
    )

    # Get socket_path from gateway instance for Unix socket support
    socket_path = getattr(gateway_instance.config, "socket_path", None)

    if socket_path:
        logger.info(
            f"[{session_id}] Connecting to {gateway_url} via Unix socket: {socket_path}"
        )
    else:
        logger.info(f"[{session_id}] Connecting to {gateway_url} via TCP")

    await _proxy_websocket_session(websocket, gateway_url, session_id, socket_path)


async def _proxy_websocket_session(
    websocket: WebSocket,
    gateway_url: str,
    session_id: str,
    socket_path: str | None = None,
) -> None:
    """
    Establish gateway connection and proxy bidirectional traffic.

    Args:
        websocket: Client WebSocket connection
        gateway_url: Gateway WebSocket URL
        session_id: Session ID for logging
        socket_path: Unix socket path (if set, uses unix_connect)
    """
    try:
        # Connect to Gateway with Unix socket support
        async with connect_websocket(
            gateway_url,
            socket_path=socket_path,
            max_size=GATEWAY_MAX_SIZE,
            ping_interval=GATEWAY_PING_INTERVAL,
            close_timeout=2.0,
            connect_timeout=GATEWAY_CONNECT_TIMEOUT,
        ) as gateway_ws:
            gateway_ws = cast(WebSocketClientProtocol, gateway_ws)

            logger.info(f"[{session_id}] Connected to Gateway, starting proxy")

            # Readiness gate - client traffic dropped until Gateway signals ready
            gateway_ready = asyncio.Event()

            # Create bidirectional proxy tasks
            client_to_gateway = asyncio.create_task(
                forward_client_to_gateway(
                    websocket, gateway_ws, session_id, gateway_ready
                )  # type: ignore[arg-type]
            )
            gateway_to_client = asyncio.create_task(
                forward_gateway_to_client(
                    gateway_ws, websocket, session_id, gateway_ready
                )  # type: ignore[arg-type]
            )

            # Wait for either direction to complete/fail
            done, pending = await asyncio.wait(
                [client_to_gateway, gateway_to_client],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel pending task
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Check for exceptions and log
            for task in done:
                if task.exception():
                    logger.error(
                        f"[{session_id}] Proxy task failed: {task.exception()}",
                        exc_info=task.exception(),
                    )

    except TimeoutError:
        logger.error(f"[{session_id}] Gateway timeout after {GATEWAY_CONNECT_TIMEOUT}s")
        await send_error(websocket, "gateway_timeout", "Gateway connection timed out")
    except websockets.InvalidURI as e:
        logger.error(f"[{session_id}] Invalid Gateway URI: {e}")
        await send_error(websocket, "invalid_gateway_uri", str(e))
    except websockets.InvalidHandshake as e:
        logger.error(f"[{session_id}] Gateway handshake failed: {e}")
        await send_error(websocket, "gateway_handshake_failed", str(e))
    except ConnectionRefusedError:
        logger.error(f"[{session_id}] Gateway not available")
        await send_error(websocket, "gateway_unavailable", "Gateway is not running")
    except Exception as e:
        logger.error(f"[{session_id}] Proxy error: {e}", exc_info=True)
        await send_error(websocket, "proxy_error", str(e))
    finally:
        # Ensure client connection is closed (gateway_ws handled by context manager)
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close(code=1000, reason="Session ended")
            except Exception as e:
                logger.debug(f"[{session_id}] Error closing client WS: {e}")

        logger.info(f"[{session_id}] Audio stream proxy closed")
