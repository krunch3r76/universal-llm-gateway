"""Wake induction: planted addresses, episodic frame, cap, quiet form."""

from __future__ import annotations

from bus_watch.induction import (
    CSE_INLINE_TRUNCATION_MARKER,
    INDUCTION_CAP,
    build_wake_induction,
)


def _digest(**over):  # noqa: ANN003, ANN202
    base = {
        "ts": "2026-09-13T04:10:00Z",
        "root": {
            "id": "10479",
            "turns": 214,
            "last_subject": "CHECKPOINT 10479 54b93098",
        },
        "register": "autonomous",
        "attention": [
            {
                "id": "10586",
                "unread": 1,
                "last_subject": "cursor-sdk CLOSEOUT 824494a8ceba",
            },
            {"kind": "budget_estimate", "used_tokens": 1, "window_limit_tokens": 2},
        ],
        "watchers_complete_unrelayed": [
            {"label": "10479-10586-r9-none", "thread": "10586"}
        ],
        "budget": {"stop_class": None},
        "checkpoint_due": False,
        "changed_since_last_tick": True,
        "summary_row": "R9 Lane A serialization (design, a:33072)",
        "policy": {
            "induction_binds": ["hopper paused (10479#210)"],
            "induction_loaded": ["git-posture § Land"],
        },
    }
    base.update(over)
    return base


def test_induction_plants_addresses_not_a_skill_copy() -> None:
    text = build_wake_induction(_digest())
    assert text.startswith("WAKE 10479 · turns=214")
    assert "Event: 10586 unread=1" in text
    assert "watcher 10479-10586-r9-none complete" in text
    assert "NOW: tip turn #214 on agent-bus:10479 · «CHECKPOINT 10479 54b93098»" in text
    assert (
        "do not re-read): reasoning-posture skill · git-posture § Land"
        in text
    )
    assert "register=autonomous · hopper paused (10479#210)" in text
    assert "repo→auto" in text or "cdp/fable" in text
    assert len(text.encode("utf-8")) <= INDUCTION_CAP


def test_induction_attended_context_budget_emits_hop_line() -> None:
    text = build_wake_induction(
        _digest(
            register="attended",
            budget={
                "stop_class": "CONTEXT_BUDGET",
                "used_tokens": 305_000,
                "window_limit_tokens": 500_000,
                "source": "ide.transcript",
            },
        )
    )
    assert "HOP liaison-ide-hop.py --root " in text
    assert " · 61% · ide.transcript" in text


def test_induction_attended_under_budget_harvest_not_hop() -> None:
    text = build_wake_induction(
        _digest(
            register="attended",
            budget={
                "stop_class": None,
                "used_tokens": 200_000,
                "window_limit_tokens": 500_000,
                "source": "ide.transcript",
            },
        )
    )
    assert "HARVEST" in text
    assert "HOP " not in text


def test_induction_names_context_budget_and_checkpoint() -> None:
    text = build_wake_induction(
        _digest(
            budget={
                "stop_class": "CONTEXT_BUDGET",
                "used_tokens": 220_000,
                "window_limit_tokens": 256_000,
                "source": "ide.transcript",
                "transcript_id": "f0fbd8f2-305a-48e8-8c61-1004ecfff015",
            },
            checkpoint_due=True,
        )
    )
    # The address is the ide-hop command (10534 #167 named prose and parked instead).
    assert (
        "CONTEXT_BUDGET 86% (ide.transcript) → CHECKPOINT, then liaison-ide-hop.py "
        "--root 10479 --row"
    ) in text
    assert "CHECKPOINT due" in text
    assert text.rstrip().endswith("go-under is overnight/departure only.")
    assert len(text.encode()) <= 700


def test_induction_budget_stop_survives_cap_over_standing_binds() -> None:
    digest = _digest(
        budget={
            "stop_class": "CONTEXT_BUDGET",
            "used_tokens": 254_874,
            "window_limit_tokens": 256_000,
            "source": "ide.transcript",
            "transcript_id": "f0fbd8f2-305a-48e8-8c61-1004ecfff015",
        },
        attention=[
            {"id": str(n), "unread": 2, "last_subject": "cursor-sdk CLOSEOUT " * 3}
            for n in range(10590, 10596)
        ],
    )
    digest["policy"]["induction_binds"] = ["a long standing bind " * 8, "another " * 20]
    text = build_wake_induction(digest)
    assert len(text.encode()) <= 700
    assert "liaison-ide-hop.py --root 10479 --row" in text
    assert "Standing: register=" in text and "a long standing bind" not in text


def test_induction_now_row_is_a_dispatch_not_a_note() -> None:
    text = build_wake_induction(
        _digest(attention=[{"id": "10589", "unread": 1, "last_subject": "R11 G3"}])
    )
    assert "OPERATOR_GATE" in text


def test_induction_headless_budget_keeps_release_step() -> None:
    text = build_wake_induction(
        _digest(
            budget={
                "stop_class": "CONTEXT_BUDGET",
                "used_tokens": 600_000,
                "window_limit_tokens": 700_000,
                "source": "giw.sdk_stream",
            }
        )
    )
    assert "(giw.sdk_stream) → CHECKPOINT, release the seat; the ticker spawns" in text
    assert "--go-under" not in text


def test_induction_empty_now_is_not_a_stop() -> None:
    text = build_wake_induction(_digest(summary_row=None))
    assert "empty NOW is not a stop" in text


def test_induction_now_from_policy_bind() -> None:
    text = build_wake_induction(
        _digest(summary_row=None, policy={"now_row": "R10 wake induction transport"})
    )
    assert (
        "NOW: tip turn #214 on agent-bus:10479 · R10 wake induction transport" in text
    )


def test_induction_attention_child_now_when_bind_empty() -> None:
    """11667#51 specimen — commissioned child in attention fills empty NOW."""
    digest = _digest(
        summary_row=None,
        policy={},
        attention=[
            {
                "id": "11693",
                "lane_role": "sub_mission",
                "lifecycle": "admitted",
                "last_subject": "COMMISSION — OLN lane status/debrief harness",
                "updated_at": "2026-09-18T16:00:00Z",
            }
        ],
    )
    text = build_wake_induction(digest)
    assert (
        "NOW: agent-bus:11693 · «COMMISSION — OLN lane status/debrief harness»"
        in text
    )


def test_induction_judgment_outranks_policy_now_row() -> None:
    """AC-1: unread cdp reply must beat stale policy.now_row."""
    digest = _digest(
        policy={"now_row": "a:35559 stale bind"},
        root={
            "id": "10479",
            "turns": 214,
            "last_subject": "CHECKPOINT 10479 54b93098",
            "unread_turns": [
                {
                    "turn_number": 36,
                    "from": "web-anthropic",
                    "subject": "cdp reply — abc",
                    "read_at": None,
                    "status": "open",
                    "thread": "10479",
                }
            ],
        },
    )
    text = build_wake_induction(digest)
    assert "NOW: 10479#36 — cdp reply" in text
    assert "a:35559" not in text


def test_induction_policy_now_row_wins_over_stale_summary_row() -> None:
    text = build_wake_induction(
        _digest(
            summary_row="IN FLIGHT lane 11364 stale prose",
            policy={"now_row": "POST-LAND tail — close todo"},
        )
    )
    assert "POST-LAND tail" in text
    assert "IN FLIGHT lane 11364" not in text
    assert "tip turn #214 on agent-bus:10479" in text


def test_induction_cse_fire_keeps_skill_activation_when_summary_row_stale() -> None:
    """11367#7: long stale summary_row must not eat cap and drop skill activation."""
    stale = "IN FLIGHT " + ("lane 11364 " * 40)
    cse = build_wake_induction(
        _digest(
            summary_row=stale,
            policy={"now_row": "close todo"},
        ),
        surface="cse",
    )
    assert "Use the liaison skill" not in cse
    assert "<skills_inline>" in cse
    assert 'slug="liaison"' in cse
    assert "harvests → folds → decides → dispatches → checkpoints → hops" in cse
    assert "Use the reasoning-posture skill" in cse
    assert "IN FLIGHT lane 11364" not in cse
    assert "close todo" in cse
    assert len(cse.encode("utf-8")) <= INDUCTION_CAP


def test_induction_quiet_form() -> None:
    text = build_wake_induction(
        _digest(
            attention=[], watchers_complete_unrelayed=[], changed_since_last_tick=False
        )
    )
    assert text.startswith("QUIET 10479")
    assert "one line, end turn" in text


def test_induction_respects_cap() -> None:
    lanes = [
        {"id": str(20000 + i), "unread": 3, "last_subject": "x" * 56} for i in range(12)
    ]
    text = build_wake_induction(_digest(attention=lanes), cap=460)
    assert len(text.encode("utf-8")) <= 460
    assert text.startswith("WAKE 10479")
    assert "NOW:" in text


def test_induction_ide_surface_unchanged() -> None:
    digest = _digest()
    default_text = build_wake_induction(digest)
    ide_text = build_wake_induction(digest, surface="ide")
    assert default_text == ide_text
    assert "Loaded already (do not re-read):" in default_text
    assert "Use the liaison skill" not in default_text


def test_induction_cse_surface_use_lines() -> None:
    text = build_wake_induction(_digest())
    cse = build_wake_induction(_digest(), surface="cse")
    assert "Use the liaison skill" not in cse
    assert "<skills_inline>" in cse
    assert 'slug="liaison"' in cse
    assert "harvests → folds → decides → dispatches → checkpoints → hops" in cse
    assert "Use the reasoning-posture skill" in cse
    assert "Use the git-posture skill" not in cse
    assert "Loaded already (do not re-read)" not in cse
    assert cse != text


def test_induction_cse_use_lines_are_bare_slugs() -> None:
    """claude.ai mounts a body only on an exact slug — a label leaks and mounts nothing."""
    cse = build_wake_induction(
        _digest(
            policy={"induction_loaded": ["liaison skill", "git-posture § Land", "`fs`"]}
        ),
        surface="cse",
    )
    assert "Use the liaison skill skill" not in cse
    assert " § " not in cse
    assert "Use the fs skill" in cse
    assert "Use the liaison skill" not in cse
    assert "<skills_inline>" in cse
    assert 'slug="liaison"' in cse
    assert "harvests → folds → decides → dispatches → checkpoints → hops" in cse


def test_induction_cse_liaison_cursor_only_inlines_sot_body_in_skills_inline() -> None:
    """``liaison`` is ``cursor_only`` — SOT body in ``<skills_inline>``, not Use-the."""
    cse = build_wake_induction(_digest(policy={}), surface="cse")
    assert "Use the liaison skill" not in cse
    assert "<skills_inline>" in cse
    assert 'slug="liaison"' in cse
    assert "harvests → folds → decides → dispatches → checkpoints → hops" in cse
    assert "Use the reasoning-posture skill" in cse


def test_induction_loaded_fence_matches_navigator_skills_policy() -> None:
    """Loaded fence must mirror navigator_doorbell_skills_from_policy (delivery list)."""
    policy = {
        "navigator_skills": [
            "liaison",
            "reasoning-posture",
            "architecture-invariants",
            "ulg-architecture",
        ],
        "induction_loaded": ["git-posture § Land"],
    }
    text = build_wake_induction(_digest(policy=policy))
    assert (
        "Loaded already (do not re-read): liaison skill · reasoning-posture skill · "
        "architecture-invariants skill · ulg-architecture skill · git-posture § Land"
        in text
    )
    cse = build_wake_induction(_digest(policy=policy), surface="cse", cap=1200)
    assert "Use the architecture-invariants skill" not in cse
    assert "Use the ulg-architecture skill" not in cse
    assert "<skills_inline>" in cse
    assert 'slug="liaison"' in cse
    assert 'slug="architecture-invariants"' in cse
    assert 'slug="ulg-architecture"' in cse
    assert "harvests → folds → decides → dispatches → checkpoints → hops" in cse
    assert "ULG Architecture" in cse
    assert "Use the reasoning-posture skill" in cse


def test_induction_cse_cap_trims_use_lines_last() -> None:
    digest = _digest(
        policy={
            "induction_binds": ["a long standing bind " * 8, "another " * 20],
            "induction_loaded": ["git-posture § Land", "checkpoint-discipline"],
        },
        attention=[
            {"id": str(n), "unread": 2, "last_subject": "cursor-sdk CLOSEOUT " * 3}
            for n in range(10590, 10596)
        ],
    )
    text = build_wake_induction(digest, surface="cse", cap=700)
    assert len(text.encode("utf-8")) <= 700
    assert "Standing: register=" in text
    assert "Use the reasoning-posture skill" in text
    assert "<skills_inline>" in text
    assert 'slug="liaison"' in text
    assert CSE_INLINE_TRUNCATION_MARKER in text


def test_induction_now_from_ticker_bind_after_persist(tmp_path) -> None:  # noqa: ANN001
    from bus_watch.now_row import format_now_line
    from bus_watch.now_row_bind import maybe_bind_now_row

    lane_id = "12045"
    subject = "G3 generate admitted"
    row = f"agent-bus:{lane_id} · «{subject}»"
    digest = {
        "ts": "2026-09-21T10:00:00Z",
        "root": {"id": "12043", "turns": 33, "last_subject": "CHECKPOINT 12043"},
        "register": "autonomous",
        "attention": [
            {
                "id": lane_id,
                "lane_role": "sub_mission",
                "unread": 1,
                "last_subject": subject,
                "updated_at": "2026-09-21T09:00:00Z",
            }
        ],
        "lanes": [
            {
                "id": lane_id,
                "lane_role": "sub_mission",
                "unread": 1,
                "last_subject": subject,
                "updated_at": "2026-09-21T09:00:00Z",
                "status": "active",
                "lifecycle": "admitted",
            }
        ],
        "policy": {},
        "policy_entity_cache": {},
        "summary_row": "",
        "watchers_complete_unrelayed": [],
        "budget": {"stop_class": None},
        "checkpoint_due": False,
        "changed_since_last_tick": True,
    }
    state: dict = {"policy": {}}
    state_path = tmp_path / "tick.json"
    state_path.write_text("{}", encoding="utf-8")
    maybe_bind_now_row(digest, state, state_path, as_of="2026-09-21T10:00:00Z")
    text = build_wake_induction(digest)
    expected = format_now_line(row, "policy", digest)
    assert f"NOW: {expected}" in text


def test_induction_spawn_signal_survives_cap() -> None:
    digest = _digest(
        spawn_signal_sources=["actionable_attention", "checkpoint_due"],
        checkpoint_due=True,
        policy={
            "induction_binds": ["a long standing bind " * 8, "another " * 20],
            "induction_loaded": ["git-posture § Land"],
        },
        attention=[
            {"id": str(n), "unread": 2, "last_subject": "cursor-sdk CLOSEOUT " * 3}
            for n in range(10590, 10596)
        ],
    )
    text = build_wake_induction(digest, cap=700)
    assert "Event: wake actionable_attention,checkpoint_due" in text
    assert len(text.encode("utf-8")) <= 700
