"""Unit tests for house_policy read and role override."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.conductor_context import RematerializeContext
from implement_admission.house_testing import HOUSE_MODEL, write_tick


def test_read_tick_policy_binds_and_critic(
    house_watch_dir: Path,
) -> None:
    from implement_admission.house_policy import read_house_policy

    write_tick(
        house_watch_dir,
        "21382",
        {
            "induction_binds": ["NO cdp/*"],
            "critic_model": HOUSE_MODEL,
            "critic_model_knobs": {"effort": "high"},
        },
    )
    hp = read_house_policy("21382", watch_dir=house_watch_dir)
    assert hp.source == "tick"
    assert hp.binds == ("NO cdp/*",)
    assert hp.critic_model == HOUSE_MODEL
    assert hp.critic_model_knobs == {"effort": "high"}


def test_tick_no_policy_key_is_tick_empty(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    write_tick(house_watch_dir, "99", {})
    hp = read_house_policy("99", watch_dir=house_watch_dir)
    assert hp.source == "tick"
    assert hp.binds == ()
    assert hp.critic_model is None


def test_tick_absent_with_id_is_unresolved(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    hp = read_house_policy("missing", watch_dir=house_watch_dir)
    assert hp.source == "unresolved"


def test_no_id_not_unresolved_is_none(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    hp = read_house_policy(None, watch_dir=house_watch_dir)
    assert hp.source == "none"


def test_unresolved_flag_no_id_is_unresolved(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    hp = read_house_policy(
        None,
        summoning_thread_id_unresolved=True,
        watch_dir=house_watch_dir,
    )
    assert hp.source == "unresolved"


def test_carriers_used_when_tick_absent(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    rem = RematerializeContext(
        house_induction_binds=("carrier-bind",),
        house_critic_model=HOUSE_MODEL,
    )
    hp = read_house_policy("tid", rematerialize=rem, watch_dir=house_watch_dir)
    assert hp.source == "carrier"
    assert hp.binds == ("carrier-bind",)


def test_tick_wins_over_carriers(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    write_tick(
        house_watch_dir,
        "win",
        {"induction_binds": ["tick-bind"]},
    )
    rem = RematerializeContext(house_induction_binds=("carrier-bind",))
    hp = read_house_policy("win", rematerialize=rem, watch_dir=house_watch_dir)
    assert hp.source == "tick"
    assert hp.binds == ("tick-bind",)


def test_malformed_binds_treated_empty(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    write_tick(house_watch_dir, "bad", {"induction_binds": ["ok", 1]})
    hp = read_house_policy("bad", watch_dir=house_watch_dir)
    assert hp.binds == ()


def test_non_string_or_empty_critic_model_is_none(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    write_tick(house_watch_dir, "c1", {"critic_model": ""})
    write_tick(house_watch_dir, "c2", {"critic_model": 3})
    assert read_house_policy("c1", watch_dir=house_watch_dir).critic_model is None
    assert read_house_policy("c2", watch_dir=house_watch_dir).critic_model is None


def test_unknown_prefix_critic_model_is_none(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    write_tick(house_watch_dir, "u", {"critic_model": "openai/gpt-4"})
    assert read_house_policy("u", watch_dir=house_watch_dir).critic_model is None


def test_carrier_unknown_prefix_critic_model_is_none(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    rem_only = RematerializeContext(house_critic_model="openai/gpt-4")
    hp = read_house_policy("absent-tick", rematerialize=rem_only, watch_dir=house_watch_dir)
    assert hp.critic_model is None
    assert hp.source == "unresolved"

    rem_bind = RematerializeContext(
        house_induction_binds=("carrier-bind",),
        house_critic_model="openai/gpt-4",
    )
    hp2 = read_house_policy("absent-tick", rematerialize=rem_bind, watch_dir=house_watch_dir)
    assert hp2.source == "carrier"
    assert hp2.binds == ("carrier-bind",)
    assert hp2.critic_model is None


def test_critic_model_knobs_carry_only_when_dict(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    write_tick(house_watch_dir, "k", {"critic_model_knobs": "nope"})
    hp = read_house_policy("k", watch_dir=house_watch_dir)
    assert hp.critic_model_knobs is None


def test_read_house_policy_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from implement_admission import house_policy as hp_mod

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("fail")

    monkeypatch.setattr(hp_mod, "load_state", _boom)
    hp = hp_mod.read_house_policy("x")
    assert hp.source == "unresolved"


def test_with_house_critic_rederives_transport() -> None:
    from implement_admission.house_policy import with_house_critic
    from implement_admission.impresario_policy import resolve_roles

    roles = resolve_roles()
    out = with_house_critic(roles, HOUSE_MODEL)
    assert out.critic.model == HOUSE_MODEL
    assert out.critic.transport == "api"


def test_with_house_critic_noop_when_none() -> None:
    from implement_admission.house_policy import with_house_critic
    from implement_admission.impresario_policy import resolve_roles

    roles = resolve_roles()
    assert with_house_critic(roles, None) is roles


def test_roles_for_ctx_override_and_none() -> None:
    from implement_admission.conductor_context import ConductorMaterializeContext
    from implement_admission.conductor_score_journal import G_ROWS
    from implement_admission.house_policy import roles_for_ctx

    ctx_none = ConductorMaterializeContext(
        source_ref="todo:x",
        slug="x",
        name="n",
        density_triage=None,
        derived_from=None,
        stop_after=None,
        entry_gate="G1",
        summon_mode="confer_and_finish",
        problem=None,
        scope=None,
        acceptance=None,
    )
    roles_for_ctx(ctx_none)
    ctx_h = ConductorMaterializeContext(
        source_ref="todo:x",
        slug="x",
        name="n",
        density_triage=None,
        derived_from=None,
        stop_after=None,
        entry_gate="G1",
        summon_mode="confer_and_finish",
        problem=None,
        scope=None,
        acceptance=None,
        house_critic_model=HOUSE_MODEL,
        rows=G_ROWS,
    )
    r1 = roles_for_ctx(ctx_h)
    assert r1.critic.model == HOUSE_MODEL


def test_as_of_is_tick_mtime(house_watch_dir: Path) -> None:
    from implement_admission.house_policy import read_house_policy

    write_tick(house_watch_dir, "mtime", {"induction_binds": []})
    hp = read_house_policy("mtime", watch_dir=house_watch_dir)
    assert hp.as_of is not None
    assert hp.as_of.endswith("Z")


def test_watch_dir_isolation_active_libs(tmp_path: Path) -> None:
    import bus_watch.fable_lock

    assert str(bus_watch.fable_lock.WATCH_DIR).startswith(str(tmp_path))
