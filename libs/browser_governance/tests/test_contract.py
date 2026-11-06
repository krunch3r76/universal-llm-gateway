"""Offline unit tests for browser_governance C0 contract."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest
from browser_governance import (
    SUPERVISION_BANNER_SCAN_JS,
    admission_preflight_mode,
    browser_admission_warn,
    browser_resource_census,
    browser_supervision_error,
    browser_supervision_visibility,
    build_supervision_envelope,
    census_enabled,
    classify_ui_banner_text,
    emit_browser_event,
    grok_keepalive_displays,
    sanitize_ui_banner_text,
    supervision_gate_mode,
    unscanned_supervision,
    warn_disk_free_gib,
    warn_mem_available_mib,
    warn_swap_free_mib,
)

pytestmark = pytest.mark.offline


class TestRuntimeEnvReaders:
    def test_supervision_gate_mode_toggle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
        assert supervision_gate_mode() == "off"
        monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
        assert supervision_gate_mode() == "observe"
        monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "enforce")
        assert supervision_gate_mode() == "observe"

    def test_census_and_preflight_toggle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "0")
        assert census_enabled() is False
        assert admission_preflight_mode() == "off"
        monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "1")
        assert census_enabled() is True
        monkeypatch.setenv("ULG_BROWSER_ADMISSION_PREFLIGHT", "off")
        assert admission_preflight_mode() == "off"
        monkeypatch.setenv("ULG_BROWSER_ADMISSION_PREFLIGHT", "warn")
        assert admission_preflight_mode() == "warn"

    def test_warn_threshold_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ULG_BROWSER_WARN_MEM_MIB", "8192")
        monkeypatch.setenv("ULG_BROWSER_WARN_SWAP_MIB", "1024")
        monkeypatch.setenv("ULG_BROWSER_WARN_DISK_GIB", "25.5")
        assert warn_mem_available_mib() == 8192.0
        assert warn_swap_free_mib() == 1024.0
        assert warn_disk_free_gib() == 25.5

    def test_grok_keepalive_displays(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ULG_GROK_KEEPALIVE_DISPLAYS", raising=False)
        assert grok_keepalive_displays([":9", ":10", "9"]) == ("9", "10")
        monkeypatch.setenv("ULG_GROK_KEEPALIVE_DISPLAYS", ":9")
        assert grok_keepalive_displays([":9", ":10"]) == ("9",)
        monkeypatch.setenv("ULG_GROK_KEEPALIVE_DISPLAYS", "off")
        assert grok_keepalive_displays([":9", ":10"]) == ()


class TestUnknownSafeSupervision:
    def test_unscanned_never_false_present(self) -> None:
        env = unscanned_supervision(surface="grok", reason="probe_skipped")
        assert env["scanned"] is False
        assert env["ui_error"]["present"] is None
        assert env["auth_state"] == "unknown"
        assert env["app_health"] is None

    def test_build_envelope_absent_ui_banner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
        env = build_supervision_envelope(surface="grok", ui_banner=None)
        assert env["scanned"] is False
        assert env["ui_error"]["present"] is None
        assert env["auth_state"] == "unknown"
        assert env["app_health"] is None

    def test_gate_off_forces_unscanned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
        env = build_supervision_envelope(
            surface="grok",
            ui_banner={
                "scanned": True,
                "present": True,
                "kind": "renderer_crashed",
                "summary": "Aw, Snap!",
            },
            auth_state="signed_in",
        )
        assert env["scanned"] is False
        assert env["ui_error"]["present"] is None
        assert env["reason"] == "gate_mode_off"

    def test_observed_when_ui_banner_scanned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
        env = build_supervision_envelope(
            surface="grok",
            ui_banner={
                "scanned": True,
                "present": True,
                "kind": "session_expired",
                "summary": "Session expired — sign in again",
            },
        )
        assert env["scanned"] is True
        assert env["ui_error"]["scanned"] is True
        assert env["ui_error"]["present"] is True
        assert env["ui_error"]["kind"] == "session_expired"


class TestBannerClassification:
    @pytest.mark.parametrize(
        ("text", "kind"),
        [
            ("The computer became unresponsive", "computer_unresponsive"),
            ("Aw, Snap!", "renderer_crashed"),
            ("Usage limit reached", "rate_or_usage_limit"),
            ("Session expired", "session_expired"),
        ],
    )
    def test_classify_known_kinds(self, text: str, kind: str) -> None:
        got_kind, summary = classify_ui_banner_text(text)
        assert got_kind == kind
        assert summary is not None
        assert len(summary) <= 160

    def test_benign_text_returns_none(self) -> None:
        assert classify_ui_banner_text("All systems normal") == (None, None)

    @pytest.mark.parametrize(
        "raw",
        [
            "before\x00after",
            "\x1b[31mred\x1b[0m",
            "beep\x07here",
            "del\x7fhere",
            "nel\x85here",
            "csi\x9bhere",
        ],
    )
    def test_sanitize_strips_c0_c1_controls(self, raw: str) -> None:
        out = sanitize_ui_banner_text(raw)
        assert out is not None
        for ch in ("\x00", "\x07", "\x7f", "\x85", "\x9b", "\x1b"):
            assert ch not in out

    def test_sanitize_redacts_before_truncation_at_boundary(self) -> None:
        prefix = "x" * 155
        raw = f"{prefix} user@boundary.example.com tail"
        out = sanitize_ui_banner_text(raw)
        assert out is not None
        assert len(out) <= 160
        assert "@" not in out
        assert "<redacted-email>" in out

    def test_sanitize_bearer_redacted_before_truncation(self) -> None:
        prefix = "y" * 150
        raw = f"{prefix} Bearer super-secret-token-value"
        out = sanitize_ui_banner_text(raw)
        assert out is not None
        assert len(out) <= 160
        assert "super-secret" not in out
        assert "Bearer <redacted>" in out

    def test_sanitize_redactions_and_length(self) -> None:
        raw = (
            "Contact user@example.com with Bearer secret-token-xyz "
            "at https://example.com/path?token=abc "
            "hash deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        )
        out = sanitize_ui_banner_text(raw)
        assert out is not None
        assert "<redacted-email>" in out
        assert "Bearer <redacted>" in out
        assert "?" not in out
        assert "<redacted-hex>" in out
        assert len(out) <= 160


class TestBannerScanJs:
    def test_js_excludes_message_testid_and_subtree_scan(self) -> None:
        assert '[data-testid*="message" i]' in SUPERVISION_BANNER_SCAN_JS
        assert "el.querySelector" in SUPERVISION_BANNER_SCAN_JS
        assert "text.slice(0, 512)" in SUPERVISION_BANNER_SCAN_JS

    def test_js_excludes_message_dom_selectors(self) -> None:
        for needle in (
            "[data-message-author-role]",
            ".sand-message-prose",
            ".markdown",
            ".prose",
            "article",
            "pre",
            "code",
            "[data-is-streaming]",
            "textarea",
        ):
            assert needle in SUPERVISION_BANNER_SCAN_JS
        assert "contenteditable" in SUPERVISION_BANNER_SCAN_JS


class TestObservationEvents:
    def test_event_factory_signals(self) -> None:
        census = browser_resource_census(
            host="jupiter",
            mem_available_mib=4096.0,
            swap_free_mib=1024.0,
            swap_total_mib=8192.0,
            disk_free_gib=100.0,
            inode_free_pct=90.0,
            total_pss_mib=512.0,
            total_rss_mib=600.0,
            cdp_ports_live=2,
            cdp_page_targets=5,
            profile_count=3,
        )
        assert census.signal == "browser.resource.census"
        assert census.role == "observation"
        assert census.scope == "node"

        warn = browser_admission_warn(
            host="jupiter",
            caller="cdp_ask",
            warnings=["low_mem"],
            mem_available_mib=1000.0,
            swap_free_mib=500.0,
            disk_free_gib=10.0,
        )
        assert warn.signal == "browser.admission.warn"

        err = browser_supervision_error(
            surface="grok",
            display="9",
            cdp_port=9222,
            error_kind="renderer_crashed",
            summary="Aw, Snap!",
            visibility_state="visible",
        )
        assert err.signal == "browser.supervision.error"

        vis = browser_supervision_visibility(
            surface="grok",
            display="9",
            cdp_port=9222,
            visibility_state="visible",
            auth_state="unknown",
        )
        assert vis.signal == "browser.supervision.visibility"

    def test_emit_skipped_under_pytest_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ULG_EVENTS_ENABLE", raising=False)
        event = browser_resource_census(
            host="h",
            mem_available_mib=None,
            swap_free_mib=None,
            swap_total_mib=None,
            disk_free_gib=None,
            inode_free_pct=None,
            total_pss_mib=None,
            total_rss_mib=None,
            cdp_ports_live=0,
            cdp_page_targets=0,
            profile_count=0,
        )
        assert emit_browser_event(event) is False

    def test_emit_to_unix_socket(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        sock_path = tmp_path / "events.sock"
        received: list[bytes] = []
        ready = threading.Event()
        stop = threading.Event()

        def _serve() -> None:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(sock_path))
            srv.listen(1)
            srv.settimeout(2.0)
            ready.set()
            try:
                while not stop.is_set():
                    try:
                        conn, _ = srv.accept()
                    except TimeoutError:
                        continue
                    with conn:
                        data = conn.recv(65536)
                        if data:
                            received.append(data)
                            stop.set()
            finally:
                srv.close()

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()
        assert ready.wait(timeout=5.0)

        monkeypatch.setenv("ULG_EVENTS_ENABLE", "1")
        monkeypatch.setenv("EVENTS_INGEST_SOCK", str(sock_path))
        monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)

        event = browser_admission_warn(
            host="local",
            caller="test",
            warnings=[],
            mem_available_mib=1.0,
            swap_free_mib=1.0,
            disk_free_gib=1.0,
        )
        assert emit_browser_event(event) is True
        stop.wait(timeout=5.0)
        thread.join(timeout=3.0)

        assert received
        line = json.loads(received[0].decode().splitlines()[0])
        assert line["signal"] == "browser.admission.warn"
        assert line["source"] == "browser-governance"
