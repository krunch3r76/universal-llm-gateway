"""Unit tests for CDP digest extract seal/parse (mocked satellite)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cortex_store.journal_digest_extract_cdp import (
    PROMPT_REV_SOFT_V2,
    build_soft_v2_sealed_prompt,
    parse_harvest_claims,
    poll_cdp_execution,
    prompt_sha256,
)

_ENTRY = (
    "While working rideshare after midnight, he found himself nodding off. "
    "FSD beeped him awake on nearly every drop into the subconscious."
)
_ANCHOR = "2026-07-17#overnight-rideshare-micro-sleeps"
_URI = "cortex://notes/journal/2026-07-17.md"

_ASSERT_CLAIM = {
    "claim": "Operator experienced micro-sleeps while driving rideshare overnight",
    "p_class": "P1",
    "canonicality": "assert",
    "attach_hint": None,
    "flags": [],
    "evidence_anchor": "overnight-rideshare-micro-sleeps",
}


@pytest.mark.offline
def test_soft_v2_prompt_includes_entry_and_schema() -> None:
    prompt = build_soft_v2_sealed_prompt(
        entry_text=_ENTRY,
        entry_anchor=_ANCHOR,
        journal_uri=_URI,
    )
    assert "Journal claim extraction task" in prompt
    assert _ANCHOR in prompt
    assert _ENTRY in prompt
    assert "P1" in prompt
    assert "assert" in prompt


@pytest.mark.offline
def test_parse_harvest_claims_accepts_fenced_json() -> None:
    harvest = json.dumps({"claims": [_ASSERT_CLAIM]})
    wrapped = f"```json\n{harvest}\n```"
    batch = parse_harvest_claims(
        wrapped,
        entry_anchor=_ANCHOR,
        journal_uri=_URI,
    )
    assert batch is not None
    assert len(batch["claims"]) == 1
    assert batch["claims"][0]["canonicality"] == "assert"


@pytest.mark.offline
def test_parse_harvest_claims_strips_cdp_archive_envelope() -> None:
    claims_json = json.dumps({"claims": [_ASSERT_CLAIM]})
    archive = (
        "# CDP ask harvest\n\n"
        "- execution_id: `abc`\n"
        "- attested_model: `Model: Haiku 4.5 Extended`\n\n"
        f"## Body\n\nThought process\njson\n{claims_json}\n"
    )
    batch = parse_harvest_claims(
        archive,
        entry_anchor=_ANCHOR,
        journal_uri=_URI,
    )
    assert batch is not None
    assert batch["claims"][0]["canonicality"] == "assert"


@pytest.mark.offline
def test_parse_requires_at_least_one_valid_claim() -> None:
    assert (
        parse_harvest_claims('{"claims": []}', entry_anchor=_ANCHOR, journal_uri=_URI)
        is None
    )


@pytest.mark.offline
def test_prompt_sha256_format() -> None:
    digest = prompt_sha256("hello")
    assert digest.startswith("sha256:")


@pytest.mark.offline
def test_model_mismatch_poll_parks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite:8770")

    completed = {
        "execution_id": "exec-1",
        "status": "completed",
        "ok": True,
        "archive_uri": "cortex://ephemeral/digest/x.md",
        "attested_model": "Sonnet 4.5",
    }

    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _Resp:
            return _Resp(completed)

    with patch("cortex_store.journal_digest_extract_cdp.httpx.Client", _Client):
        result = poll_cdp_execution(
            "exec-1",
            requested_model="haiku-4.5",
            timeout_s=1,
        )

    assert result.get("park_reason") == "model_unavailable"


@pytest.mark.offline
def test_prompt_rev_constant() -> None:
    assert PROMPT_REV_SOFT_V2 == "soft-v2"
