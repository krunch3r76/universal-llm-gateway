"""friction_close implementation — supersede friction with a resolution assertion."""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from typing import Any

from deploy_identity.code_ref_relation import resolve_commit_sha
from fastapi import HTTPException
from git_integrate.commit_paths import commit_paths_fingerprint
from universal_logging import get_logger
from universal_workspace import get_workspace_root

from ..guidance_entity import GUIDANCE_ID_PREFIXES, entity_slug_from_id
from ..routes.assertions import _create_assertion_impl, _supersede_assertion_impl
from ._shared import record
from .ops_assertions_update import _op_assertion_get

logger = get_logger("cortex-api.dispatch_ops.assertions")

_RESOLUTION_KIND_EXACT = frozenset({"superseded", "wontfix", "uncommitted"})
# arc 3924: rule:/skill: join agent_skill: as valid resolution kinds after the
# rules/skills corpus migration (workflow: retained for pre-migration back-compat).
_RESOLUTION_KIND_PREFIXES = ("agent_skill:", "rule:", "skill:", "workflow:", "todo:", "commit:")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")

_RESOLUTION_KIND_CATALOG: tuple[tuple[str, str], ...] = (
    ("agent_skill:{slug}", "closed by adopting or updating an agent skill"),
    ("rule:{slug}", "closed by adopting or updating a rule"),
    ("skill:{slug}", "closed by adopting or updating a skill"),
    ("workflow:{slug}", "closed by adopting or updating a workflow"),
    ("todo:{slug}", "promote friction into a recon-pending todo"),
    ("commit:{sha}", "closed by code fix landed at git commit"),
    ("uncommitted", "closed by direct-first fix not yet committed"),
    ("superseded", "superseded by a newer assertion or friction"),
    ("wontfix", "acknowledged; will not fix"),
)

_REPO_PATH_RE = re.compile(
    r"\b(?:libs|services|scripts|systems|pipelines|cursor-plugins)/"
    r"[A-Za-z0-9_./-]+\.(?:py|md|yaml|yml|sh|json|mdc)\b"
)
_CATEGORY_PREFIX_RE = re.compile(r"^\[[^\]]+\]\s*")


def format_resolution_kind_catalog() -> str:
    """Semicolon-separated resolution_kind forms with brief effect labels."""
    return "; ".join(f"{form} ({effect})" for form, effect in _RESOLUTION_KIND_CATALOG)


def format_resolution_kind_unknown_reason(resolution_kind: str) -> str:
    """Self-describing rejection for an invalid ``resolution_kind``."""
    return (
        f"resolution_kind is {resolution_kind!r} — accepted values: "
        f"{format_resolution_kind_catalog()}."
    )


def validate_resolution_kind(resolution_kind: str) -> str | None:
    """Return an error message when *resolution_kind* is invalid, else None."""
    if resolution_kind in _RESOLUTION_KIND_EXACT:
        return None
    for prefix in _RESOLUTION_KIND_PREFIXES:
        if not resolution_kind.startswith(prefix):
            continue
        slug = resolution_kind[len(prefix) :]
        if not slug:
            return format_resolution_kind_unknown_reason(resolution_kind)
        if prefix == "commit:" and not _COMMIT_SHA_RE.match(slug):
            return (
                f"resolution_kind {resolution_kind!r}: commit slug must be a git SHA "
                f"(7–40 hex chars) — accepted values: {format_resolution_kind_catalog()}."
            )
        return None
    return format_resolution_kind_unknown_reason(resolution_kind)


def _extract_repo_paths(text: str) -> list[str]:
    """Pull repo-relative paths from free text (resolution_note, evidence)."""
    seen: set[str] = set()
    paths: list[str] = []
    for match in _REPO_PATH_RE.finditer(text):
        path = match.group(0)
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _symptom_excerpt(friction_claim: str, *, max_len: int = 240) -> str:
    """First identifying sentence from the open friction, category tag stripped."""
    text = _CATEGORY_PREFIX_RE.sub("", friction_claim.strip())
    if not text:
        return friction_claim.strip()[:max_len]
    sentence = re.search(r"^(.+?[.!?])(?:\s|$)", text)
    excerpt = sentence.group(1) if sentence else text
    if len(excerpt) > max_len:
        return f"{excerpt[: max_len - 3]}..."
    return excerpt


def _build_close_claim(
    assertion_id: int,
    resolution_kind: str,
    friction_claim: str,
    resolution_note: str | None,
) -> str:
    """Close claim carries symptom + fix so residue projection stays identifiable."""
    symptom = _symptom_excerpt(friction_claim)
    fix = (resolution_note or "").strip() or "see evidence"
    return (
        f"[resolved:{resolution_kind}] Friction #{assertion_id} closed. "
        f"Symptom: {symptom} Fix: {fix}"
    )


def _commit_touched_set(repo: str, sha: str) -> set[str] | None:
    """Paths modified by ``sha``, or None when git diff-tree fails."""
    proc = subprocess.run(
        ["git", "-C", repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    if proc.returncode != 0:
        return None
    return set(proc.stdout.strip().splitlines())


def _path_touched_by_commit(path: str, touched: set[str]) -> bool:
    if path in touched:
        return True
    return any(t == path or t.endswith(f"/{path}") for t in touched)


def _paths_not_touched_by_commit(repo: str, sha: str, paths: list[str]) -> list[str] | None:
    """Repo-relative paths from ``paths`` absent from ``sha`` (None if git fails)."""
    touched = _commit_touched_set(repo, sha)
    if touched is None:
        return None
    return [path for path in paths if not _path_touched_by_commit(path, touched)]


def _commit_touches_paths(repo: str, sha: str, paths: list[str]) -> bool:
    """True when ``sha`` modified every named path (git diff-tree name-only)."""
    missing = _paths_not_touched_by_commit(repo, sha, paths)
    if missing is None:
        return False
    return not missing


def _validate_commit_resolution(
    resolution_kind: str,
    *,
    changed_paths: list[str] | None,
    resolution_note: str | None,
) -> str | None:
    """Reject commit: when paths are uncommitted or absent from the named SHA."""
    if not resolution_kind.startswith("commit:"):
        return None
    sha_slug = resolution_kind.removeprefix("commit:")
    paths = list(changed_paths or []) or _extract_repo_paths(resolution_note or "")
    if not paths:
        return (
            f"friction_close {resolution_kind!r} requires changed_paths or repo "
            "paths in resolution_note — cannot verify landing without paths."
        )
    try:
        repo = str(get_workspace_root())
    except RuntimeError:
        return "friction_close commit: — workspace root unavailable for git verify"
    if resolve_commit_sha(sha_slug) is None:
        return f"friction_close commit:{sha_slug} — git cannot resolve SHA"
    if commit_paths_fingerprint(repo, paths):
        preview = ", ".join(paths[:3])
        if len(paths) > 3:
            preview = f"{preview}, ..."
        return (
            f"friction_close {resolution_kind!r}: named paths have uncommitted "
            f"changes ({preview}) — use resolution_kind='uncommitted' until "
            "the fix is committed."
        )
    resolved = resolve_commit_sha(sha_slug)
    assert resolved is not None
    missing = _paths_not_touched_by_commit(repo, resolved, paths)
    if missing is None:
        return f"friction_close commit:{sha_slug} — git diff-tree failed for SHA"
    if missing:
        return (
            f"friction_close {resolution_kind!r}: commit does not touch "
            f"{missing!r} (requested {paths!r}) — use resolution_kind='uncommitted' "
            "or the commit that actually contains the fix."
        )
    return None


def _resolution_note_present(resolution_note: str | None) -> bool:
    """True when resolution_note is non-empty (None and '' are absent)."""
    return resolution_note is not None and resolution_note != ""


def _persist_already_closed_note(
    *,
    entity_id: str,
    friction_assertion_id: int,
    fulfillment_assertion_id: int,
    resolution_note: str,
    resolution_kind: str,
    agent: str,
    session_id: str,
) -> tuple[int | None, str | None]:
    """Persist losing-seat disposition note on the fulfillment chain."""
    body: dict[str, Any] = {
        "entity_id": entity_id,
        "claim": resolution_note,
        "confidence": "confirmed",
        "evidence": (
            f"friction_close already_closed disposition note for friction "
            f"#{friction_assertion_id} (fulfillment #{fulfillment_assertion_id}, "
            f"resolution_kind={resolution_kind}) at "
            f"{datetime.now(UTC).isoformat()}"
        ),
        "derivation_type": "agent_observation",
        "confidence_score": 1.0,
        "fulfillment_assertion_id": fulfillment_assertion_id,
        "session_id": session_id,
        "seeded_by": agent,
        "attributes": {"friction_assertion_id": friction_assertion_id},
    }
    try:
        result = _create_assertion_impl(body)
    except HTTPException as exc:
        return None, str(exc.detail)
    except Exception as exc:
        return None, str(exc)

    new_item = result.get("item") or {}
    note_id = new_item.get("id") if isinstance(new_item, dict) else None
    if not isinstance(note_id, int):
        return None, "assertion create returned no id"
    return note_id, None


def _already_closed_response(
    *,
    assertion_id: int,
    fulfillment_assertion_id: int,
    resolution_kind: str,
    resolution_note: str | None,
    entity_id: str | None,
    agent: str,
    session_id: str,
) -> dict[str, Any]:
    """Return already_closed payload; echo and persist non-empty resolution_note."""
    payload: dict[str, Any] = {
        "status": "already_closed",
        "assertion_id": assertion_id,
        "fulfillment_assertion_id": fulfillment_assertion_id,
        "resolution_kind": resolution_kind,
    }
    if not _resolution_note_present(resolution_note):
        return payload

    payload["resolution_note"] = resolution_note
    if not entity_id:
        payload["note_persisted"] = None
        payload["note_persist_error"] = (
            f"Friction assertion {assertion_id} has no entity_id"
        )
        return payload

    note_id, persist_err = _persist_already_closed_note(
        entity_id=entity_id,
        friction_assertion_id=assertion_id,
        fulfillment_assertion_id=fulfillment_assertion_id,
        resolution_note=resolution_note,
        resolution_kind=resolution_kind,
        agent=agent,
        session_id=session_id,
    )
    if note_id is not None:
        payload["note_persisted"] = note_id
    else:
        payload["note_persisted"] = None
        payload["note_persist_error"] = persist_err or "unknown persistence failure"
    return payload


def _promote_friction_to_todo(
    *,
    resolution_kind: str,
    friction_entity_id: str,
    friction_assertion_id: int,
    friction_claim: str,
    agent: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Promote a closed friction into a recon-pending todo when it does not exist."""
    from ._recon_seed import seed_recon_todo

    slug = resolution_kind.removeprefix("todo:")
    required_skills: list[str] = []
    if any(friction_entity_id.startswith(p) for p in GUIDANCE_ID_PREFIXES):
        required_skills = [entity_slug_from_id(friction_entity_id)]

    from .ops_entities import _op_entity_get, _op_entity_update
    from .state_card import merge_state_card

    promote_attrs = merge_state_card({"promoted_from_friction": friction_assertion_id})

    result = seed_recon_todo(
        todo_id=resolution_kind,
        name=friction_claim or f"Promoted from friction #{friction_assertion_id}",
        source_uri=f"cortex://notes/system/specs/{slug}.md",
        required_skills=required_skills,
        seed_ack=(
            f"auto-promoted from friction #{friction_assertion_id}; "
            "recon pending (density_triage=recon_pending)"
        ),
        context_target_id=friction_entity_id,
        extra_attrs=promote_attrs,
        agent=agent,
        session_id=session_id,
    )
    if result is None:
        existing = _op_entity_get(entity_id=resolution_kind, intent="full")
        if isinstance(existing, dict) and "error" not in existing:
            prior = existing.get("attributes") or {}
            if not isinstance(prior, dict):
                prior = {}
            upgraded = merge_state_card({**prior, **promote_attrs})
            _op_entity_update(entity_id=resolution_kind, attributes=upgraded)
        return None
    if result and "todo_created" in result:
        record(
            "cortex.friction.todo.promoted",
            assertion_id=friction_assertion_id,
            todo_id=resolution_kind,
            friction_entity_id=friction_entity_id,
            agent=agent,
        )
    return result


def close_friction_assertion(
    assertion_id: int,
    resolution_kind: str,
    *,
    agent: str = "unknown",
    session_id: str = "friction-close",
    evidence: str | None = None,
    resolution_note: str | None = None,
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    kind_err = validate_resolution_kind(resolution_kind)
    if kind_err:
        return {"error": kind_err}

    commit_err = _validate_commit_resolution(
        resolution_kind,
        changed_paths=changed_paths,
        resolution_note=resolution_note,
    )
    if commit_err:
        return {"error": commit_err}

    existing = _op_assertion_get(assertion_id=assertion_id)
    if "error" in existing:
        return existing

    superseded_by = existing.get("superseded_by")
    if superseded_by is not None:
        return _already_closed_response(
            assertion_id=assertion_id,
            fulfillment_assertion_id=superseded_by,
            resolution_kind=resolution_kind,
            resolution_note=resolution_note,
            entity_id=existing.get("entity_id"),
            agent=agent,
            session_id=session_id,
        )

    entity_id = existing.get("entity_id")
    if not entity_id:
        return {"error": f"Friction assertion {assertion_id} has no entity_id"}

    # Promote BEFORE superseding the friction. Promotion can fail (e.g. schema
    # reject) and superseding is irreversible-by-early-return: a closed friction
    # short-circuits at the superseded_by guard above, so a post-supersede
    # promotion failure permanently drops the todo: intent with no recovery
    # path. Running promotion first lets a failure return an error while the
    # friction is still open and the close is replayable.
    promotion: dict[str, Any] | None = None
    if resolution_kind.startswith("todo:"):
        promotion = _promote_friction_to_todo(
            resolution_kind=resolution_kind,
            friction_entity_id=entity_id,
            friction_assertion_id=assertion_id,
            friction_claim=str(existing.get("claim") or ""),
            agent=agent,
            session_id=session_id,
        )
        if isinstance(promotion, dict) and "error" in promotion:
            return {
                "error": f"friction_close promotion failed: {promotion['error']}",
                "assertion_id": assertion_id,
                "resolution_kind": resolution_kind,
            }

    friction_claim = str(existing.get("claim") or "")
    claim = _build_close_claim(
        assertion_id,
        resolution_kind,
        friction_claim,
        resolution_note,
    )

    resolved_evidence = evidence or (
        f"friction_close(assertion_id={assertion_id}, "
        f"resolution_kind={resolution_kind}) at "
        f"{datetime.now(UTC).isoformat()}"
    )

    supersede_body: dict[str, Any] = {
        "old_assertion_id": assertion_id,
        "entity_id": entity_id,
        "claim": claim,
        "confidence": "confirmed",
        "evidence": resolved_evidence,
        "derivation_type": "agent_observation",
        "confidence_score": 1.0,
        "session_id": session_id,
        "agent": agent,
        "seeded_by": agent,
        "revision_type": "restatement",
        "attributes": {"revision_type": "restatement"},
    }

    try:
        result = _supersede_assertion_impl(supersede_body)
    except Exception as exc:  # HTTPException from route layer
        detail = getattr(exc, "detail", str(exc))
        return {"error": f"friction_close supersede failed: {detail}"}

    if "error" in result:
        return result

    new_item = result.get("new") or result.get("item") or {}
    fulfillment_id = new_item.get("id") if isinstance(new_item, dict) else None

    logger.info(
        "cortex friction_close: %d -> %s via %s",
        assertion_id,
        fulfillment_id,
        resolution_kind,
    )
    record(
        "mcp.cortex.friction.closed",
        assertion_id=assertion_id,
        fulfillment_assertion_id=fulfillment_id,
        resolution_kind=resolution_kind,
        agent=agent,
    )

    return {
        "status": "closed",
        "assertion_id": assertion_id,
        "fulfillment_assertion_id": fulfillment_id,
        "resolution_kind": resolution_kind,
        "item": new_item,
        **({"promotion": promotion} if promotion else {}),
    }
