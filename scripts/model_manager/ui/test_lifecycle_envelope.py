"""Unit tests for start lifecycle envelope mapping."""

from __future__ import annotations

from scripts.model_manager.ui.lifecycle_envelope import start_envelope


def test_start_envelope_remote_banner_timeout_failure():
    msg = "cdp-ask remote start failed.\nConnection timed out during banner exchange"
    result = start_envelope(msg)
    assert result == {"status": "error", "message": msg}


def test_start_envelope_remote_start_ok():
    msg = "cdp-ask remote start ok.\nservice listening on port 8765"
    result = start_envelope(msg)
    assert result == {"status": "ok", "message": msg}


def test_start_envelope_configuration_error():
    msg = "cdp-ask configuration error: PROJECT_ASK_URL unset."
    result = start_envelope(msg)
    assert result == {"status": "error", "message": msg}


def test_start_envelope_gpu_preflight_failed():
    msg = "Gateway GPU preflight failed.\nno CUDA device"
    result = start_envelope(msg)
    assert result == {"status": "error", "message": msg}
