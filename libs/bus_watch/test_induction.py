"""Wake induction: planted addresses, episodic frame, cap, quiet form."""

from __future__ import annotations

from bus_watch.induction import INDUCTION_CAP, build_wake_induction


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
    assert (
        "NOW: tip #214 on agent-bus:10479 · «CHECKPOINT 10479 54b93098»" in text
    )
    assert "do not re-read): liaison skill · git-posture § Land" in text
    assert "register=autonomous · hopper paused (10479#210)" in text
    assert "cdp/opus-5 first" in text
    assert len(text.encode("utf-8")) <= INDUCTION_CAP


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
    assert text.rstrip().endswith("STAY only when NOW is empty; end turn.")
    assert "cdp/opus-5 first" in text


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
    assert "NOW: tip #214 on agent-bus:10479 · R10 wake induction transport" in text


def test_induction_policy_now_row_wins_over_stale_summary_row() -> None:
    text = build_wake_induction(
        _digest(
            summary_row="IN FLIGHT lane 11364 stale prose",
            policy={"now_row": "POST-LAND tail — close todo"},
        )
    )
    assert "POST-LAND tail" in text
    assert "IN FLIGHT lane 11364" not in text
    assert "tip #214 on agent-bus:10479" in text


def test_induction_cse_fire_keeps_use_lines_when_summary_row_stale() -> None:
    """11367#7: long stale summary_row must not eat cap and drop skill activation."""
    stale = "IN FLIGHT " + ("lane 11364 " * 40)
    cse = build_wake_induction(
        _digest(
            summary_row=stale,
            policy={"now_row": "close todo"},
        ),
        surface="cse",
    )
    assert "Use the liaison skill" in cse
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
    text = build_wake_induction(_digest(attention=lanes), cap=400)
    assert len(text.encode("utf-8")) <= 400
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
    assert "Use the liaison skill" in cse
    assert "Use the git-posture skill" in cse
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
    # "liaison skill" prepended by the builder and listed in policy is one slug.
    assert cse.count("Use the liaison skill") == 1


def test_induction_cse_always_includes_liaison_skill() -> None:
    cse = build_wake_induction(_digest(policy={}), surface="cse")
    assert "Use the liaison skill" in cse
    assert cse.count("Use the ") >= 1


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
    assert "Use the liaison skill" in text
