"""fetch_continuity reads # Current from the continuity doc in a dual-file house."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from .fetch_continuity import MaestroInductFetchContinuityHandler

pytestmark = pytest.mark.asyncio


async def test_dual_file_house_reads_current_from_continuity_md(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    threads = tmp_path / "notes/system/threads"
    threads.mkdir(parents=True)
    (threads / "12286-card.md").write_text(
        "# live\n**Settled:** card row\n", encoding="utf-8"
    )
    (threads / "12286-continuity.md").write_text(
        "# Current\n## Settled\nfrom the archive\n", encoding="utf-8"
    )
    out = await MaestroInductFetchContinuityHandler().execute(
        None, SimpleNamespace(outputs={"resolve": {"root": "12286"}})
    )
    assert out.json.get("settled") == "from the archive"
    assert out.json.get("error", {}).get("kind") != "continuity_current_missing"
