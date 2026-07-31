"""Body resolve: catalog URI is a hint, not a pre-_resolve_skill_file gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.skill_catalog_resolver import SkillCatalogResolveError


@pytest.mark.offline
def test_body_resolve_survives_uri_miss_via_file_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from implement_admission import skill_body_resolve as mod

    body_path = tmp_path / "path-sim.md"
    body_path.write_text("# path-sim\nfallback body\n", encoding="utf-8")

    def _boom(_slug: str) -> str:
        raise SkillCatalogResolveError("forced uri miss")

    monkeypatch.setattr(mod, "resolve_canonical_source_uri", _boom)
    monkeypatch.setattr(mod, "_lookup_entity_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "cortex_store.routes.boot._skill_trigger._resolve_skill_file",
        lambda _uri, _slug: body_path,
    )
    monkeypatch.setattr(
        "cortex_store.db.cortex_conn",
        lambda: type("C", (), {"close": lambda self: None})(),
    )

    payload, reason = mod.resolve_skill_body_from_catalog("path-sim")
    assert reason is None
    assert payload is not None
    assert "fallback body" in (payload.get("body") or "")
