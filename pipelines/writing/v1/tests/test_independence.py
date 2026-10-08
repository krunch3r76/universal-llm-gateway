"""Independence tests for writer-specialist v1.

Model ids are injected or read from the local models file. No model is called.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

_PKG = "writing_v1_handlers"
if _PKG not in sys.modules:
    _HANDLERS = Path(__file__).resolve().parents[1] / "handlers"
    _spec = importlib.util.spec_from_file_location(
        _PKG,
        _HANDLERS / "__init__.py",
        submodule_search_locations=[str(_HANDLERS)],
    )
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG] = _mod
    _spec.loader.exec_module(_mod)

independence = importlib.import_module("writing_v1_handlers.independence")

pytestmark = pytest.mark.offline


class _Ctx:
    def __init__(self, **options: object) -> None:
        self.options = options


def test_independence_same_family_refused() -> None:
    handler = independence.WritingIndependenceHandler(
        writer_model="hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid",
        reviewer_model="llama-3-70b",
    )
    payload = asyncio.run(handler.execute(None, _Ctx())).json
    assert payload["refused"] == "independence_violation"
    assert payload["independence"] is None
    assert payload["writer_family"] == "llama"
    assert payload["reviewer_family"] == "llama"


def test_independence_partial_when_allowed() -> None:
    handler = independence.WritingIndependenceHandler(
        writer_model="qwen3-14b-q4-k-m-40960",
        reviewer_model="qwen2-7b",
    )
    payload = asyncio.run(
        handler.execute(None, _Ctx(allow_partial_independence=True))
    ).json
    assert payload["refused"] is None
    assert payload["independence"] == "partial"


@pytest.mark.parametrize(
    ("model_id", "family"),
    [
        ("cdp/opus-5.5", "anthropic"),
        ("claude-opus", "anthropic"),
        ("xai/grok-4", "xai"),
        ("grok-3", "xai"),
        ("cursor/composer-2.5", "cursor"),
        ("hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid", "llama"),
        ("llama-3-8b", "llama"),
        ("qwen3-14b-q4-k-m-40960", "qwen"),
        ("gemma-2-9b", "gemma"),
        ("openai/gpt-4o-mini", "openai"),
        ("gpt-4.1", "openai"),
        ("mystery-model", None),
    ],
)
def test_model_family_table(model_id: str, family: str | None) -> None:
    assert independence.model_family(model_id) == family


def test_shipped_models_are_full_independence() -> None:
    models = independence.load_writing_models()
    assert independence.model_family(models["writer"]) == "llama"
    assert independence.model_family(models["reviewer"]) == "qwen"
    payload = asyncio.run(
        independence.WritingIndependenceHandler().execute(None, _Ctx())
    ).json
    assert payload["independence"] == "full"
    assert payload["refused"] is None
    assert payload["writer_model"] == models["writer"]
    assert payload["reviewer_model"] == models["reviewer"]
