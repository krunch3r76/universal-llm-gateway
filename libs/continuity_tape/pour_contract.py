"""Pour contract for ``ulg.continuity.messages/1`` envelopes.

Layers: L0 is the byte-for-byte capture (Cursor JSONL, a future grok export, or a
claude.ai DOM harvest which is pseudo-byte-exact). L1 is the raw pour of that
capture (``provenance=raw``). L2 is the transcribed pour, the preferred default,
same messages format, produced by a bot (``provenance=transcribed``), and never
required. ``meta.capture`` is an optional pointer at L0: ``{kind, ref, sha256,
pseudo_byte_exact}``. Producers store L0 at a readable ``cortex://`` or
``workspaces://`` ref. When capture is present on a raw pour whose kind is
``jsonl`` or ``grok_export``, ``capture.sha256`` equals ``meta.source_sha256``.

Location: ``cortex://notes/system/tape/pours/<surface>/<transcript_id>.messages.json``.
Each pour is cumulative for one window. The first write is ``if_absent``. Later
writes supply ``expected_sha256`` and are admitted only when the new ``messages``
prefix-extend the bytes already stored. Producers drop thinking blocks, tool
results, and harness-injected user lines before pouring. No transcript text
lives outside the pour.

Required meta: ``schema == ulg.continuity.messages/1``; ``provenance`` is
``raw`` (byte-exact JSONL or an equivalent raw source) or ``transcribed``
(messages transcribed by a bot); ``sources[0].producer`` is
``{id, kind, version}``; ``sources[0].kind`` is set; ``transcript_id``,
``surface``, and ``messages_sha256`` (recomputed with
``seal_messages_sha256``) are set; ``coverage == full`` unless the caller
passes ``require_full=False``. ``raw`` pours include ``source_sha256``.
``messages`` is non-empty and contains at least one user message with
content. ``sources[0].window = {lo, hi, total}`` is optional; when present
and coverage is full, ``lo == 1`` and ``hi == total``.

Import ``validate_pour`` without Stargate. Producers are out of this module.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from continuity_tape.messages import (
    SCHEMA_ID,
    CaptureRef,
    ContinuityMessagesEnvelope,
    seal_messages_sha256,
)

_PROVENANCE: frozenset[str] = frozenset({"raw", "transcribed"})


class PourRefused(Exception):  # noqa: N818 — contract name from the packet
    """A pour failed the contract. ``code`` is a ``pour.*`` refusal."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _refuse(code: str, message: str) -> None:
    raise PourRefused(code, message)


def _content_present(content: Any) -> bool:
    """String body or a non-empty structured block list."""
    if isinstance(content, str):
        return bool(content)
    if isinstance(content, list):
        return any(isinstance(block, dict) and block.get("type") for block in content)
    return False


def validate_pour(
    envelope_dict: dict[str, Any],
    *,
    require_full: bool = True,
) -> ContinuityMessagesEnvelope:
    """Validate a poured envelope dict and return the parsed model."""
    if not isinstance(envelope_dict, dict):
        _refuse("pour.schema", "envelope is not an object")
    schema = envelope_dict.get("schema")
    if schema != SCHEMA_ID:
        _refuse("pour.schema", f"schema {schema!r} != {SCHEMA_ID}")
    meta = envelope_dict.get("meta")
    if not isinstance(meta, dict):
        _refuse("pour.schema", "meta missing")
    provenance = meta.get("provenance")
    if provenance not in _PROVENANCE:
        _refuse(
            "pour.provenance_missing",
            "meta.provenance must be raw or transcribed",
        )
    sources = meta.get("sources")
    if not isinstance(sources, list) or not sources or not isinstance(sources[0], dict):
        _refuse("pour.producer_missing", "meta.sources[0] missing")
    source0 = sources[0]
    producer = source0.get("producer")
    if not isinstance(producer, dict):
        _refuse("pour.producer_missing", "meta.sources[0].producer missing")
    if not isinstance(producer.get("id"), str) or not producer.get("id"):
        _refuse("pour.producer_missing", "producer.id missing")
    if not isinstance(producer.get("kind"), str) or not producer.get("kind"):
        _refuse("pour.producer_missing", "producer.kind missing")
    version = producer.get("version", None)
    if version is not None and not isinstance(version, str):
        _refuse("pour.producer_missing", "producer.version must be str or null")
    if not isinstance(source0.get("kind"), str) or not source0.get("kind"):
        _refuse("pour.producer_missing", "meta.sources[0].kind missing")
    if not meta.get("transcript_id"):
        _refuse("pour.schema", "meta.transcript_id missing")
    if not meta.get("surface"):
        _refuse("pour.schema", "meta.surface missing")
    messages = envelope_dict.get("messages")
    if not isinstance(messages, list) or not messages:
        _refuse("pour.hollow", "messages empty")
    user_with_content = any(
        isinstance(m, dict)
        and m.get("role") == "user"
        and _content_present(m.get("content"))
        for m in messages
    )
    if not user_with_content:
        _refuse("pour.hollow", "no user message with content")
    recomputed = seal_messages_sha256(messages)
    if meta.get("messages_sha256") != recomputed:
        _refuse("pour.sha_mismatch", "meta.messages_sha256 != recomputed seal")
    coverage = meta.get("coverage")
    if require_full and coverage != "full":
        _refuse("pour.coverage", "meta.coverage must be full")
    if provenance == "raw" and not meta.get("source_sha256"):
        _refuse("pour.source_sha_missing", "raw pour requires meta.source_sha256")
    capture = meta.get("capture")
    if capture is not None:
        try:
            parsed_capture = CaptureRef.model_validate(capture)
        except ValidationError as exc:
            _refuse("pour.capture_invalid", str(exc))
        if (
            provenance == "raw"
            and parsed_capture.kind in ("jsonl", "grok_export")
            and parsed_capture.sha256 != meta.get("source_sha256")
        ):
            _refuse(
                "pour.capture_inconsistent",
                "capture.sha256 != meta.source_sha256",
            )
    window = source0.get("window")
    if window is not None:
        if not isinstance(window, dict):
            _refuse("pour.window", "window must be an object")
        lo, hi, total = window.get("lo"), window.get("hi"), window.get("total")
        if not all(isinstance(v, int) for v in (lo, hi, total)):
            _refuse("pour.window", "window.lo/hi/total must be ints")
        if coverage == "full" and not (lo == 1 and hi == total):
            _refuse("pour.window", "full coverage requires lo==1 and hi==total")
    try:
        parsed = ContinuityMessagesEnvelope.model_validate(envelope_dict)
    except ValidationError as exc:
        _refuse("pour.schema", str(exc))
    if parsed.meta.provenance not in ("raw", "transcribed"):
        _refuse("pour.provenance_missing", "parsed provenance missing")
    return parsed


__all__ = ["PourRefused", "validate_pour"]
