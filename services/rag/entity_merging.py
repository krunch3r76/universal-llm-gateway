"""Cross-chunk entity merging for structured RAG context.

Merges entities with the same name (case-insensitive) across retrieved chunks,
unioning their types and facets. Produces a structured entity summary for
inclusion in the answer pipeline's context.

Technique adapted from Microsoft typeagent-py (MIT license).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from services.rag.knowledge_extractor import Entity, Facet, Relation


@dataclass(slots=True, kw_only=True)
class MergedEntity:
    """Entity unified across multiple chunks."""

    name: str
    types: list[str]
    facets: list[Facet]
    source_chunk_count: int


def merge_entities(entities: list[Entity]) -> list[MergedEntity]:
    """Merge entities with the same name across chunks.

    Case-insensitive matching on name. Unions types and facets
    (facets keyed by name — first value wins for duplicates).
    Preserves original casing from the first occurrence.
    """
    by_key: dict[str, MergedEntity] = {}

    for entity in entities:
        key = entity.name.lower()
        if key in by_key:
            existing = by_key[key]
            existing.types = list(dict.fromkeys(existing.types + entity.type))
            facet_names = {f.name for f in existing.facets}
            for facet in entity.facets:
                if facet.name not in facet_names:
                    existing.facets.append(facet)
                    facet_names.add(facet.name)
            existing.source_chunk_count += 1
        else:
            by_key[key] = MergedEntity(
                name=entity.name,
                types=list(entity.type),
                facets=list(entity.facets),
                source_chunk_count=1,
            )

    return list(by_key.values())


def format_entity_context(merged: list[MergedEntity]) -> str:
    """Format merged entities as a structured context section.

    Returns empty string if no entities. Designed for injection into
    the answer prompt alongside source chunks.
    """
    if not merged:
        return ""

    lines: list[str] = ["## Key Entities\n"]
    for entity in merged:
        type_str = ", ".join(entity.types) if entity.types else "unknown"
        line = f"- **{entity.name}** ({type_str})"
        if entity.facets:
            facet_parts = [f"{f.name}: {f.value}" for f in entity.facets]
            line += f" — {'; '.join(facet_parts)}"
        lines.append(line)

    return "\n".join(lines)


@dataclass(slots=True, kw_only=True)
class MergedRelation:
    """Deduplicated directed edge between entities."""

    subject: str
    predicate: str
    target: str
    source_chunk_count: int


def merge_relations(entities: list[Entity]) -> list[MergedRelation]:
    """Merge relations across chunks.

    Deduplicates by (subject.lower, predicate.lower, target.lower).
    Preserves first-seen casing. Counts contributing chunks.
    """
    by_key: dict[tuple[str, str, str], MergedRelation] = {}
    for entity in entities:
        for rel in entity.relations:
            key = (entity.name.lower(), rel.predicate.lower(), rel.target.lower())
            if key in by_key:
                by_key[key].source_chunk_count += 1
            else:
                by_key[key] = MergedRelation(
                    subject=entity.name,
                    predicate=rel.predicate,
                    target=rel.target,
                    source_chunk_count=1,
                )
    return list(by_key.values())


def format_relation_context(merged: list[MergedRelation]) -> str:
    """Format merged relations as a structured context section."""
    if not merged:
        return ""
    lines: list[str] = ["## Key Relationships\n"]
    for rel in merged:
        lines.append(f"- {rel.subject} —[{rel.predicate}]→ {rel.target}")
    return "\n".join(lines)


def extract_topics_from_metadata(metadata: dict[str, object]) -> list[str]:
    """Parse topics from a chunk's extraction metadata field."""
    raw = metadata.get("extraction")
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    elif isinstance(raw, dict):
        data = raw
    else:
        return []
    topics = data.get("topics", [])
    return [t for t in topics if isinstance(t, str)]


def merge_topics(topics: list[str]) -> list[tuple[str, int]]:
    """Merge topics across chunks by name (case-insensitive), tracking frequency.

    Returns (topic, count) pairs sorted by frequency descending.
    """
    counts: dict[str, int] = {}
    originals: dict[str, str] = {}
    for topic in topics:
        key = topic.lower().strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        if key not in originals:
            originals[key] = topic.strip()
    return sorted(
        [(originals[k], c) for k, c in counts.items()],
        key=lambda x: x[1],
        reverse=True,
    )


def format_topic_context(merged: list[tuple[str, int]]) -> str:
    """Format merged topics as a structured context section."""
    if not merged:
        return ""
    lines: list[str] = ["## Key Topics\n"]
    for topic, count in merged:
        suffix = f" ({count})" if count > 1 else ""
        lines.append(f"- {topic}{suffix}")
    return "\n".join(lines)


def extract_entities_from_metadata(
    metadata: dict[str, object],
) -> list[Entity]:
    """Parse entities from a chunk's extraction metadata field.

    Returns empty list if extraction metadata is absent or malformed.
    The extraction field is a JSON-serialized dict stored by extraction_wiring.py.
    """
    raw = metadata.get("extraction")
    if not raw:
        return []

    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    elif isinstance(raw, dict):
        data = raw
    else:
        return []

    entities: list[Entity] = []
    for ent_data in data.get("entities", []):
        if not isinstance(ent_data, dict) or "name" not in ent_data:
            continue
        name = ent_data["name"]
        etype = ent_data.get("type", [])
        if not isinstance(name, str) or not isinstance(etype, list):
            continue
        facets: list[Facet] = []
        for raw_f in ent_data.get("facets") or []:
            if isinstance(raw_f, dict) and "name" in raw_f and "value" in raw_f:
                facets.append(Facet(name=raw_f["name"], value=str(raw_f["value"])))
        relations: list[Relation] = []
        for raw_r in ent_data.get("relations") or []:
            if isinstance(raw_r, dict) and "predicate" in raw_r and "target" in raw_r:
                relations.append(
                    Relation(predicate=raw_r["predicate"], target=str(raw_r["target"]))
                )
        entities.append(
            Entity(
                name=name,
                type=[t for t in etype if isinstance(t, str)],
                facets=facets,
                relations=relations,
            )
        )
    return entities
