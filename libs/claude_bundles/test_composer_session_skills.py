"""Unit tests for composer + → Skills attach heuristics."""

from __future__ import annotations

import pytest

from claude_bundles.composer_session_skills import (
    _SKILLS_ITEM,
    _is_usable_plus_candidate,
    _score_plus_candidate,
)

pytestmark = pytest.mark.offline


def test_scheduled_task_sidebar_row_rejected() -> None:
    row = {
        "tag": "A",
        "aria": "",
        "text": "Chase EO follow-up call — Monday One-tim",
        "title": "",
        "testid": "",
        "near": True,
        "svg": False,
        "x": 14,
        "y": 200,
        "href": "/scheduled-task/trig_abc",
    }
    assert _score_plus_candidate(row) < 0
    assert not _is_usable_plus_candidate(row)


def test_composer_textbox_not_plus() -> None:
    row = {
        "tag": "DIV",
        "aria": "Write your prompt to Claude",
        "text": "",
        "testid": "chat-input",
        "near": True,
        "svg": False,
        "x": 473,
        "y": 308,
    }
    assert not _is_usable_plus_candidate(row)


def test_surface_mode_chip_not_plus() -> None:
    row = {
        "tag": "BUTTON",
        "aria": "Surface",
        "text": "Chat Cowork",
        "near": True,
        "svg": False,
        "x": 505,
        "y": 366,
    }
    assert not _is_usable_plus_candidate(row)


def test_icon_only_near_composer_scores_high() -> None:
    row = {
        "tag": "BUTTON",
        "aria": "",
        "text": "",
        "title": "",
        "testid": "",
        "near": True,
        "h_near": True,
        "svg": True,
        "haspopup": "menu",
        "x": 520,
        "y": 360,
    }
    assert _is_usable_plus_candidate(row)
    assert _score_plus_candidate(row) >= 30


def test_skills_submenu_chevron_matches() -> None:
    assert _SKILLS_ITEM.search("Skills \ue02a")
    assert not _SKILLS_ITEM.search("Add skills plugin")


def test_add_files_connectors_menu_scores_high() -> None:
    row = {
        "tag": "BUTTON",
        "aria": "Add files, connectors, and more",
        "text": "\ue001",
        "near": True,
        "h_near": True,
        "haspopup": "menu",
        "x": 469,
        "y": 364,
    }
    assert _is_usable_plus_candidate(row)
    assert _score_plus_candidate(row) >= 30


def test_add_attachment_aria_scores_high() -> None:
    row = {
        "tag": "BUTTON",
        "aria": "Add attachment",
        "text": "",
        "near": True,
        "h_near": True,
        "svg": True,
        "haspopup": "true",
        "x": 490,
        "y": 365,
    }
    assert _is_usable_plus_candidate(row)
    assert _score_plus_candidate(row) >= 40
