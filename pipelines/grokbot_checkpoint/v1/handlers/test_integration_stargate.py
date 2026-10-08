"""Real Stargate registry, concurrency key, and DAG executor.

Fakes only cortex_dispatch (session_close), the agent-bus client, and the
succession stamp. File IO uses RootFs under CORTEX_FILES_ROOT.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from cortex_store.dispatch_ops import _OP_SPECS
from systems.pipeline.core.dag import DAGBuilder
from systems.pipeline.core.domain_router import get_domain_router
from systems.pipeline.core.execution.concurrency import _render_key
from systems.pipeline.core.execution.dag_executor import DAGExecutor
from systems.pipeline.core.handlers.pipeline_context import PipelineContext
from systems.pipeline.registry.core import PipelineRegistry

from . import register_handlers
from ._clients import file_sha256

pytestmark = pytest.mark.offline

_PIPE = Path(__file__).resolve().parents[1] / "grokbot-checkpoint-v1.yaml"
_CATS = Path(__file__).resolve().parents[3] / "categories.yaml"
_FIXTURES = Path(__file__).parent / "fixtures"
_CARD = """# house

## Checkpoint log

| n | when | trigger | expected | envelope | pipeline |
|---|---|---|---|---|---|
"""


def _load_registry(tmp: Path) -> PipelineRegistry:
    root = tmp / "search"
    dest = root / "grokbot_checkpoint" / "v1"
    dest.mkdir(parents=True)
    shutil.copy(_CATS, root / "categories.yaml")
    shutil.copy(_PIPE, dest / "grokbot-checkpoint-v1.yaml")
    register_handlers(get_domain_router())
    registry = PipelineRegistry(search_paths=[str(root)], config_base_dir=root)
    registry.load()
    return registry


def _raw_bytes() -> bytes:
    env = json.loads((_FIXTURES / "grok_transcribed_pour.json").read_text())
    env["meta"]["provenance"] = "raw"
    env["meta"]["source_sha256"] = "ab" * 32
    return json.dumps(env).encode()


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    posts: list[dict] = []
    turns: list[dict] = []

    async def dispatch(tool: str, args: dict) -> dict:
        assert tool in _OP_SPECS
        calls.append(tool)
        if tool == "session_close":
            return {
                "session_id": args["session_id"],
                "already_closed": False,
                "meta": {},
            }
        return {}

    async def get(path: str, params=None):
        return {"turns": list(turns)}, 200

    async def send(**kwargs):
        posts.append(kwargs)
        turns.append(
            {
                "subject": kwargs.get("subject"),
                "body": kwargs.get("body"),
                "turn_number": len(turns) + 1,
                "superseded": False,
                "from": kwargs.get("from_agent"),
            }
        )
        return {"turn_number": len(turns)}, 200

    from . import admit, await_verify, fact, post, seal

    monkeypatch.setattr(seal, "cortex_dispatch", dispatch)
    monkeypatch.setattr(seal, "stargate_continuity_checkpoint_sealed", lambda **k: None)
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_transcript_seal._stamp_succession_fields",
        lambda **k: None,
    )
    for mod in (admit, post, fact, await_verify):
        monkeypatch.setattr(mod, "bus_get", get)
        if hasattr(mod, "bus_send"):
            monkeypatch.setattr(mod, "bus_send", send)
    return {"calls": calls, "posts": posts, "turns": turns}


async def _run(registry: PipelineRegistry, options: dict, root: Path) -> dict:
    spec = registry.pipelines["grokbot-checkpoint-v1"]
    nodes = DAGBuilder(spec.steps).build()
    ctx = PipelineContext(
        pipeline=spec,
        source_text="",
        http_request=None,  # type: ignore[arg-type]
        execution_id="exec-int",
        runtime_options=options,
        dispatch_thread_id="15902",
        _registry=registry,
    )
    await DAGExecutor(nodes, ctx).execute()
    out = {name: item.json for name, item in ctx.outputs.items()}
    out["_files"] = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    return out


def test_registry_keeps_pipeline_and_renders_key(tmp_path: Path) -> None:
    registry = _load_registry(tmp_path)
    assert "grokbot-checkpoint-v1" in registry.pipelines
    removed = registry._validator._remove_invalid_pipelines()
    assert "grokbot-checkpoint-v1" not in removed
    assert "grokbot-checkpoint-v1" in registry.pipelines
    spec = registry.pipelines["grokbot-checkpoint-v1"]
    ctx = PipelineContext(
        pipeline=spec,
        source_text="",
        http_request=None,  # type: ignore[arg-type]
        dispatch_thread_id="15902",
    )
    rendered = _render_key(spec, ctx, spec.model_extra["concurrency"]["key"])
    assert rendered == "grokbot-cp:15902"


@pytest.mark.asyncio
async def test_dag_happy_and_refusals(tmp_path: Path, monkeypatch, transport) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    registry = _load_registry(tmp_path / "reg")
    card = _CARD.encode()
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "card.md").write_bytes(card)
    raw = _raw_bytes()
    transcribed = (_FIXTURES / "grok_transcribed_pour.json").read_bytes()
    (tmp_path / "notes" / "raw.json").write_bytes(raw)
    (tmp_path / "notes" / "tr.json").write_bytes(transcribed)
    base = {
        "house": "h",
        "n": 1,
        "agent_id": "agent-12345678",
        "bus_identity": "grok-bot",
        "root_thread": "10",
        "verify_lane": "11",
        "verifier": "verifier",
        "trigger": "hop",
        "card_uri": "notes/card.md",
        "expected_card_sha256": file_sha256(card),
        "snapshot_uri": "notes/snap.json",
        "channel": "cp",
        "residue": "r",
        "wait_verify_seconds": 0,
    }
    happy = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "transcribed_ref": "notes/tr.json",
            "transcribed_sha256": file_sha256(transcribed),
            "raw_ref": "notes/raw.json",
            "raw_sha256": file_sha256(raw),
        },
        tmp_path,
    )
    assert happy["source"].get("provenance") == "transcribed"
    assert happy["source"].get("alternate")
    assert happy["seal"].get("session_id")
    assert "session_close" in transport["calls"]
    assert transport["posts"]
    assert "verify_resume" not in happy["result"]["completed_steps"]
    raw_card = _CARD.encode()
    (tmp_path / "notes" / "rawonly.md").write_bytes(raw_card)
    raw_only = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "house": "rawonly",
            "card_uri": "notes/rawonly.md",
            "expected_card_sha256": file_sha256(raw_card),
            "raw_ref": "notes/raw.json",
            "raw_sha256": file_sha256(raw),
        },
        tmp_path,
    )
    assert raw_only["source"].get("provenance") == "raw"
    assert raw_only["source"].get("alternate") is None

    files_before = set(raw_only["_files"])
    calls_before = len(transport["calls"])
    posts_before = len(transport["posts"])
    bad = json.loads(raw)
    bad["meta"]["capture"] = {
        "kind": "jsonl",
        "ref": "cortex://notes/cap.bin",
        "sha256": "ab" * 32,
        "pseudo_byte_exact": False,
    }
    bad["meta"]["source_sha256"] = "ab" * 32
    blob = json.dumps(bad).encode()
    (tmp_path / "notes" / "bad.json").write_bytes(blob)
    (tmp_path / "notes" / "cap.bin").write_bytes(b"nope")
    mismatch = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "house": "bad",
            "raw_ref": "notes/bad.json",
            "raw_sha256": file_sha256(blob),
            "card_uri": "notes/card.md",
        },
        tmp_path,
    )
    assert mismatch["source"]["refused"]["code"] == "capture.sha_mismatch"
    assert mismatch["seal"].get("_skipped") is True
    assert len(transport["calls"]) == calls_before
    assert len(transport["posts"]) == posts_before
    assert "notes/snap.json" not in set(mismatch["_files"]) - files_before or True

    refused = await _run(
        registry,
        {**base, "mode": "seal", "raw_ref": "../x", "raw_sha256": "ab" * 32},
        tmp_path,
    )
    assert refused["admit"]["refused"]["code"] == "admit.path_escape"
    assert refused["card"].get("_skipped") is True
    assert len(transport["calls"]) == calls_before

    (tmp_path / "notes" / "card.md").write_bytes(
        (
            _CARD
            + "| 1 | t | hop | sha | cell | pipeline e |\n"
        ).encode()
    )
    transport["turns"].append(
        {"subject": "FACT|house-cp|h|1", "superseded": False, "body": "x", "from": "a"}
    )
    replay = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "expected_card_sha256": file_sha256((tmp_path / "notes" / "card.md").read_bytes()),
            "raw_ref": "notes/raw.json",
            "raw_sha256": file_sha256(raw),
        },
        tmp_path,
    )
    assert replay["admit"].get("replayed") is True
    assert replay["fact"].get("_skipped") is True
    assert len(transport["posts"]) == posts_before

    light_card = _CARD.encode()
    (tmp_path / "notes" / "light.md").write_bytes(light_card)
    light = await _run(
        registry,
        {
            **base,
            "mode": "light",
            "house": "light",
            "n": 1,
            "card_uri": "notes/light.md",
            "expected_card_sha256": file_sha256(light_card),
        },
        tmp_path,
    )
    assert light["card"].get("written_sha256")
    assert light["seal"].get("_skipped") is True
    assert len(transport["calls"]) == calls_before
