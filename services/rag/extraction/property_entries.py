"""Map extracted knowledge into property-index quads."""

from __future__ import annotations

from services.rag.knowledge_extractor import ExtractedKnowledge


def build_property_entries(
    knowledge: ExtractedKnowledge,
    chunk_id: str,
    scope: str = "all",
    source: str = "",
) -> list[tuple[str, str, str, str]]:
    """Build (key, chunk_id, scope, source) quads from extracted knowledge."""
    return (
        [
            (f"prop.name@@{entity.name}", chunk_id, scope, source)
            for entity in knowledge.entities
        ]
        + [
            (f"prop.type@@{etype}", chunk_id, scope, source)
            for entity in knowledge.entities
            for etype in entity.type
        ]
        + [
            (f"prop.facet@@{facet.name}:{facet.value}", chunk_id, scope, source)
            for entity in knowledge.entities
            for facet in entity.facets
        ]
        + [
            (
                f"prop.rel@@{entity.name}>{relation.predicate}>{relation.target}",
                chunk_id,
                scope,
                source,
            )
            for entity in knowledge.entities
            for relation in entity.relations
        ]
        + [
            (f"prop.topic@@{topic}", chunk_id, scope, source)
            for topic in knowledge.topics
        ]
    )
