"""webtab events use the Event Service ingest envelope."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

from web_tab.events import emit_probe_completed


def test_webtab_event_reaches_ingest_sock(tmp_path: Path, monkeypatch) -> None:
    sock_path = tmp_path / "events.sock"
    monkeypatch.setenv("EVENTS_INGEST_SOCK", str(sock_path))
    received: list[dict] = []

    def _serve() -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)
        conn, _ = srv.accept()
        data = conn.recv(65536).decode()
        conn.close()
        srv.close()
        received.append(json.loads(data.strip()))

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    emit_probe_completed(house_id="21207", target_id="t1", tab_count=2)
    thread.join(timeout=2.0)
    assert received
    line = received[0]
    assert line["signal"] == "webtab.probe.completed"
    assert line["payload"]["house_id"] == "21207"
    assert "source" in line
