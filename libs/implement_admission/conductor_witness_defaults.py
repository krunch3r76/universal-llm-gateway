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
from implement_admission.conductor_witness_types import FoldDeps, Witness

_BUS_TIMEOUT_S = 8.0
_TURNS_PAGE = 80


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
) -> bool:
    """True when a SCORE_RESURFACE subject exists after the G3 journal time."""
    cutoff = _parse_iso(after_written_at)
    for turn in turns:
        subject = str(turn.get("subject") or "")
        if not subject.upper().startswith("SCORE_RESURFACE"):
            continue
        if cutoff is None:
            return True
        created = _parse_iso(str(turn.get("created_at") or ""))
        if created is not None and created > cutoff:
            return True
    return False


class DefaultWitnessCortex:
    """Production cortex reader for conductor witness fold."""

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        from cortex_store.card import get_entity_card
        from cortex_store.db import cortex_conn

        intent = kwargs.pop("intent", "card")
        with cortex_conn() as conn:
            if intent == "card":
                return get_entity_card(conn, entity_id=entity_id)
            from cortex_store.entity_read import get_entity_impl

            return get_entity_impl(conn, entity_id=entity_id, **kwargs)

    def list_relationships(
        self,
        entity_id: str,
        *,
        type_id: str | None = None,
    ) -> list[dict[str, Any]]:
        from cortex_store.routes.relationships import list_relationships

        result = list_relationships(entity_id=entity_id, type_id=type_id, limit=200)
        return [item.model_dump() for item in result.items]


class DefaultWitnessGit:
    """Git reader using merge-base --is-ancestor for G6 landed witness."""

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
    ) -> bool:
        from urllib.parse import urlencode

        from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

        token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        qs = urlencode({"thread": str(thread_id), "last": _TURNS_PAGE})
        try:
            with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=_BUS_TIMEOUT_S) as client:
                resp = client.get(f"/turns?{qs}", headers=headers)
                if resp.status_code >= 400:
                    return False
                payload = resp.json()
        except (OSError, ValueError):
            return False
        turns = payload.get("turns") if isinstance(payload, dict) else None
        if not isinstance(turns, list):
            return False
        return score_resurface_in_turns(turns, after_written_at=after_written_at)

    def nested_implement_has_commits(self, *, nest_under_dispatch_id: str) -> bool:
        _ = nest_under_dispatch_id
        return False


def fold_deps_for_admit(
    source_ref: str,
    *,
    cortex: Any,
    repo: Path,
    summon_mode: str | None = None,
    summoning_thread_id: str | None = None,
) -> FoldDeps:
    """Live fold readers for Stargate conductor materialize."""
    return FoldDeps(
        cortex=cortex,
        bus=DefaultWitnessBus(),
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
    return row_witnesses(slug, tip_body=body or "", deps=deps, files_root=root)
