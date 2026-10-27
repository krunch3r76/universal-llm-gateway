"""harvest_seal mode: normalise, adapter, pour, and idempotent refusals."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from continuity_tape.extract_grokbot_jsonl import extract_grokbot_transcript
from continuity_tape.messages import envelope_wire_dict, seal_messages_sha256
from continuity_tape.pour_contract import validate_pour
from systems.pipeline.core.conditions import evaluate_condition, extract_condition_deps

from ._clients import file_sha256
from ._harvest import checkpoint_raw_messages_uri, run_adapter_twice
from .admit import run_admit
from .conftest import MemFs
from .harvest_adapter import run_harvest_adapter
from .harvest_normalise import run_harvest_normalise
from .harvest_pour import run_harvest_pour
from .result import assemble

pytestmark = pytest.mark.offline

_AGENT = "68757fe5-d68e-43f6-8708-6abd160bf205"
_FIXTURES = Path(__file__).parent / "fixtures"
_YAML = Path(__file__).resolve().parents[1] / "grokbot-checkpoint-v1.yaml"
_SPECIMEN = Path(
    "/mnt/torus/mcp-data/files/notes/system/tape/captures/grok/"
    "85dcee4a-0b20-4a86-95ae-01a529464072/20261017T203227.pages.txt"
)


def _l0_paths(fs: MemFs, *, ts: str = "20261017T2101Z") -> tuple[str, str]:
    raw = (_FIXTURES / "bl1_drift.pages.txt").read_bytes()
    l0 = f"notes/system/tape/captures/grok/{_AGENT}/{ts}.pages.txt"
    fs.files[l0] = raw
    return l0, file_sha256(raw)


@pytest.mark.asyncio
async def test_harvest_chain_produces_full_coverage(fs: MemFs) -> None:
    l0_ref, l0_sha = _l0_paths(fs)

    async def get(path, params=None):
        return {"turns": []}, 200

    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": l0_ref,
        "l0_sha256": l0_sha,
        "bus_identity": "grok-bot",
        "fallback": "electron",
    }
    admitted = await run_admit(options, {}, fs=fs, get=get)
    assert admitted.get("refused") is None
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None
    adapter = await run_harvest_adapter(options, norm, fs)
    assert adapter.get("refused") is None
    assert adapter["stats"]["messages_sha256"]
    assert adapter["stats"]["coverage"] == "full"
    pour = await run_harvest_pour(options, adapter, fs)
    assert pour.get("refused") is None
    assert pour["coverage"] == "full"
    assert pour["provenance"] == "transcribed"
    assert pour["ref"].endswith("/1.messages.json")
    assert "tape/" not in pour["ref"]
    msg_key = f"notes/system/checkpoints/grok/{_AGENT}/1.messages.json"
    raw_msg_key = checkpoint_raw_messages_uri(_AGENT, 1)
    assert msg_key in fs.files
    assert raw_msg_key in fs.files
    written = json.loads(fs.files[msg_key].decode())
    assert written["meta"]["provenance"] == "transcribed"
    raw_written = json.loads(fs.files[raw_msg_key].decode())
    assert raw_written["meta"]["provenance"] == "raw"
    norm_text = fs.files[str(norm["normalized_ref"])].decode()
    expected_raw = envelope_wire_dict(
        extract_grokbot_transcript(
            norm_text,
            agent_id=_AGENT,
            capture_ref=str(norm["normalized_ref"]),
            capture_sha256=str(norm["normalized_sha256"]),
            capture_kind="dom_harvest",
        )
    )
    assert raw_written["messages"] == expected_raw["messages"]


@pytest.mark.asyncio
async def test_prewritten_raw_json_keeps_l0_bytes(fs: MemFs) -> None:
    """K43 falsifier: l0_ref is already <n>.raw.json; pour must not rewrite it."""
    raw = (_FIXTURES / "bl1_drift.pages.txt").read_bytes()
    l0 = f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"
    fs.files[l0] = raw
    l0_sha = file_sha256(raw)
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": l0,
        "l0_sha256": l0_sha,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None
    adapter = await run_harvest_adapter(options, norm, fs)
    assert adapter.get("refused") is None
    pour = await run_harvest_pour(options, adapter, fs)
    assert pour.get("refused") is None
    assert file_sha256(fs.files[l0]) == l0_sha


@pytest.mark.asyncio
async def test_harvest_idempotent_second_run(fs: MemFs) -> None:
    l0_ref, l0_sha = _l0_paths(fs)
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": l0_ref,
        "l0_sha256": l0_sha,
        "fallback": "electron",
    }
    norm1 = await run_harvest_normalise(options, fs)
    adapter1 = await run_harvest_adapter(options, norm1, fs)
    pour1 = await run_harvest_pour(options, adapter1, fs)
    writes_after_first = list(fs.writes)
    norm2 = await run_harvest_normalise(options, fs)
    adapter2 = await run_harvest_adapter(options, norm2, fs)
    pour2 = await run_harvest_pour(options, adapter2, fs)
    assert norm2.get("refused") is None
    assert adapter2["stats"]["messages_sha256"] == adapter1["stats"]["messages_sha256"]
    assert pour2["sha256"] == pour1["sha256"]
    assert not any("tape/" in w for w in fs.writes[len(writes_after_first) :])


@pytest.mark.asyncio
async def test_harvest_falsifier_refuses_before_seal(fs: MemFs) -> None:
    l0_ref, _ = _l0_paths(fs)
    fs.files[l0_ref] = b"not a transcript\n"
    options = {
        "mode": "harvest_seal",
        "house": "bad",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": l0_ref,
    }
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is not None
    result = assemble(
        options,
        {
            "admit": {"json": {"refused": None}},
            "harvest_normalise": {"json": norm},
        },
    )
    assert result["refused"] is not None
    assert result.get("harvest_record") is None


@pytest.mark.asyncio
async def test_harvest_pour_prefix_refuse(fs: MemFs) -> None:
    l0_ref, l0_sha = _l0_paths(fs)
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": l0_ref,
        "l0_sha256": l0_sha,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    first = await run_harvest_pour(options, adapter, fs)
    assert first.get("refused") is None
    tampered = json.loads(json.dumps(adapter["envelope"]))
    old_msgs = tampered["messages"]
    tampered["messages"] = [{"role": "user", "content": "prefix-break"}] + old_msgs
    assert len(tampered["messages"]) >= len(old_msgs)
    fake_msha = "b" * 64
    tampered.setdefault("meta", {})["messages_sha256"] = fake_msha
    assert fake_msha != adapter["stats"]["messages_sha256"]
    bad = await run_harvest_pour(
        options,
        {
            **adapter,
            "envelope": tampered,
            "stats": {**adapter["stats"], "messages_sha256": fake_msha},
        },
        fs,
    )
    assert (bad.get("refused") or {}).get("code") == "envelope.not_prefix"


@pytest.mark.skipif(not _SPECIMEN.is_file(), reason="gc1 specimen not on disk")
@pytest.mark.asyncio
async def test_specimen_messages_sha256(fs: MemFs) -> None:
    agent = "85dcee4a-0b20-4a86-95ae-01a529464072"
    raw = _SPECIMEN.read_bytes()
    l0_ref = f"notes/system/tape/captures/grok/{agent}/20261017T203227.pages.txt"
    fs.files[l0_ref] = raw
    options = {
        "mode": "harvest_seal",
        "house": "gc1",
        "n": 6,
        "agent_id": agent,
        "l0_ref": l0_ref,
        "l0_sha256": file_sha256(raw),
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    assert adapter.get("refused") is None
    assert adapter["stats"]["coverage"] == "full"
    assert adapter["envelope"]["meta"]["provenance"] == "transcribed"
    assert adapter["raw_envelope"]["meta"]["provenance"] == "raw"
    contents = [
        block.get("type")
        for msg in adapter["envelope"]["messages"]
        if isinstance(msg.get("content"), list)
        for block in msg["content"]
        if isinstance(block, dict)
    ]
    assert "tool_use" in contents
    assert "thinking" in contents or "tool_result" in contents


def test_yaml_harvest_steps_registered() -> None:
    spec = yaml.safe_load(_YAML.read_text())
    names = [step["name"] for step in spec["steps"]]
    assert "harvest_normalise" in names
    assert "harvest_adapter" in names
    assert "harvest_pour" in names


def test_harvest_seal_envelope_condition() -> None:
    spec = yaml.safe_load(_YAML.read_text())
    envelope = next(s for s in spec["steps"] if s["name"] == "envelope")
    assert "harvest_seal" in envelope["condition"]
    assert "harvest_pour" in envelope["condition"]


def test_verify_resume_condition_depends_on_post() -> None:
    spec = yaml.safe_load(_YAML.read_text())
    verify_resume = next(s for s in spec["steps"] if s["name"] == "verify_resume")
    deps = extract_condition_deps(verify_resume["condition"])
    assert "post" in deps


def test_harvest_adapter_refusal_skips_downstream() -> None:
    spec = yaml.safe_load(_YAML.read_text())
    pour_step = next(s for s in spec["steps"] if s["name"] == "harvest_pour")
    envelope = next(s for s in spec["steps"] if s["name"] == "envelope")
    options = {"mode": "harvest_seal"}
    refused_adapter = {
        "harvest_normalise": SimpleNamespace(json={"refused": None, "_skipped": False}),
        "harvest_adapter": SimpleNamespace(
            json={"refused": {"code": "pour.schema"}, "_skipped": False}
        ),
    }
    assert not evaluate_condition(pour_step["condition"], refused_adapter, options)
    skipped_pour = {
        **refused_adapter,
        "harvest_pour": SimpleNamespace(json={"_skipped": True}),
    }
    assert not evaluate_condition(envelope["condition"], skipped_pour, options)


@pytest.mark.asyncio
async def test_run_adapter_twice_rejects_mismatched_capture_sha256(fs: MemFs) -> None:
    l0_ref, _ = _l0_paths(fs)
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": l0_ref,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None
    raw = fs.files[str(norm["normalized_ref"])]
    text = raw.decode("utf-8")
    _, err = run_adapter_twice(
        text,
        agent_id=_AGENT,
        capture_ref=str(norm["normalized_ref"]),
        capture_sha256="a" * 64,
        bus_identity=None,
    )
    assert err is not None
    assert err["refused"]["code"] == "source.sha_mismatch"


@pytest.mark.asyncio
async def test_harvest_record_line(fs: MemFs) -> None:
    l0_ref, l0_sha = _l0_paths(fs)
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": l0_ref,
        "l0_sha256": l0_sha,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    pour = await run_harvest_pour(options, adapter, fs)
    result = assemble(
        options,
        {
            "admit": {"json": {"refused": None}},
            "harvest_normalise": {"json": norm},
            "harvest_adapter": {"json": adapter},
            "harvest_pour": {"json": pour},
        },
    )
    line = result["harvest_record"]
    assert line.startswith(f"raw {norm['raw_ref']}@{l0_sha}")
    assert "tape/pours" not in line
    assert "coverage=full" in line
    cap_sha = pour["envelope"]["meta"]["capture"]["sha256"]
    cap_tok = f"capture:dom_harvest@{cap_sha[:12]}~pseudo"
    assert cap_tok in line
    assert cap_sha[:12] == norm["normalized_sha256"][:12]
    assert cap_sha[:12] != l0_sha[:12]


def _page(lo: int, hi: int, total: int, texts: list[str], *, start: bool) -> bytes:
    lines = [
        f"Transcript of this conversation, positions {lo}\u2013{hi} of {total}:",
        *[
            '{"role":"user","message":{"content":[{"type":"text","text":"'
            + text
            + '"}]}}'
            for text in texts
        ],
    ]
    if start:
        lines.append("This is the start of the transcript.")
    return ("\n".join(lines) + "\n").encode()


def _put_page(fs: MemFs, n: int, lo: int, hi: int, blob: bytes) -> str:
    folder = f"notes/system/checkpoints/grok/{_AGENT}/{n}.pages"
    fs.dirs.add(folder)
    name = f"{folder}/p{lo:06d}-{hi:06d}.txt"
    fs.files[name] = blob
    return folder


@pytest.mark.asyncio
async def test_pages_folder_and_prefix(fs: MemFs) -> None:
    first = _page(0, 0, 2, ["a"], start=True)
    second = _page(1, 1, 2, ["b"], start=False)
    folder = _put_page(fs, 1, 0, 0, first)
    _put_page(fs, 1, 1, 1, second)
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None, norm
    adapter = await run_harvest_adapter(options, norm, fs)
    assert adapter.get("refused") is None, adapter
    pour = await run_harvest_pour(options, adapter, fs, norm)
    assert pour.get("refused") is None, pour
    raw_key = f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"
    assert fs.files[raw_key].startswith(first)
    assert second in fs.files[raw_key]
    third = _page(2, 2, 3, ["c"], start=False)
    folder2 = _put_page(fs, 2, 2, 2, third)
    options2 = {**options, "n": 2, "l0_ref": folder2, "l0_sha256": ""}
    norm2 = await run_harvest_normalise(options2, fs)
    assert norm2.get("refused") is None, norm2
    adapter2 = await run_harvest_adapter(options2, norm2, fs)
    assert adapter2.get("refused") is None, adapter2
    pour2 = await run_harvest_pour(options2, adapter2, fs, norm2)
    assert pour2.get("refused") is None, pour2
    raw2 = fs.files[f"notes/system/checkpoints/grok/{_AGENT}/2.raw.json"]
    assert raw2.startswith(fs.files[raw_key])
    assert (
        pour2["envelope"]["messages"][: len(pour["envelope"]["messages"])]
        == pour["envelope"]["messages"]
    )
    fourth = _page(3, 3, 4, ["d"], start=False)
    folder3 = _put_page(fs, 3, 3, 3, fourth)
    options3 = {**options, "n": 3, "l0_ref": folder3}
    norm3 = await run_harvest_normalise(options3, fs)
    assert norm3.get("refused") is None, norm3
    adapter3 = await run_harvest_adapter(options3, norm3, fs)
    assert adapter3.get("refused") is None, adapter3
    pour3 = await run_harvest_pour(options3, adapter3, fs, norm3)
    assert pour3.get("refused") is None, pour3
    raw3 = fs.files[f"notes/system/checkpoints/grok/{_AGENT}/3.raw.json"]
    assert raw3.startswith(raw2)


@pytest.mark.asyncio
async def test_pages_gap(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 5, ["a"], start=True))
    _put_page(fs, 1, 3, 4, _page(3, 4, 5, ["c", "d"], start=False))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert (norm.get("refused") or {}).get("code") == "harvest.page_gap"


@pytest.mark.asyncio
async def test_pages_total_and_overlap(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 2, ["a"], start=True))
    _put_page(fs, 1, 1, 1, _page(1, 1, 3, ["b"], start=False))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    mixed = await run_harvest_normalise(options, fs)
    assert (mixed.get("refused") or {}).get("code") == "harvest.page_total"
    fs.files.pop(f"{folder}/p000001-000001.txt")
    _put_page(fs, 1, 0, 1, _page(0, 1, 2, ["nope", "b"], start=False))
    # two files both claim position 0 with different lines
    overlap = await run_harvest_normalise(options, fs)
    assert (overlap.get("refused") or {}).get("code") == "harvest.page_overlap"


@pytest.mark.asyncio
async def test_harvest_raw_conflict(fs: MemFs) -> None:
    l0_ref, l0_sha = _l0_paths(fs)
    raw_key = f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"
    fs.files[raw_key] = b"different"
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": l0_ref,
        "l0_sha256": l0_sha,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    pour = await run_harvest_pour(options, adapter, fs, norm)
    assert (pour.get("refused") or {}).get("code") == "harvest.raw_conflict"
    assert f"notes/system/checkpoints/grok/{_AGENT}/1.messages.json" not in fs.files


@pytest.mark.asyncio
async def test_workspaces_prefix_does_not_satisfy_cortex_raw(fs: MemFs) -> None:
    raw = (_FIXTURES / "bl1_drift.pages.txt").read_bytes()
    l0 = f"workspaces://notes/system/checkpoints/grok/{_AGENT}/1.raw.json"
    fs.files[l0.removeprefix("workspaces://")] = raw
    # MemFs.read does not strip; normalise uses fs.read(l0_ref) for files.
    fs.files[l0] = raw
    cortex = f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"
    fs.files[cortex] = b"other-bytes"
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": l0,
        "l0_sha256": file_sha256(raw),
    }
    norm = await run_harvest_normalise(options, fs)
    assert (norm.get("refused") or {}).get("code") == "harvest.workspace_refused"


@pytest.mark.asyncio
async def test_incremental_missing_prior(fs: MemFs) -> None:
    folder = _put_page(fs, 2, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 2,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert (norm.get("refused") or {}).get("code") == "harvest.no_prior"


@pytest.mark.asyncio
async def test_wrapped_pages_extend(fs: MemFs) -> None:
    """n=2 must reassemble the normalized prior, not the wrapped raw."""
    fence = b'<cursor_untrusted_data_1337 source="ReadTranscript">\n'
    first = fence + _page(0, 0, 1, ["a"], start=True)
    folder = _put_page(fs, 1, 0, 0, first)
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None, norm
    adapter = await run_harvest_adapter(options, norm, fs)
    pour = await run_harvest_pour(options, adapter, fs, norm)
    assert pour.get("refused") is None, pour
    nxt = fence + _page(1, 1, 2, ["b"], start=False)
    folder2 = _put_page(fs, 2, 1, 1, nxt)
    options2 = {**options, "n": 2, "l0_ref": folder2}
    norm2 = await run_harvest_normalise(options2, fs)
    assert norm2.get("refused") is None, norm2
    adapter2 = await run_harvest_adapter(options2, norm2, fs)
    pour2 = await run_harvest_pour(options2, adapter2, fs, norm2)
    assert pour2.get("refused") is None, pour2


@pytest.mark.asyncio
async def test_houseless_admitted_n_reaches_normalise(fs: MemFs) -> None:
    """Omit n after checkpoint 1. Admit picks 2; normalise must use that n."""
    from types import SimpleNamespace

    from .harvest_normalise import GrokbotCheckpointHarvestNormaliseHandler

    folder1 = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options1 = {
        "mode": "harvest_seal",
        "houseless": True,
        "agent_id": _AGENT,
        "l0_ref": folder1,
        "n": 1,
        "fallback": "electron",
    }
    norm1 = await run_harvest_normalise(options1, fs)
    adapter1 = await run_harvest_adapter(options1, norm1, fs)
    await run_harvest_pour(options1, adapter1, fs, norm1)
    folder2 = _put_page(fs, 2, 1, 1, _page(1, 1, 2, ["b"], start=False))
    fresh = {
        "mode": "harvest_seal",
        "houseless": True,
        "agent_id": _AGENT,
        "l0_ref": folder2,
    }
    admitted = await run_admit(fresh, {}, fs=fs, get=None)
    assert admitted["n"] == 2

    # PipelineContext.options rebuilds; the handler must read admit.json.
    ctx = SimpleNamespace(
        options=fresh,
        outputs={"admit": SimpleNamespace(json=admitted)},
        fs=fs,
        dispatch_thread_id="",
    )
    out = await GrokbotCheckpointHarvestNormaliseHandler().execute(None, ctx)
    assert out.json.get("refused") is None, out.json
    assert out.json["raw_ref"].endswith("/2.raw.json")


@pytest.mark.asyncio
async def test_houseless_skips_bus(fs: MemFs) -> None:
    called = {"n": 0}

    async def get(path, params=None):
        called["n"] += 1
        return {"turns": []}, 200

    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options = {
        "mode": "harvest_seal",
        "houseless": True,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    admitted = await run_admit(options, {}, fs=fs, get=get)
    assert admitted.get("refused") is None, admitted
    assert admitted["houseless"] is True
    assert admitted["n"] == 1
    assert called["n"] == 0
    missing = await run_admit(
        {"mode": "harvest_seal", "agent_id": _AGENT, "l0_ref": folder, "n": 1},
        {},
        fs=fs,
        get=get,
    )
    assert (missing.get("refused") or {}).get("code") == "admit.house"


def test_empty_harvest_messages_admitted() -> None:
    from src.schemas.chat_completion import ChatCompletionRequest

    req = ChatCompletionRequest(
        model="grokbot-checkpoint-v1",
        messages=[],
        pipeline_options={
            "mode": "harvest_seal",
            "l0_ref": "notes/system/checkpoints/grok/a/1.pages",
        },
    )
    assert req.model == "grokbot-checkpoint-v1"
    with pytest.raises(ValueError):
        ChatCompletionRequest(model="other", messages=[])


@pytest.mark.asyncio
async def test_variable_n_and_reviewer_window(fs: MemFs) -> None:
    """bl1 shape: highest hi sets T; a later page may name a larger N."""
    folder = _put_page(fs, 1, 0, 1, _page(0, 1, 6, ["a", "b"], start=True))
    _put_page(fs, 1, 2, 3, _page(2, 3, 4, ["c", "d"], start=False))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None, norm
    assert "of 4:" in fs.files[norm["normalized_ref"]].decode()


@pytest.mark.asyncio
async def test_bl1_drift_target_is_highest_hi(fs: MemFs) -> None:
    """A live self-page grows N. T is the N of the page with the highest hi."""
    lines = [f"m{i}" for i in range(99)]
    folder = _put_page(fs, 1, 50, 98, _page(50, 98, 99, lines[50:99], start=False))
    _put_page(fs, 1, 14, 49, _page(14, 49, 104, lines[14:50], start=False))
    _put_page(fs, 1, 0, 13, _page(0, 13, 108, lines[0:14], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None, norm
    text = fs.files[norm["normalized_ref"]].decode()
    assert "of 99:" in text
    assert "of 104:" not in text
    assert "of 108:" not in text


@pytest.mark.asyncio
async def test_evil_prior_raw_does_not_enter_messages(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    pour = await run_harvest_pour(options, adapter, fs, norm)
    assert pour.get("refused") is None, pour
    fs.files[f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"] = b"EVIL\n"
    folder2 = _put_page(fs, 2, 1, 1, _page(1, 1, 2, ["b"], start=False))
    refused = await run_harvest_normalise({**options, "n": 2, "l0_ref": folder2}, fs)
    assert (refused.get("refused") or {}).get("code") == "harvest.no_prior"


@pytest.mark.asyncio
async def test_headerless_prior_raw_refuses(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    assert (await run_harvest_pour(options, adapter, fs, norm)).get("refused") is None
    normalized = fs.files[norm["normalized_ref"]].decode()
    bare = "\n".join(
        line for line in normalized.splitlines() if line.startswith('{"role"')
    )
    fs.files[f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"] = (
        bare + "\n"
    ).encode()
    nxt = _put_page(fs, 2, 1, 1, _page(1, 1, 2, ["b"], start=False))
    refused = await run_harvest_normalise({**options, "n": 2, "l0_ref": nxt}, fs)
    assert (refused.get("refused") or {}).get("code") == "harvest.no_prior"


@pytest.mark.asyncio
async def test_rewritten_header_target_refuses(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    assert (await run_harvest_pour(options, adapter, fs, norm)).get("refused") is None
    raw_key = f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"
    rewritten = (
        fs.files[raw_key]
        .decode()
        .replace("Transcript of this conversation", "Transcript of something else", 1)
    )
    fs.files[raw_key] = rewritten.encode()
    nxt = _put_page(fs, 2, 1, 1, _page(1, 1, 2, ["b"], start=False))
    refused = await run_harvest_normalise({**options, "n": 2, "l0_ref": nxt}, fs)
    assert (refused.get("refused") or {}).get("code") == "harvest.no_prior"


@pytest.mark.asyncio
async def test_no_new_pages_at_n2_and_n3(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    assert (await run_harvest_pour(options, adapter, fs, norm)).get("refused") is None
    replay = _put_page(fs, 2, 0, 0, _page(0, 0, 1, ["a"], start=True))
    refused = await run_harvest_normalise({**options, "n": 2, "l0_ref": replay}, fs)
    assert (refused.get("refused") or {}).get("code") == "harvest.no_new_pages"
    del fs.files[f"{replay}/p000000-000000.txt"]
    nxt = _put_page(fs, 2, 1, 1, _page(1, 1, 2, ["b"], start=False))
    options2 = {**options, "n": 2, "l0_ref": nxt}
    norm2 = await run_harvest_normalise(options2, fs)
    adapter2 = await run_harvest_adapter(options2, norm2, fs)
    assert (await run_harvest_pour(options2, adapter2, fs, norm2)).get(
        "refused"
    ) is None
    replay3 = _put_page(fs, 3, 1, 1, _page(1, 1, 2, ["b"], start=False))
    refused3 = await run_harvest_normalise({**options, "n": 3, "l0_ref": replay3}, fs)
    assert (refused3.get("refused") or {}).get("code") == "harvest.no_new_pages"


@pytest.mark.asyncio
async def test_not_prefix_and_window_mismatch(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    pour = await run_harvest_pour(options, adapter, fs, norm)
    assert pour.get("refused") is None
    hole = _put_page(fs, 2, 2, 2, _page(2, 2, 3, ["c"], start=False))
    gap = await run_harvest_normalise({**options, "n": 2, "l0_ref": hole}, fs)
    assert (gap.get("refused") or {}).get("code") == "harvest.not_prefix"
    msg = f"notes/system/checkpoints/grok/{_AGENT}/1.messages.json"
    payload = json.loads(fs.files[msg].decode())
    payload["meta"]["sources"][0]["window"]["hi"] = 10
    fs.files[msg] = json.dumps(payload).encode()
    nxt = _put_page(fs, 2, 1, 1, _page(1, 1, 2, ["b"], start=False))
    mismatch = await run_harvest_normalise({**options, "n": 2, "l0_ref": nxt}, fs)
    assert (mismatch.get("refused") or {}).get("code") == "harvest.no_prior"
    assert "window.hi" in (mismatch.get("refused") or {}).get("message", "")


@pytest.mark.asyncio
async def test_pour_refuses_edited_prior_message(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    assert (await run_harvest_pour(options, adapter, fs, norm)).get("refused") is None
    msg = f"notes/system/checkpoints/grok/{_AGENT}/1.messages.json"
    payload = json.loads(fs.files[msg].decode())
    content = payload["messages"][0]["content"]
    if isinstance(content, str):
        payload["messages"][0]["content"] = "Z" + content[1:]
    else:
        payload["messages"][0]["content"][0]["text"] = "Z"
    fs.files[msg] = json.dumps(payload).encode()
    nxt = _put_page(fs, 2, 1, 1, _page(1, 1, 2, ["b"], start=False))
    options2 = {**options, "n": 2, "l0_ref": nxt}
    norm2 = await run_harvest_normalise(options2, fs)
    assert norm2.get("refused") is None, norm2
    adapter2 = await run_harvest_adapter(options2, norm2, fs)
    pour2 = await run_harvest_pour(options2, adapter2, fs, norm2)
    assert (pour2.get("refused") or {}).get("code") == "harvest.not_prefix"


@pytest.mark.asyncio
async def test_missing_raw_prior_starts_raw_chain_at_n2(fs: MemFs) -> None:
    """Legacy checkpoints without .raw.messages.json still extend transcribed chain."""
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    assert (await run_harvest_pour(options, adapter, fs, norm)).get("refused") is None
    raw_key = checkpoint_raw_messages_uri(_AGENT, 1)
    del fs.files[raw_key]
    nxt = _put_page(fs, 2, 1, 1, _page(1, 1, 2, ["b"], start=False))
    options2 = {**options, "n": 2, "l0_ref": nxt}
    norm2 = await run_harvest_normalise(options2, fs)
    adapter2 = await run_harvest_adapter(options2, norm2, fs)
    pour2 = await run_harvest_pour(options2, adapter2, fs, norm2)
    assert pour2.get("refused") is None, pour2
    assert checkpoint_raw_messages_uri(_AGENT, 2) in fs.files


@pytest.mark.asyncio
async def test_raw_chain_not_prefix_leaves_both_priors(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    assert (await run_harvest_pour(options, adapter, fs, norm)).get("refused") is None
    msg1 = f"notes/system/checkpoints/grok/{_AGENT}/1.messages.json"
    raw1 = checkpoint_raw_messages_uri(_AGENT, 1)
    prior_msg = fs.files[msg1]
    prior_raw = fs.files[raw1]
    nxt = _put_page(fs, 2, 1, 1, _page(1, 1, 2, ["b"], start=False))
    options2 = {**options, "n": 2, "l0_ref": nxt}
    norm2 = await run_harvest_normalise(options2, fs)
    adapter2 = await run_harvest_adapter(options2, norm2, fs)
    broken = json.loads(json.dumps(adapter2))
    broken["raw_envelope"]["messages"] = [{"role": "user", "content": "break"}]
    pour2 = await run_harvest_pour(options2, broken, fs, norm2)
    assert (pour2.get("refused") or {}).get("code") == "harvest.not_prefix"
    assert fs.files[msg1] == prior_msg
    assert fs.files[raw1] == prior_raw
    assert checkpoint_raw_messages_uri(_AGENT, 2) not in fs.files


@pytest.mark.asyncio
async def test_grok_export_pages_default_pour_success_readback(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    opts = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
    }
    norm = await run_harvest_normalise(opts, fs)
    adapter = await run_harvest_adapter(opts, norm, fs)
    assert adapter.get("refused") is None, adapter
    cap = adapter["raw_envelope"]["meta"]["capture"]
    assert cap["kind"] == "grok_export"
    assert cap["sha256"] == norm["normalized_sha256"]
    assert cap["sha256"] != adapter["raw_envelope"]["meta"]["source_sha256"]
    src0 = adapter["raw_envelope"]["meta"]["sources"][0]
    assert cap["sha256"] == src0["pages_sha256"]
    assert (
        src0["reassembled_jsonl_sha256"]
        == adapter["raw_envelope"]["meta"]["source_sha256"]
    )
    pour = await run_harvest_pour(opts, adapter, fs, norm)
    assert pour.get("refused") is None, pour
    msg_key = f"notes/system/checkpoints/grok/{_AGENT}/1.messages.json"
    raw_msg_key = checkpoint_raw_messages_uri(_AGENT, 1)
    raw_json_key = f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"
    assert msg_key in fs.files
    assert raw_msg_key in fs.files
    assert raw_json_key in fs.files
    written = json.loads(fs.files[msg_key].decode())
    raw_written = json.loads(fs.files[raw_msg_key].decode())
    validate_pour(raw_written)
    validate_pour(written)
    assert written["meta"]["provenance"] == "transcribed"
    assert raw_written["meta"]["provenance"] == "raw"
    assert written["meta"]["messages_sha256"] == seal_messages_sha256(
        written["messages"]
    )
    assert raw_written["meta"]["messages_sha256"] == seal_messages_sha256(
        raw_written["messages"]
    )
    assert raw_written["meta"]["capture"]["sha256"] == src0["pages_sha256"]


@pytest.mark.asyncio
async def test_grok_export_capture_mismatch_writes_no_checkpoint_artifacts(
    fs: MemFs,
) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    opts = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
    }
    norm = await run_harvest_normalise(opts, fs)
    adapter = await run_harvest_adapter(opts, norm, fs)
    assert adapter.get("refused") is None, adapter
    broken = json.loads(json.dumps(adapter))
    broken["raw_envelope"]["meta"]["capture"]["sha256"] = "f" * 64
    pour = await run_harvest_pour(opts, broken, fs, norm)
    assert (pour.get("refused") or {}).get("code") == "pour.capture_inconsistent"
    assert f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json" not in fs.files
    assert f"notes/system/checkpoints/grok/{_AGENT}/1.messages.json" not in fs.files
    assert checkpoint_raw_messages_uri(_AGENT, 1) not in fs.files


@pytest.mark.asyncio
async def test_harvest_record_capture_kind_mapping_grok_export(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    opts = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
    }
    norm = await run_harvest_normalise(opts, fs)
    adapter = await run_harvest_adapter(opts, norm, fs)
    pour = await run_harvest_pour(opts, adapter, fs, norm)
    assert pour.get("refused") is None, pour
    result = assemble(
        opts,
        {
            "admit": {"json": {"refused": None}},
            "harvest_normalise": {"json": norm},
            "harvest_adapter": {"json": adapter},
            "harvest_pour": {"json": pour},
        },
    )
    line = result["harvest_record"]
    assert "capture:grok_export@" in line
    assert "~pseudo" not in line


@pytest.mark.asyncio
async def test_harvest_record_capture_kind_mapping_dom_harvest(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 0, _page(0, 0, 1, ["a"], start=True))
    opts = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(opts, fs)
    adapter = await run_harvest_adapter(opts, norm, fs)
    pour = await run_harvest_pour(opts, adapter, fs, norm)
    assert pour.get("refused") is None, pour
    result = assemble(
        opts,
        {
            "admit": {"json": {"refused": None}},
            "harvest_normalise": {"json": norm},
            "harvest_adapter": {"json": adapter},
            "harvest_pour": {"json": pour},
        },
    )
    line = result["harvest_record"]
    assert "~pseudo" in line
    assert "capture:dom_harvest@" in line


def test_grok_export_record_has_no_pseudo() -> None:
    from ._harvest import format_harvest_record

    line = format_harvest_record(
        l0_ref="notes/system/checkpoints/grok/a/1.raw.json",
        l0_sha256="ab" * 32,
        pages=1,
        l0_bytes=3,
        truncated_parts=0,
        l1_ref="notes/system/checkpoints/grok/a/1.messages.json",
        l1_sha256="cd" * 32,
        messages_sha256="ef" * 32,
        source_sha256="11" * 32,
        adapter_read_sha256="22" * 32,
        capture_sha256="33" * 32,
        capture_kind="grok_export",
    )
    assert "capture:grok_export@" in line
    assert "~pseudo" not in line


@pytest.mark.asyncio
async def test_overlap_raw_differs_from_normalized(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 1, _page(0, 1, 4, ["a", "b"], start=True))
    _put_page(fs, 1, 1, 3, _page(1, 3, 4, ["b", "c", "d"], start=False))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None, norm
    adapter = await run_harvest_adapter(options, norm, fs)
    pour = await run_harvest_pour(options, adapter, fs, norm)
    assert pour.get("refused") is None, pour
    raw = fs.files[f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"]
    assert raw != fs.files[norm["normalized_ref"]]
    assert b"positions 1" in raw


@pytest.mark.asyncio
async def test_short_page_without_overlap(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 2, _page(0, 2, 3, ["a", "b"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None, norm
    adapter = await run_harvest_adapter(options, norm, fs)
    assert adapter.get("refused") is None, adapter
    src = adapter["envelope"]["meta"]["sources"][0]
    assert src["unrendered_positions"] == 1
    assert src["unrendered_by_page"] == [{"lo": 0, "hi": 2, "unrendered": 1}]


@pytest.mark.asyncio
async def test_short_overlapping_pages(fs: MemFs) -> None:
    first = _page(0, 1, 4, ["a", "b"], start=True)
    second = _page(1, 3, 4, ["b", "c", "d"], start=False)
    folder = _put_page(fs, 1, 0, 1, first)
    _put_page(fs, 1, 1, 3, second)
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None, norm
    adapter = await run_harvest_adapter(options, norm, fs)
    assert adapter.get("refused") is None, adapter
    assert len(adapter["envelope"]["messages"]) == 4
    src = adapter["envelope"]["meta"]["sources"][0]
    assert src["unrendered_positions"] == 0


@pytest.mark.asyncio
async def test_n2_overlap_redundant_refused(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 1, _page(0, 1, 2, ["a", "b"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    await run_harvest_pour(options, adapter, fs, norm)
    folder2 = _put_page(fs, 2, 1, 1, _page(1, 1, 2, ["b"], start=False))
    options2 = {**options, "n": 2, "l0_ref": folder2}
    norm2 = await run_harvest_normalise(options2, fs)
    assert (norm2.get("refused") or {}).get("code") == "harvest.no_new_pages"


@pytest.mark.asyncio
async def test_short_pages_incremental_n2(fs: MemFs) -> None:
    folder = _put_page(fs, 1, 0, 2, _page(0, 2, 3, ["a", "b"], start=True))
    options = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 1,
        "agent_id": _AGENT,
        "l0_ref": folder,
        "fallback": "electron",
    }
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    await run_harvest_pour(options, adapter, fs, norm)
    third = _page(3, 3, 4, ["c"], start=False)
    folder2 = _put_page(fs, 2, 3, 3, third)
    options2 = {
        "mode": "harvest_seal",
        "house": "bl1",
        "n": 2,
        "agent_id": _AGENT,
        "l0_ref": folder2,
        "fallback": "electron",
    }
    norm2 = await run_harvest_normalise(options2, fs)
    assert norm2.get("refused") is None, norm2
    adapter2 = await run_harvest_adapter(options2, norm2, fs)
    assert adapter2.get("refused") is None, adapter2
    pour2 = await run_harvest_pour(options2, adapter2, fs, norm2)
    assert pour2.get("refused") is None, pour2
    raw2 = fs.files[f"notes/system/checkpoints/grok/{_AGENT}/2.raw.json"]
    raw1 = fs.files[f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"]
    assert raw2.startswith(raw1)
