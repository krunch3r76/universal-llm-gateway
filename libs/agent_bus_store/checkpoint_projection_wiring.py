"""Post-path wiring for CHECKPOINT projection resolvers."""

from __future__ import annotations

import re

from .checkpoint_citation_lint import CitationToken
from .checkpoint_projection import (
    ArtifactAnchor,
    ChildThreadRow,
    EntityAssertionRow,
    ProjectionResolvers,
    is_checkpoint_subject,
    project_checkpoint_body,
)
from .checkpoint_projection_producers import (
    ProducerDispatchRow,
    filter_cp_projection_producer_links,
)


def build_post_resolvers(*, root_thread: str) -> ProjectionResolvers:
    """Default resolver bundle for the store HTTP post path."""
    from .db import get_thread, get_thread_lineage, get_thread_turn_count
    from .db.lane_associations import get_current_lane

    house_thread = root_thread

    _lineage_cache: list = []

    def _lineage():
        if not _lineage_cache:
            _lineage_cache.append(
                get_thread_lineage(house_thread, include_dispatch_links=True)
            )
        return _lineage_cache[0]

    def _child_row(thread_id: str) -> ChildThreadRow | None:
        row = get_thread(thread_id)
        if row is None:
            return None
        lane = get_current_lane(thread_id=thread_id)
        return ChildThreadRow(
            thread_id=thread_id,
            status=str(row.get("status", "unknown")),
            last_turn=get_thread_turn_count(thread_id),
            lane_role=lane.get("lane_role"),
            parent_thread_id=lane.get("parent_thread"),
        )

    def _child_registry(
        *, root_thread: str, cited_thread_ids: tuple[str, ...]
    ) -> tuple[tuple[ChildThreadRow, ...], tuple[ChildThreadRow, ...]]:
        # Substantiated bucket: one shared live-lineage primitive (G2) instead
        # of re-deriving "what are my children" independently here.
        lineage = _lineage()
        substantiated = tuple(
            ChildThreadRow(
                thread_id=child.thread_id,
                status=child.status,
                last_turn=child.turn_count,
                lane_role=child.lane_role,
                parent_thread_id=child.parent_thread_id,
            )
            for child in (lineage.children if lineage is not None else ())
        )
        substantiated_set = {child.thread_id for child in substantiated}

        # Cited bucket: citation-token markdown scrape — a different,
        # prose-level concern, left untouched.
        cited: list[ChildThreadRow] = []
        for thread_id in cited_thread_ids:
            if thread_id == root_thread or thread_id in substantiated_set:
                continue
            child = _child_row(thread_id)
            if child is not None:
                cited.append(child)
        return substantiated, tuple(cited)

    def _producer_registry(*, root_thread: str) -> tuple[ProducerDispatchRow, ...]:
        del root_thread
        lineage = _lineage()
        if lineage is None:
            return ()
        return filter_cp_projection_producer_links(
            lineage.dispatch_links,
            lane_thread_id=lineage.thread_id,
        )

    def _artifact_sha(uri: str) -> ArtifactAnchor | None:
        """Resolve cortex:// or workspaces:// to sha256 at post time.

        Reads ``CORTEX_FILES_ROOT`` / ``WORKSPACES_ROOT`` at call time so a
        late-set env (agent_bus process) is not frozen behind cortex_store's
        import-time ``_FILES_ROOT`` default (CP11 CCL-4 seam).
        """
        import hashlib
        import os
        from pathlib import Path

        if uri.startswith("cortex://"):
            root_env = os.environ.get("CORTEX_FILES_ROOT")
            if root_env:
                files_root = Path(root_env)
            else:
                from cortex_store.dispatch_ops._shared import _FILES_ROOT

                files_root = _FILES_ROOT
            path = files_root / uri.removeprefix("cortex://")
        elif uri.startswith("workspaces://"):
            ws_root = Path(
                os.environ.get("WORKSPACES_ROOT", "/mnt/torus/projects")
            )
            path = ws_root / uri.removeprefix("workspaces://")
        else:
            return None
        if not path.is_file():
            return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return ArtifactAnchor(uri=uri, sha256=digest)

    def _citation_row(token: CitationToken) -> EntityAssertionRow | None:
        from cortex_store.db import cortex_conn, query
        from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_get

        if token.kind == "assertion":
            payload = _op_assertion_get(assertion_id=int(token.identifier))
            if payload.get("error"):
                return None
            entity_id = str(payload.get("entity_id") or "?")
            newer_on_entity = False
            with cortex_conn() as conn:
                newer = query(
                    conn,
                    "SELECT id FROM assertions WHERE entity_id = ? AND id > ? "
                    "AND superseded_by IS NULL LIMIT 1",
                    (entity_id, int(token.identifier)),
                )
                newer_on_entity = bool(newer)
            score = payload.get("confidence_score")
            return EntityAssertionRow(
                row_id=f"a:{token.identifier}",
                entity=entity_id,
                claim_head=str(payload.get("claim") or ""),
                confidence=float(score) if score is not None else None,
                superseded_by=(
                    f"a:{payload['superseded_by']}"
                    if payload.get("superseded_by")
                    else None
                ),
                valid_until=payload.get("valid_until"),
                newer_on_entity=newer_on_entity,
            )

        kind_map = {
            "todo": "todo",
            "task": "task",
            "decision": "decision",
            "plan": "plan",
            "transcript": "transcript",
        }
        prefix = kind_map.get(token.kind)
        if prefix is None:
            return None
        entity_id = f"{prefix}:{token.identifier}"
        with cortex_conn() as conn:
            rows = query(
                conn,
                "SELECT id, name, description FROM entities WHERE id = ?",
                (entity_id,),
            )
        if not rows:
            return None
        entity = rows[0]
        claim_head = (
            "sealed window"
            if token.kind == "transcript"
            else str(entity.get("name") or entity.get("description") or "")
        )
        return EntityAssertionRow(
            row_id=f"transcript:{token.identifier}"
            if token.kind == "transcript"
            else entity_id,
            entity=entity_id,
            claim_head=claim_head,
        )

    return ProjectionResolvers(
        child_registry=_child_registry,
        producer_registry=_producer_registry,
        artifact_sha=_artifact_sha,
        citation_row=_citation_row,
    )


def _refresh_transcript_projection(thread: str) -> None:
    """Best-effort O15 sidecar refresh at CP post; never blocks the turn."""
    try:
        from cortex_store.dispatch_ops.ops_transcript_project import (
            run_transcript_project,
        )

        run_transcript_project(thread=thread)
    except Exception:
        logger = __import__("universal_logging", fromlist=["get_logger"]).get_logger(
            "agent_bus_store.checkpoint_projection_wiring"
        )
        logger.warning(
            "transcript_project hook failed for thread %s",
            thread,
            exc_info=True,
        )


def maybe_project_checkpoint_body(*, thread: str, subject: str, body: str) -> str:
    """Apply projection when subject is CHECKPOINT; otherwise passthrough."""
    from .db import get_thread

    row = get_thread(thread)
    tags = (row or {}).get("tags") or []
    from .checkpoint_scoreboard_wiring import assert_scoreboard_coherent

    assert_scoreboard_coherent(thread=thread, subject=subject, body=body, tags=tags)
    if is_checkpoint_subject(subject):
        from agent_bus_store.thread_classification import classify_thread

        if classify_thread(tags)["spine"] == "root" and re.search(
            r"^### User\b", body, re.MULTILINE
        ):
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "CHECKPOINT body must not contain user speech",
                    "code": "checkpoint.speech_in_body",
                    "reason": "checkpoint.speech_in_body",
                },
            )
    if not is_checkpoint_subject(subject):
        return body
    resolvers = build_post_resolvers(root_thread=thread)
    projected = project_checkpoint_body(
        root_thread=thread, residue=body, resolvers=resolvers
    )
    from agent_bus_store.house_pools import inject_pools_checkpoint_projection

    projected = inject_pools_checkpoint_projection(projected, thread)
    producers = resolvers.producer_registry(root_thread=thread)
    from .events.checkpoint_producers_projected import emit_checkpoint_producers_projected

    emit_checkpoint_producers_projected(
        thread=thread,
        producer_count=len(producers),
        execution_ids=[row.execution_id[:8] for row in producers],
    )
    _refresh_transcript_projection(thread)
    return projected


__all__ = [
    "build_post_resolvers",
    "maybe_project_checkpoint_body",
]
