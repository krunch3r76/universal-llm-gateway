"""Dataclasses for session transcript and CDP envelope serialization."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    n: int
    role: str
    body: str
    tools_digest: str | None = None


@dataclass(frozen=True)
class AttachmentRef:
    turn: int
    name: str
    ref: str
    media: str
    note: str = ""


@dataclass
class Budget:
    total: int = 32 * 1024
    rollup: int = 4 * 1024
    index: int = 4 * 1024
    live: int = 20 * 1024
    per_turn_cap: int = 4 * 1024
    attachments: int = 2 * 1024
    index_line_cap: int = 60


@dataclass
class SessionDoc:
    session_id: str
    meta: dict[str, str]
    rollup_text: str
    index_lines: list[str]
    archive_map_lines: list[str]
    turns: list[Turn]


@dataclass
class SessionForSeal:
    session_id: str
    turn_count: int
    transcript_uri: str
    rollup_text: str
    index_lines: list[str]
    turns: list[Turn]
    attachments: list[AttachmentRef]
    refs: list[str]
    archive_uri: str = "none"
    sealed_at_turn: int | None = None


@dataclass(frozen=True)
class SealResult:
    prompt_text: str
    k_used: int
    size_bytes: int


class SessionStoreError(ValueError):
    """Base error for session_store reject paths."""


class SchemaError(SessionStoreError):
    """Malformed transcript schema."""


class ImmutableArchiveError(SessionStoreError):
    """Writer refused to mutate an immutable archive."""


class EnvelopeOverBudget(SessionStoreError):  # noqa: N818 — spec name
    """Envelope exceeds budget after K shrink to floor."""

    def __init__(self, size_bytes: int, budget_total: int, k_used: int) -> None:
        self.size_bytes = size_bytes
        self.budget_total = budget_total
        self.k_used = k_used
        super().__init__(
            f"envelope {size_bytes} bytes exceeds budget {budget_total} at k={k_used}"
        )


class InvalidRefError(SessionStoreError):
    """A URI ref is not cortex://."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        super().__init__(f"ref must be cortex://, got {ref!r}")
