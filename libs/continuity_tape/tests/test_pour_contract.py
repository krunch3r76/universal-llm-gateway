"""Every pour.* refusal, plus a valid pour."""

from __future__ import annotations

import pytest
from continuity_tape.messages import seal_messages_sha256
from continuity_tape.pour_contract import PourRefused, validate_pour

pytestmark = pytest.mark.offline


def _ok() -> dict:
    messages = [{"role": "user", "content": "hello", "turn_index": 1}]
    return {
        "schema": "ulg.continuity.messages/1",
        "messages": messages,
        "meta": {
            "surface": "cursor",
            "transcript_id": "tid",
            "provenance": "raw",
            "source_sha256": "abc",
            "messages_sha256": seal_messages_sha256(messages),
            "coverage": "full",
            "sources": [
                {
                    "kind": "jsonl",
                    "producer": {
                        "id": "cursor-jsonl-extractor",
                        "kind": "extractor",
                        "version": None,
                    },
                    "window": {"lo": 1, "hi": 1, "total": 1},
                }
            ],
        },
    }


def test_valid_pour() -> None:
    env = validate_pour(_ok())
    assert env.meta.provenance == "raw"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d.update(schema="nope"), "pour.schema"),
        (lambda d: d["meta"].pop("provenance"), "pour.provenance_missing"),
        (lambda d: d["meta"].pop("sources"), "pour.producer_missing"),
        (lambda d: d["meta"].update(messages_sha256="0"), "pour.sha_mismatch"),
        (lambda d: d["meta"].update(coverage="tail"), "pour.coverage"),
        (lambda d: d["meta"].pop("source_sha256"), "pour.source_sha_missing"),
        (lambda d: d.update(messages=[]), "pour.hollow"),
        (
            lambda d: d["meta"]["sources"][0].update(
                window={"lo": 2, "hi": 1, "total": 9}
            ),
            "pour.window",
        ),
    ],
)
def test_refusal_codes(mutate, code: str) -> None:
    payload = _ok()
    mutate(payload)
    with pytest.raises(PourRefused) as exc:
        validate_pour(payload)
    assert exc.value.code == code


def test_capture_rules() -> None:
    payload = _ok()
    payload["meta"]["capture"] = {
        "kind": "dom_harvest",
        "ref": "cortex://notes/x",
        "sha256": "ab" * 32,
        "pseudo_byte_exact": False,
    }
    with pytest.raises(PourRefused) as exc:
        validate_pour(payload)
    assert exc.value.code == "pour.capture_invalid"
    payload["meta"]["capture"]["kind"] = "jsonl"
    payload["meta"]["capture"]["pseudo_byte_exact"] = False
    payload["meta"]["capture"]["sha256"] = "cd" * 32
    with pytest.raises(PourRefused) as mismatch:
        validate_pour(payload)
    assert mismatch.value.code == "pour.capture_inconsistent"
    payload["meta"]["source_sha256"] = "ab" * 32
    payload["meta"]["capture"] = {
        "kind": "jsonl",
        "ref": "cortex://notes/x",
        "sha256": "ab" * 32,
        "pseudo_byte_exact": False,
    }
    assert validate_pour(payload).meta.capture is not None


def test_import_without_stargate() -> None:
    assert callable(validate_pour)
