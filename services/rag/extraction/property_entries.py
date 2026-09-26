"""Map extracted knowledge into property-index quads.

Pure transformation used by ``extraction.chroma_source.extract_source`` after a
chunk is extracted: each entity name, type, facet, relation and topic becomes a
``prop.<kind>@@<value>`` key paired with the chunk id, scope and source path,
ready for ``PropertyIndex.add_batch_with_scope``.
"""

from __future__ import annotations

from services.rag.knowledge_extractor import ExtractedKnowledge


def build_property_entries(
    knowledge: ExtractedKnowledge,
    chunk_id: str,
    scope: str = "all",
    source: str = "",
) -> list[tuple[str, str, str, str]]:
    """Flatten one chunk's ExtractedKnowledge into property-index key quads.

    Emits one ``(key, chunk_id, scope, source)`` tuple per entity name
    (``prop.name@@``), entity type (``prop.type@@``), facet
    (``prop.facet@@name:value``), relation (``prop.rel@@entity>predicate>target``)
    and topic (``prop.topic@@``). No I/O; the caller persists the result.
    """
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
