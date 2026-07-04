"""Cross-lane deploy gate (F2 / AC18): boot-read cutover blocked without parity evidence."""

from __future__ import annotations

from dataclasses import dataclass

from cortex_store.guidance_entity import entity_slug_from_id

# Per-pair parity-diff artifacts recorded by Lane 2 (web-inline).
# Only slugs listed here may shift boot/inject reads from ``rule:`` → ``agent_skill:``.
CUTOVER_PARITY_EVIDENCE: dict[str, str] = {
    "orchestrator-core": (
        "cortex://notes/system/threads/skill-substantiation-migration-runbook.md"
    ),
}


class BootReadCutoverError(RuntimeError):
    """Lane-1 boot-read cutover attempted before Lane-2 parity evidence exists."""


@dataclass(frozen=True, slots=True)
class CutoverStatus:
    canonical_slug: str
    eligible: bool
    evidence_uri: str | None


def cutover_status(canonical_slug: str) -> CutoverStatus:
    uri = CUTOVER_PARITY_EVIDENCE.get(canonical_slug)
    return CutoverStatus(
        canonical_slug=canonical_slug,
        eligible=uri is not None,
        evidence_uri=uri,
    )


def assert_boot_read_cutover_allowed(entity_id: str) -> None:
    """Fail-loud when boot/inject would read ``agent_skill:`` before Lane-2 parity."""
    slug = entity_slug_from_id(entity_id)
    status = cutover_status(slug)
    if status.eligible:
        return
    raise BootReadCutoverError(
        f"boot-read cutover blocked for {entity_id!r}: "
        f"no Lane-2 parity evidence for slug {slug!r} "
        f"(eligible: {sorted(CUTOVER_PARITY_EVIDENCE)})"
    )


def prefer_inject_entity_id(entity_id: str, *, cutover: bool = False) -> str:
    """Return inject entity id — ``agent_skill:`` only when cutover allowed + requested."""
    slug = entity_slug_from_id(entity_id)
    prefix = entity_id.split(":", 1)[0] if ":" in entity_id else ""
    if cutover:
        assert_boot_read_cutover_allowed(entity_id)
        return f"agent_skill:{slug}"
    if prefix == "agent_skill":
        assert_boot_read_cutover_allowed(entity_id)
    return entity_id if ":" in entity_id else f"rule:{slug}"
