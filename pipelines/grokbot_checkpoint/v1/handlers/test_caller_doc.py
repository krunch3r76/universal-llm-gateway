"""Pin CALLER.md to handler option keys, refusal codes, and admit."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from .admit import run_admit

pytestmark = pytest.mark.offline

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "CALLER.md"
_YAML = _ROOT / "grokbot-checkpoint-v1.yaml"
_HANDLERS = Path(__file__).resolve().parent
_UUID = "11111111-1111-4111-8111-111111111111"


def _doc() -> str:
    assert _DOC.is_file(), "CALLER.md missing"
    return _DOC.read_text(encoding="utf-8")


def _fence(text: str, lang: str) -> str:
    match = re.search(rf"```{lang}\n(.*?)```", text, re.DOTALL)
    assert match, f"missing ```{lang} fence"
    return match.group(1)


def _handler_sources() -> str:
    parts = [
        path.read_text(encoding="utf-8")
        for path in sorted(_HANDLERS.glob("*.py"))
        if path.name != "test_caller_doc.py"
    ]
    return "\n".join(parts)


def test_caller_doc_stale_refusal_nulls_binding_field() -> None:
    text = _doc()
    assert "post.handoff_stale" in text
    assert "envelope payload is only `refused`" in text
    assert "that result field is null" in text


def test_caller_doc_exists_and_names_pipeline() -> None:
    text = _doc()
    assert "grokbot-checkpoint-v1" in text
    assert "pipeline_options" in text
    assert "dispatch_thread_id" in text


def test_caller_doc_option_keys_exist() -> None:
    keys = [
        line.strip()
        for line in _fence(_doc(), "option-keys").splitlines()
        if line.strip()
    ]
    assert keys, "option-keys fence empty"
    sources = _handler_sources()
    missing = [key for key in keys if f'"{key}"' not in sources]
    assert missing == []


def test_caller_doc_codes_exist() -> None:
    text = _doc()
    codes = sorted(
        set(re.findall(r"\b(?:admit|seal|source|card|post)\.[a-z0-9_]+", text))
    )
    assert codes, "doc names no refusal codes"
    sources = _handler_sources()
    missing = [code for code in codes if f'"{code}"' not in sources]
    assert missing == []


@pytest.mark.asyncio
async def test_caller_doc_example_admits() -> None:
    raw = _fence(_doc(), "seal-example")
    raw = raw.replace("<uuid>", _UUID)
    options = yaml.safe_load(raw)
    assert isinstance(options, dict)

    async def get(path, params=None):
        return {"turns": []}, 200

    ok = await run_admit(options, {}, get=get)
    assert ok.get("refused") is None
    refused = await run_admit(
        {k: v for k, v in options.items() if k != "agent_id"}, {}, get=get
    )
    assert (refused.get("refused") or {}).get("code") == "admit.agent_id"


def test_yaml_description_points_to_caller_doc() -> None:
    loaded = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    description = str(loaded.get("description") or "")
    assert "CALLER.md" in description
    assert "pipeline_options" in description
    assert "dispatch_thread_id" in description


def test_caller_doc_letter_guidance() -> None:
    text = _doc()
    assert "handoff_binding_source" in text
    assert "pipeline" in text
    assert "state should be one string" in text or "state` should be one string" in text
    assert "notes/system/checkpoints/grok/<agent>/<n>.raw.json" in text
    assert "harvest.tape_refused" in text
    assert "ReadTranscript" in text
    assert "expected_card_sha256" in text
    assert "card.legacy_name" in text
    assert "n=1" in text or "`n=1`" in text


def test_caller_doc_paths() -> None:
    text = _doc()
    assert "notes/system/seals/<sid>.messages.json" in text
    assert "notes/system/checkpoints/grok/<agent>/<n>.messages.json" in text
    assert "notes/system/checkpoints/grok/<agent>/<n>.raw.messages.json" in text
    assert "meta.provenance` `raw" in text
    assert "tape/pours/<surface>/<transcript_id>.messages.json" not in text
    assert "houseless: true" in text
    assert "notes/system/transcripts/" not in text


def test_caller_doc_raw_pour_capture_consistency() -> None:
    text = _doc()
    assert (
        "for `capture.kind` `jsonl`, `capture.sha256` must equal `meta.source_sha256`"
        in text
    )
    assert (
        "for `capture.kind` `grok_export`, `capture.sha256` must equal "
        "`meta.sources[0].pages_sha256` and `meta.sources[0].reassembled_jsonl_sha256` "
        "must equal `meta.source_sha256`"
        in text
    )
    assert "refuses `pour.capture_inconsistent`" in text
    assert "validated before any checkpoint write" in text
