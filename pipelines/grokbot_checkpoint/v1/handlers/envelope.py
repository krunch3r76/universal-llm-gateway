"""Immutable snapshot of a validated pour. Prefix-extend or refuse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import file_sha256, fs_of, refuse, rel_path_ok, step_output_json
from ._handoff import (
    HANDOFF_MAX_BYTES,
    fill_grok_binding,
    handoff_binding,
    handoff_json_size,
    normalize_handoff,
)


def slug_house(house: str) -> str:
    """One path segment. ``/``, ``..`` and spaces cannot nest or escape."""
    chars: list[str] = []
    for ch in house:
        chars.append(ch if ch.isalnum() or ch in "-_" else "-")
    slug = "".join(chars).strip("-")
    return slug or "house"


def files_roots_agree() -> bool:
    from cortex_store.dispatch_ops._shared import _FILES_ROOT
    from implement_admission.closeout_helpers import cortex_files_root

    return cortex_files_root().resolve() == Path(_FILES_ROOT).resolve()


def staging_uri(house: str, agent_id: str, provenance: str) -> str:
    return (
        "ephemeral/harvests/grok/"
        f"{slug_house(house)}-{slug_house(agent_id)[:8]}.{provenance}.messages.json"
    )


def _producer_id(meta: dict[str, Any]) -> str:
    sources = meta.get("sources")
    if isinstance(sources, list) and sources and isinstance(sources[0], dict):
        producer = sources[0].get("producer")
        if isinstance(producer, dict):
            return str(producer.get("id") or "")
    return ""


def _same_pour_identity(old_meta: dict[str, Any], source: dict[str, Any]) -> bool:
    """Same messages hash is not the same snapshot when provenance differs."""
    old_prov = str(old_meta.get("provenance") or "")
    new_prov = str(source.get("provenance") or "")
    if old_prov and new_prov and old_prov != new_prov:
        return False
    old_prod = _producer_id(old_meta)
    new_prod = str(source.get("producer") or "")
    if old_prod and new_prod and old_prod != new_prod:
        return False
    old_cap = old_meta.get("capture")
    new_cap = source.get("capture")
    old_ref = old_cap.get("ref") if isinstance(old_cap, dict) else None
    new_ref = new_cap.get("ref") if isinstance(new_cap, dict) else None
    return old_ref == new_ref


def _letter_on(
    envelope: dict[str, Any],
    options: dict[str, Any],
    *,
    messages_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None, list[str]]:
    """Copy the letter onto the envelope, stamping absent binding keys."""
    out = dict(envelope)
    letter = options.get("handoff")
    if letter is None:
        out.pop("handoff", None)
        return out, None, None, []
    if not isinstance(letter, dict):
        return out, None, None, []
    letter, normalized_fields = normalize_handoff(letter)
    messages = out.get("messages")
    filled, source = fill_grok_binding(
        letter,
        messages=messages if isinstance(messages, list) else [],
        messages_sha256=messages_sha256,
        house=str(options.get("house") or ""),
        n=options.get("n") if isinstance(options.get("n"), int) else 0,
    )
    out["handoff"] = filled
    return out, filled, source, normalized_fields


def _binding_fields(
    letter: dict[str, Any] | None,
    envelope: dict[str, Any],
    source: dict[str, Any],
    options: dict[str, Any],
    *,
    binding_source: str | None,
    normalized_fields: list[str] | None = None,
) -> dict[str, Any]:
    messages = envelope.get("messages")
    n = options.get("n")
    status, reasons = handoff_binding(
        letter,
        messages=messages if isinstance(messages, list) else [],
        messages_sha256=str(source.get("messages_sha256") or ""),
        house=str(options.get("house") or ""),
        n=n if isinstance(n, int) else 0,
    )
    fields: dict[str, Any] = {"handoff_binding": status}
    if binding_source is not None:
        fields["handoff_binding_source"] = binding_source
    if normalized_fields:
        fields["handoff.normalized"] = normalized_fields
    if status == "stale":
        fields["handoff_reasons"] = reasons
    return fields


def _snapshot(
    *,
    uri: str,
    sha256: str,
    messages_sha: str,
    action: str,
    source: dict[str, Any],
    envelope: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    return {
        "refused": None,
        "snapshot_uri": uri,
        "sha256": sha256,
        "messages_sha256": messages_sha,
        "action": action,
        "provenance": source.get("provenance"),
        "producer": source.get("producer"),
        "coverage": source.get("coverage"),
        "turn_count": source.get("turn_count"),
        "surface": source.get("surface"),
        "capture": source.get("capture"),
        "envelope": envelope,
        **binding,
    }


def _prefix_holds(old: list[Any], new: list[Any]) -> bool:
    from cortex_store.verbatim_succession import prefix_holds

    return bool(prefix_holds("messages-v1", old, new))


async def run_envelope(
    options: dict[str, Any], source: dict[str, Any], fs: Any
) -> dict[str, Any]:
    if (
        source.get("_skipped")
        or source.get("refused")
        or not source.get("messages_sha256")
    ):
        return refuse("envelope.upstream_skipped", "source did not yield a pour")
    uri = str(
        options.get("snapshot_uri")
        or staging_uri(
            str(options.get("house") or ""),
            str(options.get("agent_id") or "").strip(),
            str(source.get("provenance") or "raw"),
        )
    )
    if not rel_path_ok(uri):
        return refuse("admit.path_escape", f"refused path {uri}")
    if not files_roots_agree():
        return refuse(
            "fs.roots_disagree",
            "cortex_files_root() and cortex _FILES_ROOT differ",
        )
    source_envelope = source.get("envelope")
    if not isinstance(source_envelope, dict):
        return refuse("envelope.missing", "source produced no envelope")
    raw_letter = options.get("handoff")
    if raw_letter is not None and not isinstance(raw_letter, dict):
        # A non-dict letter is stale shape, same refusal as a mismatched letter,
        # and writes no snapshot.
        return refuse("post.handoff_stale", "shape")
    envelope, letter, binding_source, normalized_fields = _letter_on(
        source_envelope,
        options,
        messages_sha256=str(source.get("messages_sha256") or ""),
    )
    if letter is not None and handoff_json_size(letter) > HANDOFF_MAX_BYTES:
        return refuse(
            "admit.handoff_too_large",
            "handoff exceeds 16 KiB after binding fill",
        )
    binding = _binding_fields(
        letter,
        envelope,
        source,
        options,
        binding_source=binding_source,
        normalized_fields=normalized_fields,
    )
    if binding.get("handoff_binding") == "stale":
        reasons = binding.get("handoff_reasons") or []
        joined = ",".join(str(item) for item in reasons)
        # Raised here so a stale letter writes no snapshot, seal, card, or bus row.
        return refuse("post.handoff_stale", joined or "handoff")
    raw = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    existing = await fs.read(uri)
    messages_sha = str(source.get("messages_sha256") or "")
    if existing is None:
        written = await fs.write(uri, raw, if_absent=True)
        if not written.get("ok"):
            return refuse("envelope.conflict", "if_absent lost the race")
        return _snapshot(
            uri=uri,
            sha256=written["sha256"],
            messages_sha=messages_sha,
            action="write",
            source=source,
            envelope=envelope,
            binding=binding,
        )
    try:
        old = json.loads(existing.decode("utf-8"))
    except json.JSONDecodeError:
        return refuse("envelope.not_prefix", "existing snapshot is not json")
    old_meta = old.get("meta") if isinstance(old.get("meta"), dict) else {}
    old_sha = str(old_meta.get("messages_sha256") or "")
    if old_sha == messages_sha and not _same_pour_identity(old_meta, source):
        return refuse(
            "envelope.provenance_mismatch",
            "snapshot messages match but provenance, producer, or capture differ",
        )
    if old_sha == messages_sha and _same_pour_identity(old_meta, source):
        if old.get("handoff") != envelope.get("handoff"):
            written = await fs.write(uri, raw, expected_sha256=file_sha256(existing))
            if not written.get("ok"):
                return refuse("envelope.not_prefix", "guarded overwrite lost the race")
            return _snapshot(
                uri=uri,
                sha256=written["sha256"],
                messages_sha=messages_sha,
                action="overwrite",
                source=source,
                envelope=envelope,
                binding=binding,
            )
        return _snapshot(
            uri=uri,
            sha256=file_sha256(existing),
            messages_sha=messages_sha,
            action="skip",
            source=source,
            envelope=envelope,
            binding=binding,
        )
    old_msgs = old.get("messages") if isinstance(old, dict) else None
    new_msgs = envelope.get("messages")
    if not isinstance(old_msgs, list) or not isinstance(new_msgs, list):
        return refuse("envelope.not_prefix", "messages missing")
    if not _prefix_holds(old_msgs, new_msgs):
        return refuse("envelope.not_prefix", "new messages do not prefix-extend")
    written = await fs.write(uri, raw, expected_sha256=file_sha256(existing))
    if not written.get("ok"):
        return refuse("envelope.not_prefix", "guarded overwrite lost the race")
    return _snapshot(
        uri=uri,
        sha256=written["sha256"],
        messages_sha=messages_sha,
        action="overwrite",
        source=source,
        envelope=envelope,
        binding=binding,
    )


class GrokbotCheckpointEnvelopeHandler(BaseHandler):
    step_type = "grokbot_checkpoint_envelope_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        outputs = getattr(context, "outputs", {}) or {}
        source = step_output_json(outputs, "source")
        if (not source or source.get("_skipped")) and options.get("mode") == "harvest_seal":
            source = step_output_json(outputs, "harvest_pour")
        payload = await run_envelope(options, source, fs_of(context))
        return StepOutput(raw="", json=payload)
