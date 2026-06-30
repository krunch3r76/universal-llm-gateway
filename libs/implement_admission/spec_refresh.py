"""Multi-step spec-hash refresh: supersede implement_ready + skeptic assertions, re-pin attrs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.implement_ready import assertion_active

_CORTEX_TIMEOUT = 15.0
_IMPL, _SKEP = "implement_ready", "skeptic_ratified"
_ASSERTS = {
    "confidence": "confirmed",
    "superseded": False,
    "intent": "full",
    "limit": 50,
}


def _dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT) as client:
        resp = client.post("/dispatch", json={"tool": tool, "arguments": arguments})
        resp.raise_for_status()
        return resp.json()


@dataclass
class RefreshResult:
    todo_id: str
    old_sha: str | None
    new_sha: str
    implement_ready_new_id: int | None = None
    skeptic_new_id: int | None = None
    skipped_skeptic: bool = False
    no_change: bool = False
    warnings: list[str] = field(default_factory=list)


def _normalize_predicate(raw: Any) -> str:
    return "".join(raw.split()).lower() if isinstance(raw, str) else ""


def _decode_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _current_sha_from_evidence(evidence_uris: list[str]) -> str | None:
    return next((u for u in evidence_uris if str(u).startswith("spec_sha256:")), None)


def _replace_sha_in_evidence(
    evidence_uris: list[str], old_sha_uri: str | None, new_sha_uri: str
) -> list[str]:
    out, replaced = [], False
    for uri in evidence_uris:
        if old_sha_uri is not None and uri == old_sha_uri:
            if new_sha_uri not in out:
                out.append(new_sha_uri)
            replaced = True
        elif uri not in out:
            out.append(uri)
    if not replaced and new_sha_uri not in out:
        out.append(new_sha_uri)
    return out


def _find_active_assertion(
    todo_id: str, status_slug: str, now_iso: str
) -> tuple[int, dict[str, Any]] | None:
    listed = _dispatch("assertions", {"entity_id": todo_id, **_ASSERTS})
    items = listed.get("items") if isinstance(listed, dict) else None
    if not isinstance(items, list):
        return None
    target = _normalize_predicate(f"status({todo_id}, {status_slug}, current)")
    best, best_key = None, ("", -1)
    for item in items:
        if not isinstance(item, dict) or item.get("entity_id") != todo_id:
            continue
        pf = _normalize_predicate(item.get("predicate_form") or "")
        claim = _normalize_predicate((item.get("claim") or "")[:90])
        if pf != target and not claim.startswith(target):
            continue
        if not assertion_active(item, now_iso=now_iso):
            continue
        aid = item.get("id")
        if not isinstance(aid, int):
            continue
        key = (str(item.get("observed_at") or ""), aid)
        if key > best_key:
            best, best_key = item, key
    return None if best is None else (best_key[1], best)


def _supersede_assertion(
    old_id: int, old: dict[str, Any], evidence: list[str], todo_id: str, session_id: str
) -> dict[str, Any]:
    response = _dispatch(
        "supersede",
        {
            "old_assertion_id": old_id,
            "entity_id": todo_id,
            "claim": old.get("claim", ""),
            "confidence": "confirmed",
            "evidence": f"spec-hash refresh via refresh-spec-hash at {session_id}",
            "session_id": session_id,
            "agent": "refresh-spec-hash",
            "evidence_uris": evidence,
            "derivation_type": "inference",
            "seeded_by": "refresh-spec-hash",
        },
    )
    if "error" in response:
        raise RuntimeError(f"supersede failed: {response}")
    return response


def _repin_attrs(
    todo_id: str, attrs: dict[str, Any], impl_id: int, skep_id: int | None
) -> None:
    patch = {**attrs, "implement_ready_assertion_id": impl_id}
    if skep_id is not None:
        patch["skeptic_assertion_id"] = skep_id
    _dispatch("entity_update", {"entity_id": todo_id, "attributes": patch})


def _verify_refresh(
    *,
    todo_id: str,
    new_sha_uri: str,
    impl_new_id: int,
    skep_new_id: int | None,
    now_iso: str,
) -> None:
    for slug in (_IMPL, _SKEP):
        if slug == _SKEP and skep_new_id is None:
            continue
        found = _find_active_assertion(todo_id, slug, now_iso)
        if found is None:
            raise RuntimeError(f"{todo_id}: post-refresh {slug} assertion missing")
        if new_sha_uri not in (found[1].get("evidence_uris") or []):
            raise RuntimeError(f"{todo_id}: {slug} assertion missing new sha")
    pinned = _decode_attributes(
        _dispatch("entity_get", {"entity_id": todo_id, "intent": "full"}).get(
            "attributes"
        )
    )
    if pinned.get("implement_ready_assertion_id") != impl_new_id:
        raise RuntimeError(f"{todo_id}: implement_ready_assertion_id attr mismatch")
    if skep_new_id is not None and pinned.get("skeptic_assertion_id") != skep_new_id:
        raise RuntimeError(f"{todo_id}: skeptic_assertion_id attr mismatch")


def _supersede_skeptic(
    result: RefreshResult,
    *,
    todo_id: str,
    skep: tuple[int, dict[str, Any]],
    old_sha: str | None,
    new_sha: str,
    session_id: str,
) -> None:
    skep_id, row = skep
    if _current_sha_from_evidence(row.get("evidence_uris") or []) == new_sha:
        result.skeptic_new_id = skep_id
        return
    evidence = _replace_sha_in_evidence(
        row.get("evidence_uris") or [], old_sha, new_sha
    )
    try:
        result.skeptic_new_id = _supersede_assertion(
            skep_id, row, evidence, todo_id, session_id
        )["new"]["id"]
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "concurrent refresh" in msg or "409" in msg:
            raise RuntimeError(
                f"{todo_id}: concurrent refresh detected during skeptic supersede. "
                "Re-read assertions and rerun."
            ) from exc
        raise


def refresh_spec_attestations(
    *, todo_id: str, spec_path: Path, dry_run: bool = False
) -> RefreshResult:
    new_sha = dense_spec_hash_uri(spec_path.read_text(encoding="utf-8"))
    now_iso = datetime.now(UTC).isoformat()
    session_id = f"refresh-spec-hash-{now_iso}"
    entity = _dispatch("entity_get", {"entity_id": todo_id, "intent": "full"})
    if "error" in entity:
        raise ValueError(f"{todo_id}: entity not found — {entity['error']}")
    attrs = _decode_attributes(entity.get("attributes"))
    impl = _find_active_assertion(todo_id, _IMPL, now_iso)
    if impl is None:
        raise ValueError(
            f"{todo_id}: no active confirmed implement_ready assertion found"
        )
    impl_id, impl_row = impl
    old_sha = _current_sha_from_evidence(impl_row.get("evidence_uris") or [])
    result = RefreshResult(todo_id=todo_id, old_sha=old_sha, new_sha=new_sha)
    if old_sha == new_sha:
        result.no_change = True
        if not dry_run:
            skep = _find_active_assertion(todo_id, _SKEP, now_iso)
            skep_id = skep[0] if skep else None
            if attrs.get("implement_ready_assertion_id") != impl_id or (
                skep_id is not None and attrs.get("skeptic_assertion_id") != skep_id
            ):
                result.implement_ready_new_id, result.skeptic_new_id = impl_id, skep_id
                _repin_attrs(todo_id, attrs, impl_id, skep_id)
                _verify_refresh(
                    todo_id=todo_id,
                    new_sha_uri=new_sha,
                    impl_new_id=impl_id,
                    skep_new_id=skep_id,
                    now_iso=datetime.now(UTC).isoformat(),
                )
        return result
    if dry_run:
        return result
    skep = _find_active_assertion(todo_id, _SKEP, now_iso)
    if skep is not None:
        _supersede_skeptic(
            result,
            todo_id=todo_id,
            skep=skep,
            old_sha=old_sha,
            new_sha=new_sha,
            session_id=session_id,
        )
    else:
        result.skipped_skeptic = True
        result.warnings.append(
            f"{todo_id}: no active skeptic_ratified assertion found — "
            "rerun axis-2 skeptic before implement dispatch."
        )
    result.implement_ready_new_id = _supersede_assertion(
        impl_id,
        impl_row,
        _replace_sha_in_evidence(impl_row.get("evidence_uris") or [], old_sha, new_sha),
        todo_id,
        session_id,
    )["new"]["id"]
    _repin_attrs(todo_id, attrs, result.implement_ready_new_id, result.skeptic_new_id)
    _verify_refresh(
        todo_id=todo_id,
        new_sha_uri=new_sha,
        impl_new_id=result.implement_ready_new_id,
        skep_new_id=result.skeptic_new_id,
        now_iso=datetime.now(UTC).isoformat(),
    )
    return result


__all__ = ["RefreshResult", "refresh_spec_attestations"]
