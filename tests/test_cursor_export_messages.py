"""Tests for scripts/cursor-export-messages.py dialogue-only default."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from continuity_tape.extract_jsonl import extract_turns_from_jsonl
from continuity_tape.messages import seal_messages_sha256

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "cursor-export-messages.py"
_SPECIMEN_ID = "78604724-464a-46a3-8c52-1d35db18d8c7"
def _specimen_jsonl_path() -> Path | None:
    rel = (
        Path(".cursor")
        / "projects"
        / "mnt-torus-projects-universal-llm-gateway"
        / "agent-transcripts"
        / _SPECIMEN_ID
        / f"{_SPECIMEN_ID}.jsonl"
    )
    for root in (Path.home(), Path("/home/io")):
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


_SPECIMEN_JSONL = _specimen_jsonl_path()

_MINI_LIAISON_HOP = (
    "Liaison IDE hop (attended register) tip_cp=17. LOAD skill.\n"
    "HOP: hop now.\n"
    "OPERATOR LOOP: close with a situation report to the operator. "
    "A finished watch with no next leg is not a situation report."
)


def _load_export_module():
    spec = importlib.util.spec_from_file_location(
        "cursor_export_messages_test",
        _SCRIPT,
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def export_mod():
    return _load_export_module()


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _basic_records() -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": "Hello world."}]},
        },
        {
            "role": "assistant",
            "message": {"content": [{"type": "text", "text": "Hi."}]},
        },
    ]


def _run(
    export_mod,
    monkeypatch: pytest.MonkeyPatch,
    jsonl: Path,
    transcript_id: str,
    argv: list[str],
) -> tuple[int, str, str]:
    monkeypatch.setattr(
        export_mod,
        "locate_cursor_transcript_jsonl",
        lambda tid: (jsonl, None) if tid == transcript_id else (None, "not_found"),
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    with mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
        code = export_mod.main([transcript_id, *argv])
    return code, stdout.getvalue(), stderr.getvalue()


def test_default_passes_strict_dialogue_true(
    export_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(jsonl, _basic_records())
    calls: list[bool] = []

    original = export_mod.extract_turns_from_jsonl

    def spy(path, **kwargs):
        calls.append(kwargs.get("strict_dialogue", False))
        return original(path, **kwargs)

    monkeypatch.setattr(export_mod, "extract_turns_from_jsonl", spy)
    code, _, _ = _run(export_mod, monkeypatch, jsonl, tid, ["--stdout"])
    assert code == 0
    assert calls == [True]


def test_raw_passes_strict_dialogue_false(
    export_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(jsonl, _basic_records())
    calls: list[bool] = []

    original = export_mod.extract_turns_from_jsonl

    fixed_at = "2020-01-01T00:00:00+00:00"

    def spy(path, **kwargs):
        calls.append(kwargs.get("strict_dialogue", False))
        kwargs["observed_at"] = fixed_at
        return original(path, **kwargs)

    monkeypatch.setattr(export_mod, "extract_turns_from_jsonl", spy)
    code, out, _ = _run(export_mod, monkeypatch, jsonl, tid, ["--raw", "--stdout"])
    assert code == 0
    assert calls == [False]
    golden_env = extract_turns_from_jsonl(
        jsonl,
        tools="none",
        transcript_id=tid,
        strict_dialogue=False,
        observed_at=fixed_at,
    )
    golden = {
        "transcript_id": tid,
        "jsonl_path": str(jsonl),
        "meta": golden_env.meta.model_dump(),
        "messages": golden_env.messages,
    }
    assert out == json.dumps(golden, ensure_ascii=False, indent=2) + "\n"


def test_default_elide_call_order(
    export_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(jsonl, _basic_records())
    order: list[str] = []

    real_exclude = export_mod.exclude_ide_catalog_turns
    real_elide = export_mod.elide_ide_catalog_blocks

    def track_exclude(msgs):
        order.append("exclude")
        return real_exclude(msgs)

    def track_elide(msgs):
        order.append("elide")
        return real_elide(msgs)

    monkeypatch.setattr(export_mod, "exclude_ide_catalog_turns", track_exclude)
    monkeypatch.setattr(export_mod, "elide_ide_catalog_blocks", track_elide)
    code, _, _ = _run(export_mod, monkeypatch, jsonl, tid, ["--stdout"])
    assert code == 0
    assert order == ["exclude", "elide"]


def test_excise_not_called_bootstrap_survives(
    export_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    hop = _MINI_LIAISON_HOP
    records = [
        {
            "role": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": f"{hop}\n<user_query>keep this ask</user_query>",
                    }
                ]
            },
        },
        {
            "role": "assistant",
            "message": {"content": [{"type": "text", "text": "ok"}]},
        },
    ]
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(jsonl, records)

    def boom(*_a, **_k):
        raise AssertionError("excise_turn_one_resume_bootstrap must not run")

    monkeypatch.setattr(
        "agent_bus_store.tape_catalog_elide.excise_turn_one_resume_bootstrap",
        boom,
    )
    code, out, _ = _run(export_mod, monkeypatch, jsonl, tid, ["--stdout"])
    assert code == 0
    payload = json.loads(out)
    user_turns = [m for m in payload["messages"] if m.get("role") == "user"]
    assert user_turns
    assert any("keep this ask" in str(m.get("content") or "") for m in user_turns)
    assert any(
        "[elided liaison_hop_bootstrap:" in str(m.get("content") or "")
        for m in user_turns
    )


def test_elides_manually_attached_skills_keeps_query(
    export_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    skill_body = "skill-" * 30
    text = (
        f"<manually_attached_skills>{skill_body}</manually_attached_skills>\n"
        "<user_query>short ask</user_query>"
    )
    records = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": text}]},
        },
    ]
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(jsonl, records)
    code, out, _ = _run(export_mod, monkeypatch, jsonl, tid, ["--stdout"])
    assert code == 0
    content = payload_user_content(json.loads(out))
    assert "[elided manually_attached_skills:" in content
    assert "short ask" in content
    assert skill_body not in content


def test_drops_catalog_only_keeps_substantive(
    export_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    catalog_only = (
        "<dynamic_tool_catalog>" + ("z" * 80) + "</dynamic_tool_catalog>"
    )
    substantive = "<user_query>real work</user_query>"
    records = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": catalog_only}]},
        },
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": substantive}]},
        },
        {
            "role": "assistant",
            "message": {"content": [{"type": "text", "text": "done"}]},
        },
    ]
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(jsonl, records)
    code, out, _ = _run(export_mod, monkeypatch, jsonl, tid, ["--stdout"])
    assert code == 0
    payload = json.loads(out)
    contents = [str(m.get("content") or "") for m in payload["messages"]]
    assert not any("dynamic_tool_catalog" in c and "real work" not in c for c in contents)
    assert any("real work" in c for c in contents)


def payload_user_content(payload: dict[str, Any]) -> str:
    return "\n".join(
        str(m.get("content") or "")
        for m in payload["messages"]
        if m.get("role") == "user"
    )


def test_reseal_matches_written_messages_default_and_raw(
    export_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = "11111111-1111-1111-1111-111111111111"
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(jsonl, _basic_records())
    monkeypatch.setattr(
        export_mod,
        "locate_cursor_transcript_jsonl",
        lambda _tid: (jsonl, None),
    )
    for flag in ([], ["--raw"]):
        out_file = tmp_path / f"out-{''.join(flag) or 'default'}.json"
        monkeypatch.setattr(
            export_mod, "default_out_path", lambda _tid, p=out_file: p
        )
        stderr = io.StringIO()
        with mock.patch.object(sys, "stderr", stderr):
            code = export_mod.main([tid, *flag, "-o", str(out_file)])
        assert code == 0
        payload = json.loads(out_file.read_text(encoding="utf-8"))
        sha = payload["meta"]["messages_sha256"]
        count = payload["meta"]["message_count"]
        msgs = payload["messages"]
        assert sha == seal_messages_sha256(msgs)
        assert count == len(msgs)
        err = stderr.getvalue()
        assert f"({count} messages)" in err


def test_messages_only_and_stdout_cleaned(
    export_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = "22222222-2222-2222-2222-222222222222"
    catalog = "<dynamic_tool_catalog>" + ("q" * 60) + "</dynamic_tool_catalog>"
    records = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": catalog}]},
        },
        {
            "role": "user",
            "message": {
                "content": [{"type": "text", "text": "<user_query>stay</user_query>"}]
            },
        },
    ]
    jsonl = tmp_path / "t.jsonl"
    _write_jsonl(jsonl, records)
    code, out, _ = _run(
        export_mod, monkeypatch, jsonl, tid, ["--stdout", "--messages-only"]
    )
    assert code == 0
    msgs = json.loads(out)
    assert isinstance(msgs, list)
    assert "meta" not in out
    assert len(msgs) == 1
    assert "stay" in msgs[0]["content"]

    code2, out2, _ = _run(export_mod, monkeypatch, jsonl, tid, ["--stdout"])
    assert code2 == 0
    wrapped = json.loads(out2)
    assert "meta" in wrapped
    assert len(wrapped["messages"]) == 1


def test_help_mentions_dialogue_only_and_raw() -> None:
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--help"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    help_text = (proc.stdout or "") + (proc.stderr or "")
    assert "dialogue-only" in help_text.lower() or "dialogue only" in help_text.lower()
    assert "--raw" in help_text


@pytest.mark.skipif(_SPECIMEN_JSONL is None, reason="specimen jsonl missing")
def test_specimen_export_under_32kb(
    export_mod, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = _SPECIMEN_ID
    assert _SPECIMEN_JSONL is not None
    out_path = tmp_path / "out.json"
    monkeypatch.setattr(
        export_mod,
        "locate_cursor_transcript_jsonl",
        lambda _tid: (_SPECIMEN_JSONL, None),
    )
    monkeypatch.setattr(export_mod, "default_out_path", lambda _tid: out_path)
    code = export_mod.main([tid])
    assert code == 0
    raw = out_path.read_bytes()
    payload = json.loads(raw.decode())
    turn5_users = [
        m
        for m in payload["messages"]
        if m.get("role") == "user" and int(m.get("turn_index") or 0) == 5
    ]
    assert turn5_users, "expected turn-5 user message in specimen export"
    c = str(turn5_users[0].get("content") or "")
    assert "[elided manually_attached_skills:" in c
    assert "hermes can do better" in c
    assert len(raw) < 32 * 1024, f"export size {len(raw)} bytes exceeds 32KiB budget"