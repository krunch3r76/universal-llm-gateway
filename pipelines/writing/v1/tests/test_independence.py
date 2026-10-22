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


class _Out:
    def __init__(self, payload: dict, model_id: str | None = None) -> None:
        self.json = payload
        self.model_id = model_id


class _Ctx:
    def __init__(self, outputs: dict | None = None, **options: object) -> None:
        self.options = options
        self.outputs = outputs or {}


def test_independence_same_family_uses_review_fallback() -> None:
    handler = independence.WritingIndependenceHandler(
        writer_model="hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid",
        reviewer_model="llama-3-70b",
    )
    payload = asyncio.run(handler.execute(None, _Ctx())).json
    assert payload["refused"] is None
    assert payload["independence"] == "full"
    assert payload["writer_family"] == "llama"
    assert payload["reviewer_family"] != payload["writer_family"]
    assert payload["reviewer_model"] == "openai/gpt-5.4-mini"


def test_independence_override_reviewer_same_family_uses_fallback() -> None:
    qwen = "qwen3-5-9b-q8-0-131072"
    handler = independence.WritingIndependenceHandler(writer_model=qwen)
    payload = asyncio.run(
        handler.execute(None, _Ctx(model_ref_overrides={"reviewer": qwen}))
    ).json
    assert payload["refused"] is None
    assert payload["independence"] == "full"
    assert payload["reviewer_model"] == "openai/gpt-5.4-mini"
    assert payload["reviewer_family"] == "openai"


def test_independence_qwen_drafter_local_ernie_uses_openai_fallback() -> None:
    qwen = "qwen3-5-9b-q8-0-131072"
    payload = asyncio.run(
        independence.WritingIndependenceHandler().execute(
            None,
            _Ctx(
                outputs={
                    "draft": _Out(
                        {
                            "selected_step": "drafter_local_ernie",
                            "draft": "Sample",
                        },
                        model_id=qwen,
                    ),
                    "drafter_local_ernie": _Out({"draft": "Sample"}, model_id=qwen),
                }
            ),
        )
    ).json
    assert payload["refused"] is None
    assert payload["writer_model"] == qwen
    assert payload["reviewer_model"] == "openai/gpt-5.4-mini"


def test_independence_partial_when_allowed_and_no_fallback() -> None:
    handler = independence.WritingIndependenceHandler(
        writer_model="qwen3-14b-q4-k-m-40960",
        reviewer_model="qwen2-7b",
    )
    payload = asyncio.run(
        handler.execute(
            None,
            _Ctx(allow_partial_independence=True),
        )
    ).json
    assert payload["refused"] is None
    assert payload["independence"] in {"partial", "full"}


@pytest.mark.parametrize(
    ("model_id", "family"),
    [
        ("cdp/opus-5.5", "anthropic"),
        ("claude-opus", "anthropic"),
        ("xai/grok-4", "xai"),
        ("grok-3", "xai"),
        ("cursor/composer-2.5", "cursor"),
        ("hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid", "llama"),
        ("hermes-4-14b-qwen3", "qwen"),
        ("hermes-4-14b", None),
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
    served = "qwen3-5-9b-q8-0-131072"
    models = independence.load_writing_models()
    assert models["writer"] == served
    assert models["reviser"] == served
    assert models["reviewer"] == served
    assert independence.model_family(models["writer"]) == "qwen"
    assert independence.model_family(models["reviewer"]) == "qwen"
    # Live runs bind writer from the selected cloud drafter, not models.yaml writer.
    payload = asyncio.run(
        independence.WritingIndependenceHandler().execute(
            None,
            _Ctx(
                outputs={
                    "draft": _Out({"selected_step": "drafter_openai"}),
                    "drafter_openai": _Out({}, model_id="openai/gpt-5.4"),
                }
            ),
        )
    ).json
    assert payload["independence"] == "full"
    assert payload["refused"] is None
    assert payload["writer_model"] == "openai/gpt-5.4"
    assert payload["reviewer_model"] == models["reviewer"]


def test_independence_cdp_writer_local_reviewer_full() -> None:
    qwen = "qwen3-14b-q4-k-m-40960"
    payload = asyncio.run(
        independence.WritingIndependenceHandler().execute(
            None,
            _Ctx(
                outputs={"draft": _Out({}, model_id="cdp/opus-5.5")},
                model_ref_overrides={"reviewer": qwen},
            ),
        )
    ).json
    assert payload["independence"] == "full"
    assert payload["writer_model"] == "cdp/opus-5.5"
    assert payload["reviewer_model"] == qwen


def test_independence_cdp_reviewer_local_writer_full() -> None:
    hermes = "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
    payload = asyncio.run(
        independence.WritingIndependenceHandler().execute(
            None,
            _Ctx(
                outputs={"assemble": _Out({"reviewer_seat": "cdp"})},
                seat_models={"cdp": "cdp/opus-5.5"},
                model_ref_overrides={"writer": hermes},
            ),
        )
    ).json
    assert payload["independence"] == "full"
    assert payload["writer_model"] == hermes
    assert payload["reviewer_model"] == "cdp/opus-5.5"


def test_anthropic_writer_auto_resolves_openai_gpt5_reviewer() -> None:
    payload = asyncio.run(
        independence.WritingIndependenceHandler().execute(
            None,
            _Ctx(
                outputs={
                    "draft": _Out({}, model_id="anthropic/claude-sonnet-5-5"),
                    "assemble": _Out({"reviewer_seat": "openai"}),
                },
                seat_models={"openai": "openai/gpt-5.4"},
            ),
        )
    ).json
    assert payload["refused"] is None
    assert payload["independence"] == "full"
    assert payload["writer_family"] == "anthropic"
    assert payload["reviewer_family"] == "openai"
    assert payload["reviewer_model"] == "openai/gpt-5.4"


def test_anthropic_writer_cdp_reviewer_uses_openai_fallback() -> None:
    payload = asyncio.run(
        independence.WritingIndependenceHandler().execute(
            None,
            _Ctx(
                outputs={
                    "draft": _Out({}, model_id="anthropic/claude-haiku-5-5"),
                    "assemble": _Out({"reviewer_seat": "cdp"}),
                },
                seat_models={"cdp": "cdp/opus-5.5"},
            ),
        )
    ).json
    assert payload["refused"] is None
    assert payload["reviewer_model"] == "openai/gpt-5.4-mini"


def test_independence_cdp_writer_and_reviewer_uses_openai_fallback() -> None:
    payload = asyncio.run(
        independence.WritingIndependenceHandler().execute(
            None,
            _Ctx(
                outputs={
                    "draft": _Out({}, model_id="cdp/opus-5.5"),
                    "assemble": _Out({"reviewer_seat": "cdp"}),
                },
                seat_models={"cdp": "cdp/opus-5.5"},
            ),
        )
    ).json
    assert payload["refused"] is None
    assert payload["reviewer_model"] == "openai/gpt-5.4-mini"


def test_independence_openai_writer_skips_same_family_reviewer_fallback() -> None:
    payload = asyncio.run(
        independence.WritingIndependenceHandler(
            writer_model="openai/gpt-5.4",
            reviewer_model="openai/gpt-5.4-mini",
        ).execute(None, _Ctx())
    ).json
    assert payload["refused"] is None
    assert payload["reviewer_model"] == "deepseek-r1-distill-qwen-14b-q8-0-65536"
