"""Legacy conv12 archive alignment after full-id key migration (a:41830 G4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chat_harvest.archive import (
    ArchiveRefusalError,
    _conv12_archive_dest,
    archive_chat_transcript,
    archive_dest,
    conv12,
    envelope_json_text,
)
from chat_harvest.messages import build_harvest_envelope, turns_to_messages
from chat_harvest.models import ChatTurn

pytestmark = pytest.mark.offline

CSE_ID = "cse_01aroapcvAAAAextra"
CSE_URL = f"https://claude.ai/cowork/{CSE_ID}"


def _turns(start: int, end: int) -> list[ChatTurn]:
    rows: list[ChatTurn] = []
    ordinal = 0
    for turn in range(start, end + 1):
        ordinal += 1
        rows.append(
            ChatTurn(
                author="user",
                ordinal=ordinal,
                text=f"User turn {turn} with enough text here.",
                source="dom",
            )
        )
        ordinal += 1
        rows.append(
            ChatTurn(
                author="assistant",
                ordinal=ordinal,
                text=f"Assistant turn {turn} with enough text here.",
                source="dom",
            )
        )
    return rows


def test_legacy_conv12_archive_blocks_narrower_reharvest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    conv12_dest = _conv12_archive_dest("claude", CSE_ID)
    conv12_dest.parent.mkdir(parents=True, exist_ok=True)
    full_turns = _turns(1, 5)
    envelope = build_harvest_envelope(
        site="claude",
        conversation_id=CSE_ID,
        url=CSE_URL,
        messages=turns_to_messages(full_turns),
        harvested_at="t0",
        streaming=False,
    )
    conv12_dest.write_text(envelope_json_text(envelope), encoding="utf-8")
    assert not archive_dest("claude", CSE_ID).is_file()

    narrow = full_turns[:6]
    with pytest.raises(ArchiveRefusalError) as exc:
        archive_chat_transcript("claude", CSE_ID, CSE_URL, narrow, harvested_at="t1")
    assert exc.value.code == "narrower_capture"
    assert conv12(CSE_ID) in conv12_dest.name
