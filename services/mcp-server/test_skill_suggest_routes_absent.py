"""Route absence tests for dormant skill-suggest HTTP entry points."""

from __future__ import annotations

from pathlib import Path


def test_cortex_skills_suggest_route_unregistered() -> None:
    from cortex_store.routes.skills import post_skill_suggest, router

    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/suggest" not in paths
    assert "/skills/suggest" not in paths
    # Implementation retained but unreachable via FastAPI routing.
    assert callable(post_skill_suggest)


def test_stargate_suggest_dispatch_router_unmounted() -> None:
    app_path = (
        Path(__file__).resolve().parents[1]
        / "universal-stargate"
        / "systems"
        / "proxy"
        / "app.py"
    )
    source = app_path.read_text(encoding="utf-8")
    assert "frontier_consult_skills_router" not in source
    assert "suggest-dispatch" not in source
    assert "skill_suggest_dispatch" not in source

    # Router object still exists on the dormant module for later redesign.
    from systems.frontier_consult.skill_suggest_dispatch import skills_router

    paths = {getattr(route, "path", None) for route in skills_router.routes}
    assert "/api/v1/skills/suggest-dispatch" in paths
