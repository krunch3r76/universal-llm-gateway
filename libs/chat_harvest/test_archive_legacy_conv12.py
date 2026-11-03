"""Legacy conv12 archive alignment after full-id key migration (a:41830 G4 / N1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chat_harvest.archive import (
    ArchiveConflictError,
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

CHAT_A_ID = "cse_01AroaPbHXkUECGTWbMyLXqo"
CHAT_A_URL = f"https://claude.ai/cowork/{CHAT_A_ID}"
# Shares conv12(CHAT_A_ID) with a distinct conversation (N1 collision shape).
CHAT_B_ID = "cse_01aroapbhZZZZZZZZZZZZ"
CHAT_B_URL = f"https://claude.ai/cowork/{CHAT_B_ID}"

CSE_ID = "cse_01aroapcvAAAAextra"
CSE_URL = f"https://claude.ai/cowork/{CSE_ID}"


def _reordinal(turns: list[ChatTurn], *, start: int) -> list[ChatTurn]:
    return [
        ChatTurn(
            author=t.author,
            ordinal=start + i,
            text=t.text,
            source=t.source,
        )
        for i, t in enumerate(turns)
    ]


def _turns(start: int, end: int, *, prefix: str = "User") -> list[ChatTurn]:
    rows: list[ChatTurn] = []
    ordinal = 0
    for turn in range(start, end + 1):
        ordinal += 1
        rows.append(
            ChatTurn(
                author="user",
                ordinal=ordinal,
                text=f"{prefix} turn {turn} with enough text here.",
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


def _write_legacy_conv12(
    tmp_path: Path,
    *,
    conversation_id: str,
    url: str,
    turns: list[ChatTurn],
) -> Path:
    conv12_dest = _conv12_archive_dest("claude", conversation_id)
    conv12_dest.parent.mkdir(parents=True, exist_ok=True)
    envelope = build_harvest_envelope(
        site="claude",
        conversation_id=conversation_id,
        url=url,
        messages=turns_to_messages(turns),
        harvested_at="t0",
        streaming=False,
    )
    conv12_dest.write_text(envelope_json_text(envelope), encoding="utf-8")
    return conv12_dest


def test_legacy_conv12_ignored_for_colliding_chat_b(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Chat B must not align against chat A's legacy conv12 sidecar (N1)."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    assert conv12(CHAT_A_ID) == conv12(CHAT_B_ID)
    _write_legacy_conv12(
        tmp_path, conversation_id=CHAT_A_ID, url=CHAT_A_URL, turns=_turns(1, 3)
    )
    assert not archive_dest("claude", CHAT_B_ID).is_file()
    b_turns = _turns(1, 1, prefix="ChatB")
    _uri, _sha, alignment = archive_chat_transcript(
        "claude", CHAT_B_ID, CHAT_B_URL, b_turns, harvested_at="t1"
    )
    assert alignment == "first_write"
    assert archive_dest("claude", CHAT_B_ID).is_file()


def test_legacy_conv12_same_chat_divergent_raises_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _write_legacy_conv12(
        tmp_path, conversation_id=CHAT_A_ID, url=CHAT_A_URL, turns=_turns(1, 2)
    )
    divergent = _turns(1, 2)
    divergent[2] = ChatTurn(
        author="assistant",
        ordinal=4,
        text="Different assistant body with enough text here.",
        source="dom",
    )
    with pytest.raises(ArchiveConflictError):
        archive_chat_transcript(
            "claude", CHAT_A_ID, CHAT_A_URL, divergent, harvested_at="t1"
        )


def test_legacy_conv12_head_extension_matches_full_id_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same-chat head_extension via legacy conv12 matches full-id archive behavior."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    base_turns = _turns(1, 2)
    head_ext_turns = [
        ChatTurn(
            author="user",
            ordinal=1,
            text="New head user message with enough text here.",
            source="dom",
        ),
        ChatTurn(
            author="assistant",
            ordinal=2,
            text="New head assistant message with enough text here.",
            source="dom",
        ),
        *_reordinal(base_turns, start=3),
    ]
    _write_legacy_conv12(
        tmp_path, conversation_id=CHAT_A_ID, url=CHAT_A_URL, turns=base_turns
    )
    with pytest.raises(ArchiveRefusalError) as legacy_exc:
        archive_chat_transcript(
            "claude",
            CHAT_A_ID,
            CHAT_A_URL,
            head_ext_turns,
            harvested_at="t1",
        )
    full_path = archive_dest("claude", CHAT_A_ID)
    envelope = build_harvest_envelope(
        site="claude",
        conversation_id=CHAT_A_ID,
        url=CHAT_A_URL,
        messages=turns_to_messages(_turns(1, 2)),
        harvested_at="t0",
        streaming=False,
    )
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(envelope_json_text(envelope), encoding="utf-8")
    with pytest.raises(ArchiveRefusalError) as full_exc:
        archive_chat_transcript(
            "claude",
            CHAT_A_ID,
            CHAT_A_URL,
            head_ext_turns,
            harvested_at="t2",
        )
    assert legacy_exc.value.code == full_exc.value.code == "head_extension"


def test_legacy_conv12_archive_blocks_narrower_reharvest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    full_turns = _turns(1, 5)
    _write_legacy_conv12(
        tmp_path, conversation_id=CSE_ID, url=CSE_URL, turns=full_turns
    )
    assert not archive_dest("claude", CSE_ID).is_file()
    narrow = full_turns[:6]
    with pytest.raises(ArchiveRefusalError) as exc:
        archive_chat_transcript("claude", CSE_ID, CSE_URL, narrow, harvested_at="t1")
    assert exc.value.code == "narrower_capture"
    assert conv12(CSE_ID) in _conv12_archive_dest("claude", CSE_ID).name


def test_legacy_conv12_corrupt_bytes_ignored_for_colliding_chat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC7 (a): corrupt conv12 sidecar does not block a colliding chat (Shape B)."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    conv12_path = _conv12_archive_dest("claude", CHAT_A_ID)
    conv12_path.parent.mkdir(parents=True, exist_ok=True)
    conv12_path.write_bytes(b"\x00not json")
    before = conv12_path.read_bytes()
    b_turns = _turns(1, 1, prefix="ChatB")
    _uri, _sha, alignment = archive_chat_transcript(
        "claude", CHAT_B_ID, CHAT_B_URL, b_turns, harvested_at="t1"
    )
    assert alignment == "first_write"
    assert archive_dest("claude", CHAT_B_ID).is_file()
    assert conv12_path.read_bytes() == before


def test_legacy_conv12_invalid_schema_ignored_for_colliding_chat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC7 (b): valid JSON but wrong schema → first_write for colliding chat."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    conv12_path = _conv12_archive_dest("claude", CHAT_A_ID)
    conv12_path.parent.mkdir(parents=True, exist_ok=True)
    conv12_path.write_text("{}", encoding="utf-8")
    before = conv12_path.read_text(encoding="utf-8")
    _uri, _sha, alignment = archive_chat_transcript(
        "claude",
        CHAT_B_ID,
        CHAT_B_URL,
        _turns(1, 1, prefix="ChatB"),
        harvested_at="t1",
    )
    assert alignment == "first_write"
    assert archive_dest("claude", CHAT_B_ID).is_file()
    assert conv12_path.read_text(encoding="utf-8") == before


def test_legacy_conv12_corrupt_same_chat_first_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC7 (c): corrupt conv12 for same chat → first_write; legacy file unchanged."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    conv12_path = _conv12_archive_dest("claude", CHAT_A_ID)
    conv12_path.parent.mkdir(parents=True, exist_ok=True)
    conv12_path.write_bytes(b"\xffbad")
    before = conv12_path.read_bytes()
    _uri, _sha, alignment = archive_chat_transcript(
        "claude", CHAT_A_ID, CHAT_A_URL, _turns(1, 1), harvested_at="t1"
    )
    assert alignment == "first_write"
    assert archive_dest("claude", CHAT_A_ID).is_file()
    assert conv12_path.read_bytes() == before
