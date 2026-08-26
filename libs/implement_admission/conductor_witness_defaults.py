"""Production witness readers for conductor fold and closeout grading."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.conductor_score_journal import read_tip
from implement_admission.conductor_witness_table import row_witnesses
from implement_admission.conductor_witness_types import FoldDeps, Witness


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
