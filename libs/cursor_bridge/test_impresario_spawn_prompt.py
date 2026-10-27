"""Offline tests for impresario spawn prompt augmentation."""

from __future__ import annotations

import pytest

from cursor_bridge.impresario_spawn_prompt import (
    IMPRESARIO_SKILL_USE_LINE,
    augment_spawn_prompt_for_impresario,
)

pytestmark = pytest.mark.offline


def test_augment_no_op_without_signal() -> None:
    assert (
        augment_spawn_prompt_for_impresario("hello", slug=None, title=None) == "hello"
    )


def test_augment_prepends_on_impresario_slug() -> None:
    out = augment_spawn_prompt_for_impresario(
        "go", slug="composer-impresario", title=None
    )
    assert out.startswith(IMPRESARIO_SKILL_USE_LINE)
    assert "go" in out


def test_augment_prepends_on_impresario_title() -> None:
    out = augment_spawn_prompt_for_impresario(
        "go", slug=None, title="Composer impresario"
    )
    assert IMPRESARIO_SKILL_USE_LINE in out


def test_augment_idempotent_when_use_line_present() -> None:
    body = f"{IMPRESARIO_SKILL_USE_LINE}\n\nalready"
    assert (
        augment_spawn_prompt_for_impresario(body, slug="x-impresario", title=None)
        == body
    )


@pytest.mark.parametrize(
    "slug",
    [
        "gc1-review-impresario-skill-pulls-238e0cf8",
        "reviewer-impresario-tab",
        "gc1-review-impresario-skill",
    ],
)
def test_augment_no_op_for_reviewish_impresario_slug(slug: str) -> None:
    assert augment_spawn_prompt_for_impresario("go", slug=slug, title=None) == "go"


def test_augment_no_op_for_reviewish_impresario_title() -> None:
    assert (
        augment_spawn_prompt_for_impresario(
            "go",
            slug=None,
            title="Review impresario skill pulls",
        )
        == "go"
    )


@pytest.mark.parametrize("gui_host", [":8"])
def test_spawn_tab_prepends_impresario_skill_when_title_matches(gui_host: str) -> None:
    from unittest.mock import patch

    from tools.cursor_bridge import _spawn_tab

    with patch("cursor_bridge.bridge_spawn_relay.launch_cursor_agents_tab") as launch:
        launch.return_value = {"ok": True, "editor_agent_id": "a-1"}
        _spawn_tab(
            gui_host=gui_host,
            prompt="TYPE: DIRECTIVE",
            model="composer-2.5",
            title="composer impresario",
        )
    sent = launch.call_args.args[0]
    assert IMPRESARIO_SKILL_USE_LINE in sent
