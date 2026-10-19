"""harvest_seal mode: normalise, adapter, pour, and idempotent refusals."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from systems.pipeline.core.conditions import evaluate_condition, extract_condition_deps

from ._clients import file_sha256
from ._harvest import run_adapter_twice
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
    assert pour["ref"].startswith("notes/system/checkpoints/grok/")
    assert "tape/" not in pour["ref"]
    blocks = pour["envelope"]["messages"][0]["content"]
    assert isinstance(blocks, list)


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
        "harvest_normalise": SimpleNamespace(
            json={"refused": None, "_skipped": False}
        ),
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
    assert line.startswith(f"raw {l0_ref}@{l0_sha}")
    assert "tape/pours" not in line
    assert "coverage=full" in line
    cap_sha = pour["envelope"]["meta"]["capture"]["sha256"]
    cap_tok = f"capture:dom_harvest@{cap_sha[:12]}~pseudo"
    assert cap_tok in line
    assert cap_sha[:12] == norm["normalized_sha256"][:12]
    assert cap_sha[:12] != l0_sha[:12]
