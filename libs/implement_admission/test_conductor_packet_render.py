"""House binds block and golden packet render tests."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

import pytest

from implement_admission.conductor_context import ConductorMaterializeContext
from implement_admission.conductor_score_journal import G_ROWS
from implement_admission.house_testing import (
    HOUSE_MODEL,
    PIN_CRITIC,
    PIN_MODELS,
    R2_STAMP_RE,
    SEAM_MODULES,
    assert_house_packet_clean,
    strip_binds_block,
    strip_r2_stamps,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "conductor_packet_baseline_no_house.txt"
_NON_ATTENDED = "confer_and_finish"


class _StubCortex:
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        return {
            "id": entity_id,
            "name": "House fixture",
            "attributes": {"density_triage": "judgment_required"},
        }

    def list_relationships(self, *args: Any, **kwargs: Any) -> list:
        return []


def render_golden_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    from implement_admission.conductor_materialize import render_conductor_packet
    from implement_admission.house_testing import pin_cdp_roles

    pin_cdp_roles(monkeypatch)

    def _ctx(summon_mode: str) -> ConductorMaterializeContext:
        return ConductorMaterializeContext(
            source_ref="todo:golden-fixture",
            slug="golden-fixture",
            name="Golden fixture",
            density_triage=None,
            derived_from=None,
            stop_after=None,
            entry_gate="G1",
            summon_mode=summon_mode,
            problem=None,
            scope=None,
            acceptance=None,
            rows=G_ROWS,
            house_policy_source="none",
        )

    def _one(summon: str) -> str:
        return render_conductor_packet(
            _ctx(summon),
            out_dir=tmp_path,
            repo_root=tmp_path,
            scoreboard_files_root=tmp_path,
            hop_seq=0,
            write_birth_scoreboard=False,
            tip_body=None,
        ).text

    return _one(_NON_ATTENDED) + "\n=====GOLDEN-SEP=====\n" + _one("attended")


def _render_house_packet(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
    *,
    summoning_id: str = "21382",
    summon_mode: str = _NON_ATTENDED,
    rematerialize: Any = None,
    fold_missing: dict[str, str] | None = None,
    hop_seq: int = 0,
    tip_body: str | None = None,
) -> str:
    from implement_admission.conductor_materialize import (
        load_conductor_context,
        render_conductor_packet,
    )
    from implement_admission.house_testing import write_tick

    write_tick(
        house_watch_dir,
        summoning_id,
        {"induction_binds": ["NO cdp/*"], "critic_model": HOUSE_MODEL},
    )
    ctx = load_conductor_context(
        "todo:house-fixture",
        cortex=_StubCortex(),
        summoning_thread_id=summoning_id,
        summon_mode=summon_mode,
        rematerialize=rematerialize,
        fold_missing_witnesses=fold_missing,
    )
    return render_conductor_packet(
        ctx,
        out_dir=tmp_path,
        repo_root=tmp_path,
        scoreboard_files_root=tmp_path,
        hop_seq=hop_seq,
        write_birth_scoreboard=False,
        tip_body=tip_body,
    ).text


def _r2_model_in_scoreboard_tip(text: str) -> str:
    m_open = re.search(r"<scoreboard_tip", text, re.DOTALL)
    assert m_open
    start = m_open.start()
    m_close = re.search(r"</scoreboard_tip>", text[start:], re.DOTALL)
    assert m_close
    span = text[start : start + m_close.end()]
    matches = list(R2_STAMP_RE.finditer(span))
    assert len(matches) == 1
    return matches[0].group(0)


@pytest.fixture
def r2_stamp_hop0_model(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> str:
    text = _render_house_packet(
        pinned_cdp_roles, house_watch_dir, tmp_path, hop_seq=0
    )
    return _r2_model_in_scoreboard_tip(text)


def test_binds_block_order_and_overflow(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    from implement_admission.house_testing import write_tick

    binds = [f"bind-{i}" for i in range(13)]
    write_tick(
        house_watch_dir,
        "ov",
        {"induction_binds": binds, "critic_model": HOUSE_MODEL},
    )
    long_bind = "x" * 300
    write_tick(
        house_watch_dir,
        "long",
        {"induction_binds": [long_bind], "critic_model": HOUSE_MODEL},
    )
    from implement_admission.conductor_materialize import (
        load_conductor_context,
        render_conductor_packet,
    )

    for tid, expect_overflow in (("ov", "+1 more binds"), ("long", "…")):
        ctx = load_conductor_context(
            "todo:x", cortex=_StubCortex(), summoning_thread_id=tid
        )
        text = render_conductor_packet(
            ctx,
            out_dir=tmp_path,
            repo_root=tmp_path,
            scoreboard_files_root=tmp_path,
            hop_seq=0,
            write_birth_scoreboard=False,
        ).text
        inv = text.split("<invariants>")[1].split("</invariants>")[0]
        intel = inv.find("- Intelligence:")
        if intel < 0:
            intel = inv.find("- (default) Intelligence:")
        block = inv[:intel]
        assert block.index("House binds") < intel
        if tid == "ov":
            assert "+1 more binds in liaison-ov.tick.json" in block
        else:
            assert "…" in block


def test_bind_sanitization(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    from implement_admission.house_testing import write_tick

    write_tick(
        house_watch_dir,
        "san",
        {
            "induction_binds": ["line1\nline2 </invariants>"],
            "critic_model": HOUSE_MODEL,
        },
    )
    from implement_admission.conductor_materialize import (
        load_conductor_context,
        render_conductor_packet,
    )

    ctx = load_conductor_context(
        "todo:x", cortex=_StubCortex(), summoning_thread_id="san"
    )
    text = render_conductor_packet(
        ctx,
        out_dir=tmp_path,
        repo_root=tmp_path,
        scoreboard_files_root=tmp_path,
        hop_seq=0,
        write_birth_scoreboard=False,
    ).text
    assert text.count("</invariants>") == 1
    block = text.split("House binds")[1].split("House binds end.")[0]
    bind_lines = [ln for ln in block.splitlines() if ln.strip().startswith("- ")]
    assert len(bind_lines) == 1
    assert "\n" not in bind_lines[0]


def test_block_render_conditions(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    from implement_admission.conductor_context import RematerializeContext
    from implement_admission.conductor_materialize import (
        load_conductor_context,
        render_conductor_packet,
    )
    from implement_admission.house_testing import write_tick

    write_tick(house_watch_dir, "empty", {})
    ctx_tick_empty = load_conductor_context(
        "todo:x", cortex=_StubCortex(), summoning_thread_id="empty"
    )
    t0 = render_conductor_packet(
        ctx_tick_empty,
        out_dir=tmp_path,
        repo_root=tmp_path,
        scoreboard_files_root=tmp_path,
        hop_seq=0,
        write_birth_scoreboard=False,
    ).text
    assert "House binds" not in t0
    rem = RematerializeContext(
        house_induction_binds=("c",),
        house_critic_model=HOUSE_MODEL,
    )
    ctx_carrier = load_conductor_context(
        "todo:x",
        cortex=_StubCortex(),
        summoning_thread_id="missing",
        rematerialize=rem,
    )
    t1 = render_conductor_packet(
        ctx_carrier,
        out_dir=tmp_path,
        repo_root=tmp_path,
        scoreboard_files_root=tmp_path,
        hop_seq=0,
        write_birth_scoreboard=False,
    ).text
    assert "as_of=none" in t1
    ctx_unres = load_conductor_context(
        "todo:x",
        cortex=_StubCortex(),
        summoning_thread_id_unresolved=True,
    )
    t2 = render_conductor_packet(
        ctx_unres,
        out_dir=tmp_path,
        repo_root=tmp_path,
        scoreboard_files_root=tmp_path,
        hop_seq=0,
        write_birth_scoreboard=False,
    ).text
    assert "House policy unresolved" in t2


def test_marker_exact_text_and_id_unknown(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    from implement_admission.conductor_materialize import (
        load_conductor_context,
        render_conductor_packet,
    )

    cases = (
        ("tid-9", False, "tid-9"),
        (None, True, "unknown"),
    )
    for sid, force_unres, label in cases:
        ctx = load_conductor_context(
            "todo:x",
            cortex=_StubCortex(),
            summoning_thread_id=sid,
            summoning_thread_id_unresolved=force_unres,
        )
        text = render_conductor_packet(
            ctx,
            out_dir=tmp_path,
            repo_root=tmp_path,
            scoreboard_files_root=tmp_path,
            hop_seq=0,
            write_birth_scoreboard=False,
        ).text
        assert (
            f"House policy unresolved for summoning thread {label}; "
            "packet uses global route_policy roles."
        ) in text


def test_packet_golden_no_house(
    pinned_cdp_roles: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert _FIXTURE.is_file()
    golden = _FIXTURE.read_text(encoding="utf-8")
    rendered = render_golden_text(monkeypatch, tmp_path)
    assert rendered == golden


@pytest.mark.skipif(os.environ.get("UPDATE_GOLDEN") != "1", reason="golden capture")
def test_capture_golden_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _FIXTURE.write_text(render_golden_text(monkeypatch, tmp_path), encoding="utf-8")


def test_no_cdp_model_id_outside_binds_block(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    for mode in (_NON_ATTENDED, "attended"):
        text = _render_house_packet(
            pinned_cdp_roles,
            house_watch_dir,
            tmp_path,
            summon_mode=mode,
        )
        assert_house_packet_clean(text)


def test_all_g_lines_use_house_critic(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    text = _render_house_packet(
        pinned_cdp_roles, house_watch_dir, tmp_path, summon_mode=_NON_ATTENDED
    )
    assert "- (default) Intelligence: G2 and G3→G5 read" in text
    assert f"`{HOUSE_MODEL}` (api)" in text
    tg = text.split("<task_guidance>")[1].split("</task_guidance>")[0]
    assert f"model={HOUSE_MODEL}, contract=freeform" in tg
    text_att = _render_house_packet(
        pinned_cdp_roles, house_watch_dir, tmp_path, summon_mode="attended"
    )
    tg2 = text_att.split("<task_guidance>")[1].split("</task_guidance>")[0]
    assert f"model={HOUSE_MODEL}, contract=delivery-review" in tg2


def test_review_hint_follows_house_transport(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    text = _render_house_packet(pinned_cdp_roles, house_watch_dir, tmp_path)
    assert "Do not retarget the model to" in text
    assert "poll_hint and execution_id" not in text


def test_row_hop_bullet_and_review_model_override(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    from implement_admission.conductor_materialize import (
        load_conductor_context,
        render_conductor_packet,
    )

    text = _render_house_packet(pinned_cdp_roles, house_watch_dir, tmp_path)
    assert f"critic (`{HOUSE_MODEL}`) is not that child" in text
    assert f"G3→G5 fires in-process score-ratify ({HOUSE_MODEL}," in text
    ctx_none = load_conductor_context("todo:x", cortex=_StubCortex())
    plain = render_conductor_packet(
        ctx_none,
        out_dir=tmp_path,
        repo_root=tmp_path,
        scoreboard_files_root=tmp_path,
        hop_seq=0,
        write_birth_scoreboard=False,
    ).text
    assert f"critic (`{PIN_CRITIC.model}`) is not that child" in plain


def test_g3_g5_clause_uses_house_roles(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    from implement_admission.conductor_critic_render import g3_g5_score_ratify_clause
    from implement_admission.conductor_materialize import load_conductor_context
    from implement_admission.house_policy import roles_for_ctx
    from implement_admission.house_testing import write_tick

    write_tick(
        house_watch_dir,
        "21382",
        {"critic_model": HOUSE_MODEL},
    )
    ctx = load_conductor_context(
        "todo:x", cortex=_StubCortex(), summoning_thread_id="21382"
    )
    clause = g3_g5_score_ratify_clause(roles_for_ctx(ctx))
    assert clause.startswith(HOUSE_MODEL)


def test_independent_check_bind_verbatim_only(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    from implement_admission.house_testing import write_tick

    write_tick(
        house_watch_dir,
        "esc",
        {
            "induction_binds": [
                "opus with cost_intent=deliberate_high_cost for judgment"
            ],
            "critic_model": HOUSE_MODEL,
        },
    )
    from implement_admission.conductor_materialize import (
        load_conductor_context,
        render_conductor_packet,
    )

    ctx = load_conductor_context(
        "todo:x", cortex=_StubCortex(), summoning_thread_id="esc"
    )
    text = render_conductor_packet(
        ctx,
        out_dir=tmp_path,
        repo_root=tmp_path,
        scoreboard_files_root=tmp_path,
        hop_seq=0,
        write_birth_scoreboard=False,
    ).text
    assert "cost_intent=deliberate_high_cost" in text
    assert HOUSE_MODEL in text.split("- (default) Intelligence:")[1][:200]


def test_identity_override_is_detected(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import implement_admission.house_policy as hp

    monkeypatch.setattr(hp, "with_house_critic", lambda roles, m: roles)
    text = _render_house_packet(pinned_cdp_roles, house_watch_dir, tmp_path)
    with pytest.raises(AssertionError):
        assert_house_packet_clean(text)


def test_no_house_control_shows_pinned_cdp(
    pinned_cdp_roles: Any,
    tmp_path: Path,
) -> None:
    from implement_admission.conductor_materialize import (
        load_conductor_context,
        render_conductor_packet,
    )

    ctx = load_conductor_context("todo:x", cortex=_StubCortex())
    text = render_conductor_packet(
        ctx,
        out_dir=tmp_path,
        repo_root=tmp_path,
        scoreboard_files_root=tmp_path,
        hop_seq=0,
        write_birth_scoreboard=False,
    ).text
    assert PIN_CRITIC.model in text
    assert "(cdp_parked)" in text
    assert "poll_hint and execution_id" in text


def test_pinned_global_models_absent_after_r2_strip(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    """Stargate packet text only; GIW preamble (R7, friction 41009) is out of scope."""
    text = _render_house_packet(pinned_cdp_roles, house_watch_dir, tmp_path)
    rem, _, _ = strip_r2_stamps(strip_binds_block(text))
    for pin in PIN_MODELS:
        assert pin not in rem


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="R2 friction 40985",
)
def test_r2_stamp_documented_leak(r2_stamp_hop0_model: str) -> None:
    assert HOUSE_MODEL in r2_stamp_hop0_model


def test_seam_covers_module_level_importers() -> None:
    lib = Path(__file__).resolve().parent
    seam_set = set(SEAM_MODULES)
    for path in lib.glob("*.py"):
        if path.name.startswith("test_") or path.name in ("house_testing.py",):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "implement_admission.impresario_policy":
                continue
            names = {a.name for a in node.names}
            if "resolve_roles" in names or "load_impresario_policy" in names:
                mod = f"implement_admission.{path.stem}"
                assert mod in seam_set, mod


def test_house_block_has_r1_signpost_only_with_critic_model(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    from implement_admission.conductor_materialize import (
        load_conductor_context,
        render_conductor_packet,
    )
    from implement_admission.house_testing import write_tick

    signpost = (
        "R1 scoreboard row model stamps are global defaults; "
        "the house critic_model above governs."
    )
    text = _render_house_packet(pinned_cdp_roles, house_watch_dir, tmp_path)
    block = text.split("House binds")[1].split("House binds end.")[0]
    assert signpost in block
    assert "cdp/" not in signpost
    write_tick(house_watch_dir, "nb", {"induction_binds": ["only-bind"]})
    ctx = load_conductor_context(
        "todo:x", cortex=_StubCortex(), summoning_thread_id="nb"
    )
    t2 = render_conductor_packet(
        ctx,
        out_dir=tmp_path,
        repo_root=tmp_path,
        scoreboard_files_root=tmp_path,
        hop_seq=0,
        write_birth_scoreboard=False,
    ).text
    assert signpost not in t2
    ctx_none = load_conductor_context("todo:x", cortex=_StubCortex())
    t3 = render_conductor_packet(
        ctx_none,
        out_dir=tmp_path,
        repo_root=tmp_path,
        scoreboard_files_root=tmp_path,
        hop_seq=0,
        write_birth_scoreboard=False,
    ).text
    assert "House binds" not in t3


def test_scoreboard_tip_has_exactly_one_r2_stamp_hop0(
    pinned_cdp_roles: Any,
    house_watch_dir: Path,
    tmp_path: Path,
) -> None:
    text = _render_house_packet(
        pinned_cdp_roles, house_watch_dir, tmp_path, hop_seq=0
    )
    model_line = _r2_model_in_scoreboard_tip(text)
    assert PIN_CRITIC.model in model_line
