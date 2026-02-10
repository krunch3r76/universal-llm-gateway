"""OpenAI-compatible file transcription endpoint for Whisper ASR."""

import asyncio
import os
import tempfile
import uuid
from typing import Any, Literal

import aiofiles
import aiofiles.os
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from universal_logging import get_logger

from src.routers.dependencies import get_worker_controller

logger = get_logger(__name__)
router = APIRouter()

# === SUPPORTED FORMATS ===
SUPPORTED_EXTENSIONS = {
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".oga",
    ".ogg",
    ".wav",
    ".webm",
}

# === OPTIONAL LIMITS (disabled by default) ===
MAX_FILE_SIZE: int | None = None  # Set to 25 * 1024 * 1024 for 25MB limit
MAX_AUDIO_DURATION_S: float | None = None  # Set to 30 * 60 for 30 min limit
PROCESSING_TIMEOUT_S: float | None = None  # Set to 10 * 60 for 10 min timeout


def _validate_audio_sync(temp_path: str) -> tuple[float, int, int]:
    """
    Synchronous audio validation (runs in executor).

    Returns:
        Tuple of (duration, samplerate, channels)

    Raises:
        Exception if decode fails
    """
    import soundfile as sf

    info = sf.info(temp_path)
    return info.duration, info.samplerate, info.channels


async def _validate_audio_file(temp_path: str, request_id: str) -> float:
    """
    Validate audio file can be decoded (non-blocking).

    Runs soundfile.info() in executor to avoid blocking the event loop.

    Returns:
        Audio duration in seconds

    Raises:
        HTTPException: If file cannot be decoded or exceeds duration limit
    """
    loop = asyncio.get_running_loop()

    try:
        duration, samplerate, channels = await loop.run_in_executor(
            None, _validate_audio_sync, temp_path
        )

        logger.debug(
            f"[{request_id}] Audio validated: {duration:.1f}s, "
            f"{samplerate}Hz, {channels}ch"
        )

        if MAX_AUDIO_DURATION_S is not None and duration > MAX_AUDIO_DURATION_S:
            raise HTTPException(
                status_code=400,
                detail=f"Audio too long: {duration:.0f}s exceeds "
                f"{MAX_AUDIO_DURATION_S}s limit",
            )

        return duration

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[{request_id}] Audio decode failed: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid audio file: could not decode. "
            f"Ensure ffmpeg/libsndfile1 installed for mp3/m4a/ogg/webm. "
            f"Error: {str(e)}",
        )


@router.post("/audio/transcriptions")
async def create_transcription(
    file: UploadFile = File(..., description="Audio file to transcribe"),
    model: str = Form(..., description="Model ID (e.g., whisper-large-v3)"),
    language: str | None = Form(None, description="Language code (e.g., en, fa)"),
    prompt: str | None = Form(None, description="Optional text to guide style"),
    response_format: Literal["json", "text", "verbose_json", "srt", "vtt"] = Form(
        "json", description="Response format"
    ),
    temperature: float = Form(0.0, ge=0.0, le=1.0, description="Sampling temperature"),
    worker_controller=Depends(get_worker_controller),
):
    """
    Transcribe audio file (OpenAI-compatible).

    Creates a transcription of the uploaded audio file using the specified
    Whisper model. Compatible with OpenAI's `/v1/audio/transcriptions` API.

    **Supported Formats:** flac, m4a, mp3, mp4, mpeg, mpga, oga, ogg, wav, webm
    """
    request_id = str(uuid.uuid4())[:8]

    # === VALIDATE FILE EXTENSION ===
    ext = os.path.splitext(file.filename or "")[1].lower() or ".wav"
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file extension: {ext}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    # === READ FILE CONTENT (async) ===
    file_content = await file.read()

    if MAX_FILE_SIZE is not None and len(file_content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )

    if len(file_content) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file")

    logger.info(
        f"[{request_id}] Transcription request: model={model}, "
        f"file={file.filename}, size={len(file_content) // 1024}KB, "
        f"format={response_format}"
    )

    temp_path = None
    try:
        # === SAVE TO TEMP FILE (async) ===
        fd, temp_path = tempfile.mkstemp(suffix=ext, prefix="whisper_")
        os.close(fd)  # Close sync fd, we'll use aiofiles

        async with aiofiles.open(temp_path, "wb") as f:
            await f.write(file_content)

        logger.debug(f"[{request_id}] Saved to temp file: {temp_path}")

        # === VALIDATE AUDIO (via executor) ===
        audio_duration = await _validate_audio_file(temp_path, request_id)

        # === ENSURE MODEL IS LOADED ===
        if not await worker_controller.ensure_model_loaded(model):
            raise HTTPException(
                status_code=503,
                detail=f"Failed to load model: {model}. Model may not exist in catalog.",
            )

        # === TRANSCRIBE VIA TYPED RPC ===
        result = await worker_controller.transcribe_file(
            model_id=model,
            audio_file_path=temp_path,
            language=language,
            prompt=prompt,
            temperature=temperature,
            word_timestamps=(response_format == "verbose_json"),
            timeout=PROCESSING_TIMEOUT_S,
        )

        logger.info(
            f"[{request_id}] Transcription complete: "
            f"{len(result.get('text', ''))} chars, "
            f"language={result.get('language')}"
        )

        return _format_response(result, response_format)

    except HTTPException:
        raise
    except TimeoutError:
        timeout_s = PROCESSING_TIMEOUT_S
        logger.error(f"[{request_id}] Processing timeout after {timeout_s}s")
        raise HTTPException(
            status_code=504,
            detail=f"Processing timeout after {PROCESSING_TIMEOUT_S}s. "
            f"Audio duration: {audio_duration:.0f}s",
        )
    except Exception as e:
        logger.error(f"[{request_id}] Transcription failed: {e}")
        error_msg = str(e).lower()
        if "decode" in error_msg or "audio" in error_msg or "format" in error_msg:
            raise HTTPException(
                status_code=400, detail=f"Audio processing error: {str(e)}"
            )
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")
    finally:
        if temp_path:
            try:
                await aiofiles.os.remove(temp_path)
                logger.debug(f"[{request_id}] Cleaned up temp file")
            except Exception as e:
                logger.warning(f"[{request_id}] Failed to cleanup temp file: {e}")


def _format_response(result: dict[str, Any], response_format: str):
    """Format transcription result based on requested format."""
    text = result.get("text", "") or ""
    segments = result.get("segments") or []
    language = result.get("language") or "unknown"
    duration = result.get("duration") or 0

    if response_format == "text":
        return PlainTextResponse(content=text)

    if response_format == "json":
        return {"text": text}

    if response_format == "verbose_json":
        formatted_segments = []
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            words = seg.get("words") or []
            if not isinstance(words, list):
                words = []
            formatted_segments.append(
                {
                    "id": seg.get("id", i),
                    "seek": int(float(seg.get("start", 0)) * 100),
                    "start": float(seg.get("start", 0)),
                    "end": float(seg.get("end", 0)),
                    "text": str(seg.get("text", "")).strip(),
                    "tokens": [],
                    "temperature": 0.0,
                    "avg_logprob": -0.5,
                    "compression_ratio": 1.0,
                    "no_speech_prob": 0.0,
                    "words": words,
                }
            )

        return {
            "task": "transcribe",
            "language": language,
            "duration": float(duration),
            "text": text,
            "segments": formatted_segments,
        }

    if response_format == "srt":
        return PlainTextResponse(
            content=_generate_srt(segments), media_type="text/plain"
        )

    if response_format == "vtt":
        return PlainTextResponse(content=_generate_vtt(segments), media_type="text/vtt")

    return {"text": text}


def _format_timestamp(seconds: float, use_comma: bool = True) -> str:
    """Format seconds to SRT/VTT timestamp."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    sep = "," if use_comma else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def _generate_srt(segments: list) -> str:
    """Generate SRT subtitle format."""
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _format_timestamp(seg.get("start", 0), use_comma=True)
        end = _format_timestamp(seg.get("end", 0), use_comma=True)
        text = seg.get("text", "").strip()
        lines.extend([str(i), f"{start} --> {end}", text, ""])
    return "\n".join(lines)


def _generate_vtt(segments: list) -> str:
    """Generate WebVTT subtitle format."""
    lines = ["WEBVTT", ""]
    for seg in segments:
        start = _format_timestamp(seg.get("start", 0), use_comma=False)
        end = _format_timestamp(seg.get("end", 0), use_comma=False)
        text = seg.get("text", "").strip()
        lines.extend([f"{start} --> {end}", text, ""])
    return "\n".join(lines)
