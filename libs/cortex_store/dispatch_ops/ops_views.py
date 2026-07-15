"""view_render dispatch op — register, refresh, full, and read_asof for derived views."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..db import WRITE_LOCK, cortex_conn, query
from ..entity_crud import update_entity_impl
from ..event_publisher import cortex_view_rendered
from ..routes.relationships import _create_relationship_impl, _delete_relationship_impl
from ._shared import _FILES_ROOT, record
from ._views import (
    archive_revision,
    build_document_body,
    build_stamp,
    check_attr_edge_parity,
    compute_core_hash,
    extract_core_sections,
    load_recipe,
    parse_recipe_id,
    parse_stamp_from_body,
    read_asof_instance,
    render_core_sections,
    snapshot_for_scope,
    validate_citation_grammar,
    view_registration_attrs,
)
from ._views.recipe import ViewRecipeError

_ROOT_REQUIRED_PROFILES = frozenset({"matter_charter", "matter_doctrine"})
_VALID_MODES = frozenset({"register", "refresh", "full", "read_asof"})


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": message, "code": code, **extra}


def _decode_attrs(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _load_document(conn, document_id: str) -> dict[str, Any] | None:
    rows = query(
        conn,
        "SELECT id, type, source_uri, attributes, content_hash FROM entities WHERE id = ?",
        (document_id,),
    )
    return dict(rows[0]) if rows else None


def _section_stamps(
    recipe: dict[str, Any],
    core_sections: dict[str, str],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    stamps: dict[str, Any] = {}
    for section in recipe.get("sections", []):
        section_id = section["section_id"]
        if section_id not in core_sections and section.get("core", True):
            continue
        stamps[section_id] = {
            "watched_set": section.get("watched_set"),
            "snapshot": snapshot,
            "core_present": section_id in core_sections,
        }
    return stamps


def _delta_sections(
    conn,
    recipe: dict[str, Any],
    *,
    root_id: str | None,
    prior_snapshot: dict[str, Any],
) -> set[str]:
    current = snapshot_for_scope(conn, recipe, root_id)
    if current.get("max_assertion_id", 0) <= prior_snapshot.get("max_assertion_id", 0):
        return set()
    touched: set[str] = set()
    for section in recipe.get("sections", []):
        if section.get("core", True):
            touched.add(section["section_id"])
    return touched


def _write_head_file(source_uri: str, body: str) -> str:
    rel = source_uri.removeprefix("cortex://")
    path = _FILES_ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _create_derived_from(document_id: str, root_id: str) -> dict[str, Any]:
    return _create_relationship_impl(
        {
            "source_id": document_id,
            "target_id": root_id,
            "type_id": "derived_from",
        }
    )


def _rollback_relationship(rel_id: int | None) -> None:
    if rel_id is None:
        return
    _delete_relationship_impl(rel_id)


def _emit_rendered(
    *,
    document_id: str,
    view_rev: int,
    mode: str,
    sections_repaired: list[str],
    delta: dict[str, list[Any]],
) -> None:
    try:
        cortex_view_rendered(
            document_id=document_id,
            view_rev=view_rev,
            mode=mode,
            sections_repaired_count=len(sections_repaired),
            delta_create_count=len(delta.get("create", [])),
            delta_update_count=len(delta.get("update", [])),
            delta_delete_count=len(delta.get("delete", [])),
        )
    except Exception:
        record(
            "cortex.view.rendered",
            document_id=document_id,
            view_rev=view_rev,
            mode=mode,
            sections_repaired_count=len(sections_repaired),
        )


def _op_view_render(
    document_id: str | None = None,
    mode: str = "refresh",
    root_id: str | None = None,
    view_profile: str | None = None,
    narrative_sections: dict[str, str] | None = None,
    as_of_system: str | None = None,
    as_of_valid: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Render or refresh a derived view document from graph state and recipe data."""
    if as_of_valid:
        return _err(
            "as_of_valid_unsupported",
            "as_of_valid graph replay is deferred in v1; use as_of_system",
        )
    if not document_id:
        return _err("document_not_found", "document_id is required")
    if mode not in _VALID_MODES:
        return _err("unknown_view_profile", f"Invalid mode {mode!r}")

    narrative_sections = narrative_sections or {}

    with WRITE_LOCK:
        with cortex_conn() as conn:
            doc = _load_document(conn, document_id)
            if not doc:
                return _err("document_not_found", f"Document {document_id!r} not found")
            if doc.get("type") != "document":
                return _err("document_not_found", f"Entity {document_id!r} is not a document")

            attrs = _decode_attrs(doc.get("attributes"))
            profile = view_profile or attrs.get("view_profile")
            recipe_id = attrs.get("derivation_recipe")
            if mode == "read_asof":
                if not as_of_system:
                    return _err("as_of_instance_not_found", "as_of_system is required")
                body = read_asof_instance(
                    _FILES_ROOT,
                    document_id=document_id,
                    as_of_system=as_of_system,
                )
                if body is None:
                    return _err(
                        "as_of_instance_not_found",
                        f"No archived instance covers {as_of_system!r}",
                    )
                return {
                    "document_id": document_id,
                    "mode": mode,
                    "as_of_system": as_of_system,
                    "body": body,
                }

            if mode == "register":
                if not profile:
                    return _err("unknown_view_profile", "view_profile is required for register")
                if profile in _ROOT_REQUIRED_PROFILES and not root_id:
                    return _err(
                        "view_root_required",
                        f"root_id is required for profile {profile!r}",
                    )
                try:
                    _, version = parse_recipe_id(f"recipe:{profile}/v1")
                    recipe = load_recipe(profile, version)
                except ViewRecipeError as exc:
                    return _err(exc.code, exc.message)
                recipe_id = f"recipe:{profile}/v1"
            else:
                if not attrs.get("derived_from_snapshot"):
                    return _err("view_not_registered", f"View {document_id!r} is not registered")
                if not profile or not recipe_id:
                    return _err("view_not_registered", "Missing view_profile or derivation_recipe")
                try:
                    prof, version = parse_recipe_id(recipe_id)
                    recipe = load_recipe(prof, version)
                    if prof != profile:
                        return _err(
                            "recipe_profile_mismatch",
                            f"Recipe {recipe_id!r} does not match view_profile {profile!r}",
                        )
                except ViewRecipeError as exc:
                    return _err(exc.code, exc.message)

            source_uri = doc.get("source_uri")
            if not source_uri:
                return _err("document_not_found", f"Document {document_id!r} has no source_uri")

            rel_id: int | None = None
            prior_body = ""
            rel_path = source_uri.removeprefix("cortex://")
            head_path = _FILES_ROOT / rel_path
            if head_path.is_file():
                prior_body = head_path.read_text(encoding="utf-8")

            prior_core = extract_core_sections(prior_body) if prior_body else {}
            prior_snapshot = attrs.get("derived_from_snapshot") or {}
            view_rev = int(attrs.get("view_rev") or 0)

            if mode == "register":
                snapshot = snapshot_for_scope(conn, recipe, root_id)
                core_sections = render_core_sections(
                    conn, recipe, root_id=root_id, snapshot=snapshot
                )
                section_stamp_map = _section_stamps(recipe, core_sections, snapshot)
                core_hash = compute_core_hash(core_sections)
                for section in recipe.get("sections", []):
                    if section.get("core", False):
                        continue
                    sid = section["section_id"]
                    if sid in narrative_sections and not section.get("citation_exempt"):
                        if not validate_citation_grammar(narrative_sections[sid]):
                            return _err(
                                "citation_grammar_violation",
                                f"Narrative section {sid!r} lacks required citation token",
                            )
                if root_id:
                    rel_result = _create_derived_from(document_id, root_id)
                    if "error" in rel_result:
                        return rel_result
                    rel_id = rel_result.get("id")
                view_rev = 1
                stamp = build_stamp(
                    document_id=document_id,
                    view_profile=profile,
                    derivation_recipe=recipe_id,
                    view_rev=view_rev,
                    core_hash=core_hash,
                    content_hash="",
                    derived_from_snapshot=snapshot,
                    section_stamps=section_stamp_map,
                    prior_revision_uri=None,
                    mode=mode,
                    agent=agent,
                    session_id=session_id,
                )
                body = build_document_body(stamp, core_sections, narrative_sections)
                content_hash = _write_head_file(source_uri, body)
                stamp["generated"]["content_hash"] = content_hash
                reg_attrs = view_registration_attrs(
                    view_profile=profile,
                    derivation_recipe=recipe_id,
                    derived_from_snapshot=snapshot,
                    view_rev=view_rev,
                    core_hash=core_hash,
                    section_stamps=section_stamp_map,
                )
                try:
                    update_entity_impl(
                        conn,
                        entity_id=document_id,
                        updates={"attributes": reg_attrs, "content_hash": content_hash},
                    )
                except Exception as exc:
                    _rollback_relationship(rel_id)
                    if head_path.is_file():
                        head_path.unlink(missing_ok=True)
                    return _err("view_not_registered", f"Registration failed: {exc}")
                if root_id and not check_attr_edge_parity(
                    conn, document_id=document_id, root_id=root_id, profile=profile
                ):
                    _rollback_relationship(rel_id)
                    update_entity_impl(
                        conn,
                        entity_id=document_id,
                        updates={"attributes": attrs},
                    )
                    if head_path.is_file():
                        head_path.unlink(missing_ok=True)
                    return _err(
                        "view_not_registered",
                        "derived_from edge missing after registration (partial failure)",
                    )
                delta = {"create": list(core_sections.keys()), "update": [], "delete": []}
                _emit_rendered(
                    document_id=document_id,
                    view_rev=view_rev,
                    mode=mode,
                    sections_repaired=list(core_sections.keys()),
                    delta=delta,
                )
                return {
                    "document_id": document_id,
                    "view_rev": view_rev,
                    "mode": mode,
                    "core_hash": core_hash,
                    "written_sha256": content_hash,
                    "sections_repaired": list(core_sections.keys()),
                    "delta": delta,
                    "archived_revision_uri": None,
                    "stamp": stamp,
                }

            current_snapshot = snapshot_for_scope(conn, recipe, root_id or None)
            if mode == "refresh":
                delta_ids = _delta_sections(
                    conn,
                    recipe,
                    root_id=root_id,
                    prior_snapshot=prior_snapshot,
                )
                for sid, text in narrative_sections.items():
                    if sid not in delta_ids:
                        return _err(
                            "anti_amnesia_violation",
                            f"Narrative update for non-delta section {sid!r} rejected",
                        )
                if not delta_ids and not narrative_sections:
                    core_hash = compute_core_hash(prior_core) if prior_core else attrs.get("core_hash")
                    return {
                        "document_id": document_id,
                        "view_rev": view_rev,
                        "mode": mode,
                        "core_hash": core_hash,
                        "written_sha256": doc.get("content_hash"),
                        "sections_repaired": [],
                        "delta": {"create": [], "update": [], "delete": []},
                        "archived_revision_uri": None,
                        "stamp": parse_stamp_from_body(prior_body),
                    }
                repair_ids = delta_ids or set()
            else:
                repair_ids = {s["section_id"] for s in recipe.get("sections", []) if s.get("core", True)}

            new_core = dict(prior_core)
            repaired = render_core_sections(
                conn,
                recipe,
                root_id=root_id,
                snapshot=current_snapshot,
                section_ids=repair_ids,
            )
            new_core.update(repaired)
            core_hash = compute_core_hash(new_core)

            for section in recipe.get("sections", []):
                if section.get("core", False):
                    continue
                sid = section["section_id"]
                if sid in narrative_sections and not section.get("citation_exempt"):
                    if not validate_citation_grammar(narrative_sections[sid]):
                        return _err(
                            "citation_grammar_violation",
                            f"Narrative section {sid!r} lacks required citation token",
                        )

            archived_uri = None
            if prior_body and (repaired or narrative_sections):
                archived_uri = archive_revision(
                    _FILES_ROOT,
                    document_id=document_id,
                    view_rev=view_rev,
                    body=prior_body,
                )
                view_rev += 1

            section_stamp_map = _section_stamps(recipe, new_core, current_snapshot)
            prior_rev_uri = attrs.get("prior_revision_uri")
            stamp = build_stamp(
                document_id=document_id,
                view_profile=profile,
                derivation_recipe=recipe_id,
                view_rev=view_rev,
                core_hash=core_hash,
                content_hash="",
                derived_from_snapshot=current_snapshot,
                section_stamps=section_stamp_map,
                prior_revision_uri=archived_uri or prior_rev_uri,
                mode=mode,
                agent=agent,
                session_id=session_id,
            )
            merged_narrative = {}
            if prior_body:
                for section in recipe.get("sections", []):
                    if section.get("core", True):
                        continue
                    sid = section["section_id"]
                    if sid in narrative_sections:
                        merged_narrative[sid] = narrative_sections[sid]
            else:
                merged_narrative = dict(narrative_sections)
            body = build_document_body(stamp, new_core, merged_narrative)
            content_hash = _write_head_file(source_uri, body)
            stamp["generated"]["content_hash"] = content_hash
            update_attrs = view_registration_attrs(
                view_profile=profile,
                derivation_recipe=recipe_id,
                derived_from_snapshot=current_snapshot,
                view_rev=view_rev,
                core_hash=core_hash,
                section_stamps=section_stamp_map,
                prior_revision_uri=archived_uri or prior_rev_uri,
            )
            update_entity_impl(
                conn,
                entity_id=document_id,
                updates={"attributes": update_attrs, "content_hash": content_hash},
            )
            delta = {
                "create": [k for k in repaired if k not in prior_core],
                "update": [k for k in repaired if k in prior_core and repaired[k] != prior_core.get(k)],
                "delete": [k for k in prior_core if k not in new_core],
            }
            _emit_rendered(
                document_id=document_id,
                view_rev=view_rev,
                mode=mode,
                sections_repaired=list(repaired.keys()),
                delta=delta,
            )
            return {
                "document_id": document_id,
                "view_rev": view_rev,
                "mode": mode,
                "core_hash": core_hash,
                "written_sha256": content_hash,
                "sections_repaired": list(repaired.keys()),
                "delta": delta,
                "archived_revision_uri": archived_uri,
                "stamp": stamp,
            }


__all__ = ["_op_view_render"]
