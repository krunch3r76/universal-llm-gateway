"""Finalize-envelope tests for writer-specialist v1.

Step outputs are in-memory fakes. Rate rows are the local seed file.
No service is contacted.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

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

finalize = importlib.import_module("writing_v1_handlers.finalize")

pytestmark = pytest.mark.offline

_HERMES = "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
_QWEN = "qwen3-14b-q4-k-m-40960"


class _Out:
    def __init__(
        self,
        payload: dict[str, Any] | None,
        *,
        error: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model_id: str | None = None,
        model_call_count: int = 0,
    ) -> None:
        self.json = payload
        self.error = error
        self.raw = ""
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.model_id = model_id
        self.model_call_count = model_call_count


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


def _assemble(**extra: Any) -> dict[str, Any]:
    payload = {
        "ok": True,
        "refused": None,
        "pin_ledger": [{"pin_id": "P1", "attested": True}],
        "packet": "<documents>\npins\n</documents>",
        "writer_seat": "local",
        "reviewer_seat": "local",
        "output": "envelope",
    }
    payload.update(extra)
    return payload


def _draft() -> dict[str, Any]:
    return {"draft": "Backed.", "claims": [], "need": [], "dispositions": []}


def _run(outputs: dict[str, Any]) -> dict[str, Any]:
    payload = asyncio.run(
        finalize.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload["unsent"] is True
    return payload


def test_finalize_cost_resolves_rate_alias() -> None:
    outputs = {
        "draft": _Out(
            _draft(),
            model_id="composer-2.5",
            model_call_count=1,
        )
    }
    total, note = finalize._cost(
        outputs,
        {"draft": {"prompt_tokens": 1_000_000, "completion_tokens": 0}},
        {"writer": "unused"},
    )
    assert note is None
    assert total == 0.5


def test_finalize_partial_review_failure() -> None:
    payload = _run(
        {
            "assemble": _Out(_assemble()),
            "draft": _Out(
                _draft(),
                prompt_tokens=12,
                completion_tokens=4,
                model_id=_HERMES,
                model_call_count=1,
            ),
            "provenance_check": _Out({"ok": True, "violations": [], "pass": True}),
            "independence": _Out(
                {
                    "refused": None,
                    "independence": "full",
                    "writer_model": _HERMES,
                    "reviewer_model": _QWEN,
                }
            ),
            "review": _Out(None, error="model_down"),
        }
    )
    assert payload["review"]["status"] == "failed"
    assert payload["revision"] is None
    assert payload["ship_gate"]["pass"] is False
    assert payload["draft_v1"]["draft"] == "Backed."
    assert "pin_ledger" in payload["provenance"]
    assert payload["est_cost_usd"] == 0.0


def test_finalize_revision_happy_path() -> None:
    payload = _run(
        {
            "assemble": _Out(_assemble()),
            "draft": _Out(
                _draft(),
                prompt_tokens=20,
                completion_tokens=10,
                model_id=_HERMES,
                model_call_count=1,
            ),
            "provenance_check": _Out(
                {
                    "violations": [{"type": "em_dash", "claim_id": None, "detail": 1}],
                    "pass": False,
                }
            ),
            "independence": _Out({"refused": None, "independence": "full"}),
            "review": _Out(
                {
                    "findings": [{"ref": "s1", "type": "register", "fix": "shorter"}],
                    "verdict": "revise",
                },
                prompt_tokens=8,
                completion_tokens=4,
                model_id=_QWEN,
                model_call_count=1,
            ),
            "revise": _Out(
                {
                    "draft": "Shorter.",
                    "changed_claims": ["c1"],
                    "dispositions": ["express"],
                },
                prompt_tokens=15,
                completion_tokens=6,
                model_id=_HERMES,
                model_call_count=1,
            ),
            "provenance_check_final": _Out({"violations": [], "pass": True}),
        }
    )
    assert payload["ship_gate"]["pass"] is True
    assert payload["ship_gate"]["unresolved"] == []
    assert payload["revision"]["draft_v2"] == "Shorter."
    assert payload["revision"]["changed_claims"] == ["c1"]
    assert payload["provenance"]["final"] == []
    assert payload["seats_used"]["writer"] == _HERMES
    assert payload["seats_used"]["reviewer"] == _QWEN
    assert payload["seats_used"]["reviser"] == _HERMES
    assert payload["est_cost_usd"] == 0.0
    assert payload["review"]["status"] == "ok"


def _skipped_revise_outputs(
    verdict: str, findings: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "assemble": _Out(_assemble()),
        "draft": _Out(_draft(), model_id=_HERMES, model_call_count=1),
        "provenance_check": _Out({"violations": [], "pass": True}),
        "independence": _Out({"refused": None, "independence": "full"}),
        "review": _Out(
            {"findings": findings, "verdict": verdict},
            model_id=_QWEN,
            model_call_count=1,
        ),
        "revise": _Out({"_skipped": True}),
        "provenance_check_final": _Out({"_skipped": True}),
    }


def test_finalize_skipped_revise_on_ship() -> None:
    payload = _run(_skipped_revise_outputs("ship", []))
    assert payload["revision"] is None
    assert payload["provenance"]["final"] is None
    assert payload["seats_used"]["reviser"] is None
    assert payload["ship_gate"]["pass"] is True


def test_finalize_skipped_revise_on_revise_verdict() -> None:
    findings = [{"ref": "s1", "type": "omission", "fix": "add the date"}]
    payload = _run(_skipped_revise_outputs("revise", findings))
    assert payload["ship_gate"]["pass"] is False
    assert findings[0] in payload["ship_gate"]["unresolved"]


def test_finalize_live_reviewer_same_family_fails_gate() -> None:
    payload = _run(
        {
            "assemble": _Out(_assemble()),
            "draft": _Out(_draft(), model_id=_HERMES, model_call_count=1),
            "provenance_check": _Out({"violations": [], "pass": True}),
            "independence": _Out({"refused": None, "independence": "full"}),
            "review": _Out(
                {"findings": [], "verdict": "ship"},
                model_id=_HERMES,
                model_call_count=1,
            ),
        }
    )
    assert payload["ship_gate"]["pass"] is False
    assert payload["seats_used"]["reviewer"] == _HERMES
    assert payload["review"]["independence"] is None
    assert {
        "type": "independence_mismatch",
        "claim_id": None,
        "detail": f"{_HERMES}->{_HERMES}",
    } in payload["ship_gate"]["unresolved"]


def test_finalize_ship_without_revision() -> None:
    payload = _run(
        {
            "assemble": _Out(_assemble()),
            "draft": _Out(
                _draft(),
                prompt_tokens=5,
                completion_tokens=2,
                model_id=_HERMES,
                model_call_count=1,
            ),
            "provenance_check": _Out({"violations": [], "pass": True}),
            "independence": _Out({"refused": None, "independence": "full"}),
            "review": _Out(
                {"findings": [], "verdict": "ship"},
                prompt_tokens=3,
                completion_tokens=1,
                model_id=_QWEN,
                model_call_count=1,
            ),
        }
    )
    assert payload["ship_gate"]["pass"] is True
    assert payload["revision"] is None
    assert payload["provenance"]["final"] is None
    assert payload["seats_used"]["reviser"] is None
    assert payload["est_cost_usd"] == 0.0


def test_finalize_refused_envelope() -> None:
    payload = _run(
        {
            "assemble": _Out(
                {"ok": False, "refused": "brief_invalid", "error": "missing: signer"}
            )
        }
    )
    assert payload["refused"] == "brief_invalid"
    assert payload["error"] == "missing: signer"
    assert "draft_v1" not in payload


def test_finalize_packet_envelope() -> None:
    payload = _run(
        {
            "assemble": _Out(
                _assemble(
                    output="packet", packet="RENDERED", pin_ledger=[{"pin_id": "P1"}]
                )
            )
        }
    )
    assert payload["output"] == "packet"
    assert payload["packet"] == "RENDERED"
    assert payload["pin_ledger"] == [{"pin_id": "P1"}]


def test_finalize_independence_refused() -> None:
    payload = _run(
        {
            "assemble": _Out(_assemble()),
            "draft": _Out(_draft()),
            "provenance_check": _Out({"violations": [], "pass": True}),
            "independence": _Out(
                {"refused": "independence_violation", "independence": None}
            ),
        }
    )
    assert payload["refused"] == "independence_violation"
    assert payload["draft_v1"]["draft"] == "Backed."
    assert payload["provenance"] == {"initial": []}


def test_finalize_seat_fallback_null_and_present() -> None:
    quiet = _run(
        {
            "assemble": _Out(_assemble()),
            "draft": _Out(_draft(), model_id=_HERMES),
            "provenance_check": _Out({"violations": [], "pass": True}),
            "independence": _Out({"refused": None, "independence": "full"}),
            "review": _Out({"findings": [], "verdict": "ship"}, model_id=_QWEN),
        }
    )
    assert quiet["seat_fallback"] == {"draft": None, "review": None}
    record = {"seat": "cdp", "role": "writer", "reason": "timeout"}
    flagged = _run(
        {
            "assemble": _Out(_assemble()),
            "draft": _Out({**_draft(), "seat_fallback": record}, model_id=_HERMES),
            "provenance_check": _Out({"violations": [], "pass": True}),
            "independence": _Out({"refused": None, "independence": "full"}),
            "review": _Out(
                {"findings": [], "verdict": "ship", "seat_fallback": None},
                model_id=_QWEN,
            ),
        }
    )
    assert flagged["seat_fallback"]["draft"] == record
    assert flagged["seat_fallback"]["review"] is None


def test_finalize_seats_used_writer_is_cdp_model() -> None:
    payload = _run(
        {
            "assemble": _Out(_assemble(writer_seat="cdp", reviewer_seat="local")),
            "draft": _Out(_draft(), model_id="cdp/opus-5.5"),
            "provenance_check": _Out({"violations": [], "pass": True}),
            "independence": _Out({"refused": None, "independence": "full"}),
            "review": _Out({"findings": [], "verdict": "ship"}, model_id=_QWEN),
        }
    )
    assert payload["seats_used"]["writer"] == "cdp/opus-5.5"


def test_finalize_refused_review_with_violations_preserves_draft_v1() -> None:
    draft = _draft()
    fallback = {"reason": "timeout", "fallback_to": None, "seat": "cdp"}
    result = asyncio.run(
        finalize.WritingFinalizeHandler().execute(
            None,
            _Ctx(
                {
                    "assemble": _Out(_assemble(reviewer_seat="cdp")),
                    "draft": _Out(draft, model_id=_HERMES),
                    "provenance_check": _Out(
                        {"violations": [{"type": "omission"}], "pass": False}
                    ),
                    "independence": _Out({"refused": None, "independence": "full"}),
                    "review": _Out(
                        {
                            "refused": "reviewer_seat_failed",
                            "error": "reviewer_seat_failed",
                            "seat_fallback": fallback,
                        }
                    ),
                    "revise": _Out({"_skipped": True}),
                    "provenance_check_final": _Out({"_skipped": True}),
                }
            ),
        )
    )
    assert result.error is None
    payload = result.json
    assert payload["draft_v1"]["draft"] == draft["draft"]
    assert payload["unsent"] is True
    assert payload["review"]["status"] == "failed"
    assert payload["ship_gate"]["pass"] is False
    assert payload["seat_fallback"]["review"] == fallback
