"""Shared chunk-spec dataclass for authority-class chunkers.

Lives in its own module so the per-class chunkers can import it without
the package ``__init__`` (which itself imports the chunkers) being a
partial-initialization hazard.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChunkSpec:
    """One chunk emitted by an authority-class chunker.

    ``pinpoint`` is the source's native subdivision label (e.g. ``f-1-B``
    for statute (f)(1)(B)) — opaque to the resolver, used as the URI
    fragment per docs/architecture/entity-backed-claim-provenance.md
    § 2.2 / § 2.3. ``None`` for chunkers that don't address sub-document
    units (default paragraph splitter).
    """

    text: str
    pinpoint: str | None = None
