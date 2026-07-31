"""Tests for inline-only corpus URI admission in frontier dispatch."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from agent_seat import AgentMeta, HydrationBundle

from .corpus_inline import (
    CORPUS_BODY_BUDGET_BYTES,
    inline_corpus_for_packet,
    parse_corpus_uris,
    resolve_corpus_bodies,
)
from .events import (
    PipelineFrontierDispatchCorpusInlined,
    PipelineFrontierDispatchCorpusUnresolved,
)
from .service import FrontierGenerateRequest, build_dispatch_body

_DISPATCH_THREAD = "test-dispatch-thread"
_SIGNAL_RE = re.compile(r"^[a-z]+(\.[a-z]+){1,4}$")
_CORPUS_MARKER = "corpus-body:"


def _packet(*, corpus_line: str, body: str = "review task") -> str:
    return f"""<scope>{body}</scope>
<invariants>ready</invariants>
<task_guidance>Execute.</task_guidance>
<corpus>{corpus_line}</corpus>
<mcp_capabilities>fs(read)</mcp_capabilities>
<output_format>Reply.</output_format>
"""


def _bundle(model: str = "xai/grok-4.5") -> HydrationBundle:
    return HydrationBundle(
        briefing_card_md="# briefing",
        agent_meta=AgentMeta(default_model=model, allowed_models=[model]),
        inline_only=True,
    )


def _force_corpus_inline_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Corpus inline is card-gated; grok-4.5 is MCP-capable — force gate for unit tests."""
    monkeypatch.setattr(
        "systems.frontier_consult.service.corpus_inline_gated",
        lambda _model: True,
    )


def _write_workspaces_doc(tmp_path: Path, rel: str, content: str) -> str:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"workspaces://universal-llm-gateway/{rel}"


@pytest.fixture
def workspaces_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    repo = root / "universal-llm-gateway"
    repo.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(root))
    monkeypatch.setattr(
        "systems.frontier_consult.service._workspaces_root",
        lambda: root,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.corpus_inline.workspaces_root",
        lambda workspaces_root_override=None: (
            workspaces_root_override if workspaces_root_override is not None else root
        ),
    )
    return root


def test_parse_corpus_uris_excludes_skill_paths() -> None:
    text = _packet(
        corpus_line=(
            "workspaces://universal-llm-gateway/tasks/specs/foo.md "
            "workspaces://universal-llm-gateway/.cursor/skills/arch/SKILL.md "
            "cortex://notes/system/agent-skills/bar.md"
        )
    )
    uris = parse_corpus_uris(text)
    assert uris == ("workspaces://universal-llm-gateway/tasks/specs/foo.md",)


def test_parse_corpus_uris_requires_corpus_block() -> None:
    bare = "workspaces://universal-llm-gateway/tasks/specs/foo.md"
    assert parse_corpus_uris(bare) == ()


def test_resolve_corpus_bodies_budget_packs(workspaces_root: Path) -> None:
    uri_a = _write_workspaces_doc(workspaces_root, "tasks/specs/a.md", "A" * 10)
    uri_b = _write_workspaces_doc(workspaces_root, "tasks/specs/b.md", "B" * 10)
    result = resolve_corpus_bodies(
        (uri_a, uri_b),
        budget_bytes=140,
        workspaces_root_override=workspaces_root,
    )
    assert len(result.injected) == 1
    assert len(result.dropped) == 1
    assert "corpus-body:" in result.block_md


@pytest.mark.asyncio
async def test_inline_only_inlines_corpus_from_packet_path(
    workspaces_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uri = _write_workspaces_doc(
        workspaces_root,
        "tasks/specs/dense-spec.md",
        "# Dense spec\nGround truth paragraph.",
    )
    packet_rel = "tmp/packets/corpus-inline.md"
    packet_file = workspaces_root / "universal-llm-gateway" / packet_rel
    packet_file.parent.mkdir(parents=True, exist_ok=True)
    packet_file.write_text(_packet(corpus_line=uri), encoding="utf-8")

    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return _bundle()

    monkeypatch.setattr("systems.frontier_consult.service.hydrate_agent", fake_hydrate)
    _force_corpus_inline_gate(monkeypatch)

    events: list[Any] = []
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "ignored when packet_path set"}],
        role="skeptic",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="xai/grok-4.5",
        packet_path=f"workspaces://universal-llm-gateway/{packet_rel}",
    )
    body = await build_dispatch_body(req, event_publisher=events.append)
    system = body["pipeline_options"]["system"]
    assert _CORPUS_MARKER in system
    assert "Ground truth paragraph." in system
    inlined = [
        e for e in events if e.signal == "pipeline.frontier.dispatch.corpus.inlined"
    ]
    assert len(inlined) == 1
    assert _SIGNAL_RE.match(inlined[0].signal)
    payload = inlined[0].payload
    assert payload["injected_count"] == 1
    assert payload["injected_bytes"] > 0
    assert payload["budget_bytes"] == CORPUS_BODY_BUDGET_BYTES


@pytest.mark.asyncio
async def test_mcp_capable_skips_corpus_inline(
    workspaces_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uri = _write_workspaces_doc(
        workspaces_root, "tasks/specs/skip-me.md", "secret corpus"
    )
    packet_text = _packet(corpus_line=uri)

    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return HydrationBundle(
            briefing_card_md="# briefing",
            agent_meta=AgentMeta(
                default_model="openai/gpt-5.5",
                allowed_models=["openai/gpt-5.5"],
            ),
            inline_only=False,
        )

    monkeypatch.setattr("systems.frontier_consult.service.hydrate_agent", fake_hydrate)

    events: list[Any] = []
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": packet_text}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="openai/gpt-5.5",
    )
    body = await build_dispatch_body(req, event_publisher=events.append)
    system = body["pipeline_options"]["system"]
    assert _CORPUS_MARKER not in system
    assert "secret corpus" not in system
    assert not any(
        e.signal.startswith("pipeline.frontier.dispatch.corpus.")
        for e in events
    )


@pytest.mark.asyncio
async def test_unresolved_corpus_uri_soft_drops(
    workspaces_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = "workspaces://universal-llm-gateway/tasks/specs/does-not-exist.md"
    packet_text = _packet(corpus_line=missing)

    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return _bundle()

    monkeypatch.setattr("systems.frontier_consult.service.hydrate_agent", fake_hydrate)
    _force_corpus_inline_gate(monkeypatch)

    events: list[Any] = []
    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": packet_text}],
        role="skeptic",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="xai/grok-4.5",
        mcp=False,
    )
    body = await build_dispatch_body(req, event_publisher=events.append)
    assert body["pipeline_options"]["mcp"] is False
    unresolved = [
        e for e in events if e.signal == "pipeline.frontier.dispatch.corpus.unresolved"
    ]
    assert len(unresolved) == 1
    assert unresolved[0].payload["uri"] == missing
    assert _SIGNAL_RE.match(unresolved[0].signal)


@pytest.mark.asyncio
async def test_skill_layer_c_retained_under_corpus_budget_pressure(
    workspaces_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uri = _write_workspaces_doc(workspaces_root, "tasks/specs/huge.md", "Z" * 5000)
    packet_text = _packet(corpus_line=uri)
    skill_md = "<!-- injected-body:rule:foo digest:sha256:abc -->"

    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return HydrationBundle(
            briefing_card_md="# briefing",
            agent_meta=AgentMeta(default_model="xai/grok-4.5"),
            inline_only=True,
            injected_bodies_md=skill_md,
            injection_meta={
                "injected": [{"bytes": len(skill_md)}],
                "dropped": [],
                "metrics": {},
            },
        )

    monkeypatch.setattr("systems.frontier_consult.service.hydrate_agent", fake_hydrate)
    _force_corpus_inline_gate(monkeypatch)
    monkeypatch.setattr(
        "systems.frontier_consult.service.CORPUS_BODY_BUDGET_BYTES",
        64,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.corpus_inline.CORPUS_BODY_BUDGET_BYTES",
        64,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": packet_text}],
        role="skeptic",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="xai/grok-4.5",
    )
    body = await build_dispatch_body(req)
    system = body["pipeline_options"]["system"]
    assert "injected-body:rule:foo" in system
    assert _CORPUS_MARKER not in system


@pytest.mark.asyncio
async def test_dispatch_thread_body_resolves_corpus(
    workspaces_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uri = _write_workspaces_doc(
        workspaces_root,
        "tasks/specs/thread-body.md",
        "Thread-body corpus content.",
    )
    packet_text = _packet(corpus_line=uri)

    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return _bundle()

    monkeypatch.setattr("systems.frontier_consult.service.hydrate_agent", fake_hydrate)
    _force_corpus_inline_gate(monkeypatch)

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": packet_text}],
        role="skeptic",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="xai/grok-4.5",
    )
    body = await build_dispatch_body(req)
    system = body["pipeline_options"]["system"]
    assert "Thread-body corpus content." in system
    assert _CORPUS_MARKER in system


def test_event_factories_match_catalog_shape() -> None:
    inlined = PipelineFrontierDispatchCorpusInlined(
        request_id="abc",
        role="skeptic",
        model="xai/grok-4.5",
        injected_count=1,
        dropped_count=0,
        injected_bytes=100,
        dropped_bytes=0,
        budget_bytes=CORPUS_BODY_BUDGET_BYTES,
    )
    unresolved = PipelineFrontierDispatchCorpusUnresolved(
        request_id="abc",
        role="skeptic",
        model="xai/grok-4.5",
        uri="workspaces://universal-llm-gateway/missing.md",
    )
    assert _SIGNAL_RE.match(inlined.signal)
    assert _SIGNAL_RE.match(unresolved.signal)


def test_inline_corpus_for_packet_unit(workspaces_root: Path) -> None:
    uri = _write_workspaces_doc(workspaces_root, "tasks/specs/unit.md", "unit body")
    result = inline_corpus_for_packet(
        _packet(corpus_line=uri),
        workspaces_root_override=workspaces_root,
    )
    assert result.injected_bytes > 0
    assert "unit body" in result.block_md
