"""AC-P-2: mint-tab-launch emits pool floor + CLOSEOUT field list."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from agent_bus_store.house_pools import parse_closeout_thread_id, parse_pools, pool_status_is_blocked

_SCRIPT_PATH = Path(__file__).resolve().parent / "mint-tab-launch.py"
_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "libs/agent_bus_store/test_house_pools.py"
)


def _load_mint_tab_launch():
    loader = SourceFileLoader("mint_tab_launch", str(_SCRIPT_PATH))
    spec = importlib.util.spec_from_loader("mint_tab_launch", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _manifest_block() -> str:
    loader = SourceFileLoader("test_house_pools_fixture", str(_FIXTURE_PATH))
    spec = importlib.util.spec_from_loader("test_house_pools_fixture", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module._MANIFEST_BLOCK


def _card_text() -> str:
    return f"# card\n\n{_manifest_block()}\n"


@pytest.fixture
def house_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card_path = tmp_path / "notes/system/threads/10223-continuity.md"
    card_path.parent.mkdir(parents=True)
    card_path.write_text(_card_text(), encoding="utf-8")
    return card_path


def test_mint_tab_launch_emits_pool_floor_and_closeout_fields(
    house_card: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC-P-2: skills, reads, closeout shape, forbidden + CLOSEOUT field list."""
    mint = _load_mint_tab_launch()
    output = tmp_path / "tab-launch-W4.md"
    card = house_card.read_text(encoding="utf-8")
    rows = parse_pools(card)
    assert pool_status_is_blocked(rows["conductor"].status)  # fixture carries blocked conductor
    work = rows["work"]

    rc = mint.main(
        [
            "--house",
            "10223",
            "--pool",
            "work",
            "--slice",
            "W4",
            "--output",
            str(output),
            "--slice-body",
            "Implement slice W4.",
        ]
    )
    assert rc == 0
    assert capsys.readouterr().out.strip() == str(output)

    prompt = output.read_text(encoding="utf-8")
    for slug in work.must_load:
        assert f"Use the `{slug}` skill." in prompt
    assert "**Read:**" in prompt
    assert "cortex://notes/system/threads/10223-opportunities.md" in prompt
    assert "**Closeout:**" in prompt
    assert "CLOSEOUT — WORK W4" in prompt
    assert "commit · pytest · files · verdict · bus_posts" in prompt
    assert "**Forbidden:**" in prompt
    assert "posting on agent-bus:10223" in prompt
    coord = parse_closeout_thread_id(work.closeout)
    assert coord is not None
    assert f"agent-bus:{coord}" in prompt
    assert "Implement slice W4." in prompt


def test_mint_tab_launch_missing_card_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    mint = _load_mint_tab_launch()
    rc = mint.main(["--house", "99999", "--slice", "W4"])
    assert rc == 1
    assert "continuity card missing" in capsys.readouterr().err
