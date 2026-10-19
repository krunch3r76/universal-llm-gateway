"""Width-seat rendering from Pattern A ``resolve_roles()``."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from implement_admission.conductor_critic_render import (
    WIDTH_SEAT_MARKER_START,
    embed_width_seat_block,
    g3_g5_score_ratify_clause,
    width_seat_block,
)
from implement_admission.conductor_materialize import (
    attended_g3_g5_task_sentence,
    hop_invariant_g3_g5_fragment,
)
from implement_admission.impresario_policy import resolve_roles
from services.git_integration_worker.cursor_sdk_packet import (
    _CONDUCTOR_ATTENDED_RESURFACE_TEMPLATE,
    _CONDUCTOR_HOP_TEMPLATE,
)


def _render_templates(seat_clause: str) -> tuple[str, str]:
    hop = _CONDUCTOR_HOP_TEMPLATE.format(
        hop_seq=1,
        thread_id="13550",
        lineage="",
        width_clause=seat_clause,
    )
    attended = _CONDUCTOR_ATTENDED_RESURFACE_TEMPLATE.format(
        caller_agent="cursor",
        summoning_thread_id="9638",
        width_clause=seat_clause,
    )
    return hop, attended


@pytest.mark.offline
def test_default_critic_renders_in_clause_and_materialize() -> None:
    roles = resolve_roles()
    assert roles.critic.model == "cursor/gemini-4-argon"
    clause = g3_g5_score_ratify_clause()
    hop, attended = _render_templates(clause)
    for rendered in (
        clause,
        hop,
        attended,
        hop_invariant_g3_g5_fragment(),
        attended_g3_g5_task_sentence(),
    ):
        assert "cursor/gemini-4-argon" in rendered


@pytest.mark.offline
def test_width_seat_block_names_critic_render_module() -> None:
    block = width_seat_block()
    assert "conductor_critic_render.py" in block
    assert "cursor/gemini-4-argon" in block


_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "cursor-plugins" / "ulg-ecosystem"
_REPO_ROOT = Path(__file__).resolve().parents[2]

_NON_WIDTH_ACTIVE: dict[str, str] = {
    "cursor-plugins/ulg-ecosystem/commands/prompt-code-responsibility-check.md": (
        "ACTIVE is a set name in an invariant example (L53)"
    ),
    "cursor-plugins/ulg-ecosystem/rules/dispatch-kernel_ulg.mdc": (
        "alwaysApply per-file ceiling (1100)"
    ),
}

_P1 = re.compile(r"\bACTIVE(?:\.model)?\**[^;,.|]{0,12}?cdp/", re.IGNORECASE)
_P2 = re.compile(r"model=cdp/[\w.-]+,\s*reasoning_effort=", re.IGNORECASE)
_P3 = re.compile(
    r"(reasoning_effort|effort_when_bind_gates_wave)\W{0,3}"
    r"(low|medium|high|extra|xhigh|max)\b",
    re.IGNORECASE,
)


def _plugin_files() -> list[Path]:
    out: list[Path] = []
    for pattern in ("*.md", "*.mdc"):
        out.extend(sorted(_PLUGIN_ROOT.rglob(pattern)))
    return out


def _strip_blocks(text: str) -> str:
    while True:
        start = text.find("<!-- width-seat:v1:start -->")
        if start == -1:
            return text
        end = text.find("<!-- width-seat:v1:end -->", start)
        if end == -1:
            return text
        text = text[:start] + text[end + len("<!-- width-seat:v1:end -->") :]


def _rel(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


@pytest.mark.offline
def test_width_seat_blocks_match_active() -> None:
    """T1: every width-seat block matches resolve_roles()."""
    stale: list[str] = []
    found = False
    for path in _plugin_files():
        text = path.read_text(encoding="utf-8")
        if WIDTH_SEAT_MARKER_START not in text:
            continue
        found = True
        if embed_width_seat_block(text) != text:
            stale.append(_rel(path))
    assert found, "no width-seat blocks in plugin tree"
    assert stale == [], (
        "width-seat blocks stale vs ACTIVE — run "
        "scripts/cursor/patch_ecosystem_policy_blocks.py --width-seat:\n"
        + "\n".join(stale)
    )


@pytest.mark.offline
def test_no_width_seat_literal_outside_block() -> None:
    """T2: no width-seat literals outside generated blocks."""
    violations: list[str] = []
    for path in _plugin_files():
        rel = _rel(path)
        if rel in _NON_WIDTH_ACTIVE:
            continue
        text = path.read_text(encoding="utf-8")
        body = _strip_blocks(text)
        norm = re.sub(r"\s+", " ", body)
        for match in _P1.finditer(norm):
            violations.append(f"{_rel(path)} P1: {match.group(0)!r}")
        for match in _P2.finditer(norm):
            violations.append(f"{_rel(path)} P2: {match.group(0)!r}")
        for line in body.splitlines():
            if re.search(r"\bACTIVE\b", line):
                for match in _P3.finditer(line):
                    violations.append(
                        f"{_rel(path)} P3: {line.strip()!r} ({match.group(0)!r})"
                    )
    assert violations == [], "width-seat literal outside block:\n" + "\n".join(
        violations
    )


@pytest.mark.offline
def test_width_seat_named_files_carry_block() -> None:
    """T3: files naming ACTIVE as width seat carry a block (except exclusions)."""
    missing: list[str] = []
    for path in _plugin_files():
        rel = _rel(path)
        if rel in _NON_WIDTH_ACTIVE:
            continue
        text = path.read_text(encoding="utf-8")
        body = _strip_blocks(text)
        if not re.search(r"\bACTIVE\b", body):
            continue
        if WIDTH_SEAT_MARKER_START not in text:
            missing.append(rel)
    assert missing == [], (
        "ACTIVE named outside block without _NON_WIDTH_ACTIVE entry:\n"
        + "\n".join(missing)
    )
