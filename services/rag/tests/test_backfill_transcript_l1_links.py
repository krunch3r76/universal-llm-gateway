"""Tests for transcript L1 backfill script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "rag" / "backfill_transcript_l1_links.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_transcript_l1_links", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_apply_one_calls_execute_op_dict(tmp_path: Path) -> None:
    mod = _load_module()
    source = tmp_path / "cursor-test.source.jsonl"
    source.write_text('{"role":"user","content":"hi"}\n', encoding="utf-8")
    with patch.object(mod, "_files_root", return_value=tmp_path):
        with patch.object(mod, "execute_op") as mock_exec:
            mock_exec.return_value = {"ok": True}
            out = mod._apply_one(source, dry_run=False)
    mock_exec.assert_called_once()
    args = mock_exec.call_args
    assert args[0][0] == "entity_update"
    assert isinstance(args[0][1], dict)
    assert args[0][1]["entity_id"] == "transcript:cursor-test"
    assert args[0][1]["attributes"]["source_jsonl_uri"].startswith("files://")
    assert out["entity_id"] == "transcript:cursor-test"
