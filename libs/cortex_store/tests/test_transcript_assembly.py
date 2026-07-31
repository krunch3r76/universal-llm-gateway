"""Unit tests for transcript assembly session-ID derivation (friction 13697)."""

from __future__ import annotations

import json
from pathlib import Path

from agent_seat.session_id import SESSION_ID_RE

from cortex_store.transcript_assembly import derive_session_id_from_jsonl_start


def test_derive_session_id_from_jsonl_start(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<timestamp>2026-04-08T23:11:00Z</timestamp>\n"
                                "Start the handoff arc."
                            ),
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sid = derive_session_id_from_jsonl_start(jsonl_path=jsonl, agent="cursor")
    assert sid.startswith("cursor-2026-04-08-231100-")
    assert SESSION_ID_RE.match(sid)
