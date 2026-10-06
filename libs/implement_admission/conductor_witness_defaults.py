"""Production witness readers for conductor fold and closeout grading."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.conductor_score_journal import read_tip
from implement_admission.conductor_witness_table import row_witnesses
from implement_admission.conductor_witness_types import (
    FoldDeps,
    ScoreResurfaceRead,
    Witness,
    WitnessNestedImplement,
)

_BUS_TIMEOUT_S = 8.0
_CORTEX_TIMEOUT_S = 15.0
_TURNS_PAGE = 80


class WitnessCortexUnavailable(RuntimeError):
    """Cortex API read failed. Fold must not report a clean OPEN."""


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def score_resurface_in_turns(
    turns: list[dict[str, Any]],
    *,
    after_written_at: str | None,
    slug: str | None = None,
    exec_id: str | None = None,
    review_sha: str | None = None,
) -> bool:
    """True when a mission-bound SCORE_RESURFACE exists after the G3 journal.

    Slug must appear in the subject when given (shared summoning roots host
    several missions). When the tip recorded a CDP exec or review sha, the
    turn body must cite it. No G3 cutoff does not admit a foreign resurface.
    """
    cutoff = _parse_iso(after_written_at)
    slug_token = (slug or "").strip().lower()
    exec_token = (exec_id or "").strip().lower()
    sha_token = (review_sha or "").strip().lower()
    for turn in turns:
        subject = str(turn.get("subject") or "")
        if not subject.upper().startswith("SCORE_RESURFACE"):
            continue
        if slug_token and slug_token not in subject.lower():
            continue
        body = str(turn.get("body") or "")
        blob = f"{subject}\n{body}".lower()
        if exec_token and exec_token not in blob:
            continue
        if sha_token and sha_token not in blob:
            continue
        if cutoff is None:
            return True
        created = _parse_iso(str(turn.get("created_at") or ""))
        if created is not None and created > cutoff:
            return True
    return False


def _cortex_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Read cortex through the API (UDS or HTTP). Never open the sqlite file."""
    from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT_S) as client:
            resp = client.post(
                "/dispatch", json={"tool": tool, "arguments": arguments}
            )
            if resp.status_code >= 400:
                raise WitnessCortexUnavailable(
                    f"cortex {tool} HTTP {resp.status_code}: {resp.text[:300]}"
                )
            payload = resp.json()
    except WitnessCortexUnavailable:
        raise
    except (OSError, ValueError) as exc:
        raise WitnessCortexUnavailable(f"cortex {tool} failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise WitnessCortexUnavailable(f"cortex {tool} returned a non-object")
    if payload.get("error"):
        raise WitnessCortexUnavailable(f"cortex {tool}: {payload.get('error')}")
    return payload


class DefaultWitnessCortex:
    """Production cortex reader for conductor witness fold.

    Reads go through the cortex API. Dispatch HOME cannot open the sqlite
    file (``unable to open database file``); a down API raises
    ``WitnessCortexUnavailable`` so the fold can report failure.
    """

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        arguments: dict[str, Any] = {"entity_id": entity_id}
        arguments.update(kwargs)
        return _cortex_dispatch("entity_get", arguments)

    def list_relationships(
        self,
        entity_id: str,
        *,
        type_id: str | None = None,
    ) -> list[dict[str, Any]]:
        arguments: dict[str, Any] = {"entity_id": entity_id, "limit": 200}
        if type_id is not None:
            arguments["type_id"] = type_id
        payload = _cortex_dispatch("relationships", arguments)
        items = payload.get("items")
        if not isinstance(items, list):
            raise WitnessCortexUnavailable("cortex relationships: missing items")
        return [item for item in items if isinstance(item, dict)]


class DefaultWitnessGit:
    """Git reader using merge-base --is-ancestor for G7 landed witness."""

    def __init__(self, repo: Path) -> None:
        self._repo = repo.resolve()

    def is_ancestor(self, commit: str, ref: str) -> bool:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, ref],
            cwd=self._repo,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0


class DefaultWitnessBus:
    """Agent-bus HTTP reader for attended G5 SCORE_RESURFACE."""

    def has_score_resurface_after(
        self,
        *,
        thread_id: str,
        after_written_at: str | None,
        slug: str | None = None,
        exec_id: str | None = None,
        review_sha: str | None = None,
    ) -> ScoreResurfaceRead:
        from urllib.parse import urlencode

        from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

        token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
        if not token:
            return "unknown"
        headers = {"Authorization": f"Bearer {token}"}
        qs = urlencode({"thread": str(thread_id), "last": _TURNS_PAGE})
        try:
            with make_sync_client(
                DEFAULT_AGENT_BUS_URL, timeout=_BUS_TIMEOUT_S
            ) as client:
                resp = client.get(f"/turns?{qs}", headers=headers)
                if resp.status_code >= 400:
                    return "unknown"
                payload = resp.json()
        except (OSError, ValueError):
            return "unknown"
        turns = payload.get("turns") if isinstance(payload, dict) else None
        if not isinstance(turns, list):
            return "unknown"
        hit = score_resurface_in_turns(
            turns,
            after_written_at=after_written_at,
            slug=slug,
            exec_id=exec_id,
            review_sha=review_sha,
        )
        return "found" if hit else "not_found"


def fold_deps_for_admit(
    source_ref: str,
    *,
    cortex: Any,
    repo: Path,
    summon_mode: str | None = None,
    summoning_thread_id: str | None = None,
    nested_implement: WitnessNestedImplement | None = None,
) -> FoldDeps:
    """Live fold readers for Stargate conductor materialize."""
    return FoldDeps(
        cortex=cortex,
        bus=DefaultWitnessBus(),
        nested_implement=nested_implement,
        git=DefaultWitnessGit(repo),
        source_ref=source_ref,
        summon_mode=summon_mode,
        summoning_thread_id=summoning_thread_id,
        repo=repo,
    )


def closeout_witnesses_for_slug(
    slug: str,
    *,
    tip_body: str | None,
    deps: FoldDeps,
    files_root: Path | None = None,
) -> dict[str, Witness | None]:
    """Witness map for closeout grading — uses tip when present."""
    root = files_root if files_root is not None else cortex_files_root()
    body = tip_body
    if body is None:
        tip = read_tip(slug, files_root=root)
        body = tip[0] if tip else ""
    try:
        return row_witnesses(slug, tip_body=body or "", deps=deps, files_root=root)
    except WitnessCortexUnavailable:
        raise
