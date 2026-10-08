"""Parse continuity-house ``## Pools`` manifest blocks.

Single authority for pool rows on a house continuity card. Projections
(``mint-tab-launch``, CDP staging, cursor-sdk admit, CHECKPOINT residue)
import this module — do not re-parse the table elsewhere.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Literal

from implement_admission.closeout_helpers import cortex_files_root

logger = logging.getLogger(__name__)

CardStatus = Literal["found", "missing"]
ConductorGateBasis = Literal["open", "blocked", "card_missing", "no_pools"]

POOL_COLUMNS: tuple[str, ...] = (
    "pool",
    "executor",
    "status",
    "must_load",
    "must_read",
    "closeout",
    "forbidden",
)

_POOLS_MARKER = "<!-- pools v1"
_POOLS_HEADING = "## Pools"
_STATUS_OPEN = re.compile(r"^open$", re.IGNORECASE)
_STATUS_BLOCKED = re.compile(
    r"^blocked\s*·\s*.+\s*·\s*since\s+\d{4}-\d{2}-\d{2}$",
    re.IGNORECASE,
)
_STATUS_SERIAL = re.compile(r"^serial\s*·\s*\d+$", re.IGNORECASE)
_SKILL_SPLIT = re.compile(r"\s*·\s*")
_HOUSE_THREAD_RE = re.compile(
    r"(?:agent-bus:|arc:)(\d{4,})|house\s+agent-bus:\*{0,2}(\d{4,})\*{0,2}",
    re.IGNORECASE,
)
_POOLS_RESIDUE_RE = re.compile(r"Pools:\s*([0-9a-f]{8})\b")
_CLOSEOUT_THREAD_RE = re.compile(r"agent-bus:(\d+)")


class PoolsParseError(ValueError):
    """Invalid ``## Pools`` table cell or column header."""

    def __init__(self, message: str, *, cell: str | None = None) -> None:
        super().__init__(message)
        self.cell = cell


@dataclass(frozen=True, slots=True)
class PoolRow:
    """One executor-pool row from a house continuity card."""

    pool: str
    executor: str
    status: str
    must_load: tuple[str, ...]
    must_read: tuple[str, ...]
    closeout: str
    forbidden: str


@dataclass(frozen=True, slots=True)
class ContinuityCard:
    """Resolved house card, or an explicit miss.

    ``relpath``, ``uri``, ``text``, and ``sha256`` are set only when
    ``status`` is ``found``. ``tried`` is the candidate relpaths examined,
    in ladder order, on both outcomes.
    """

    status: CardStatus
    tried: tuple[str, ...]
    relpath: str | None = None
    uri: str | None = None
    text: str | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.status == "found":
            if any(
                value is None
                for value in (self.relpath, self.uri, self.text, self.sha256)
            ):
                raise ValueError("found card requires relpath, uri, text, and sha256")
            return
        if self.status != "missing":
            raise ValueError(f"unknown card status: {self.status}")
        if any(
            value is not None
            for value in (self.relpath, self.uri, self.text, self.sha256)
        ):
            raise ValueError(
                "missing card must not carry relpath, uri, text, or sha256"
            )


@dataclass(frozen=True, slots=True)
class ConductorPoolGate:
    """Conductor-pool admission class for one house.

    ``refusal`` is the pool status cell only when ``basis`` is ``blocked``.
    ``card_missing`` and ``no_pools`` admit. A conductor row whose status is
    not the blocked vocabulary, including ``serial``, admits with basis
    ``open`` and the cell text in ``pool_status`` — the basis set has no
    separate serial value, and refusal is blocked-only.
    """

    basis: ConductorGateBasis
    pool_status: str | None = None

    @property
    def refusal(self) -> str | None:
        if self.basis != "blocked":
            return None
        return self.pool_status


def _normalized_house_id(house_id: str) -> str:
    return house_id.strip().removeprefix("agent-bus:")


def _continuity_card_candidate_relpaths(house_id: str) -> tuple[str, ...]:
    """Ladder: live card, continuity-card, legacy archive. Same directory."""
    normalized = _normalized_house_id(house_id)
    return tuple(
        f"notes/system/threads/{normalized}{suffix}"
        for suffix in ("-card.md", "-continuity-card.md", "-continuity.md")
    )


def load_continuity_card(house_id: str) -> ContinuityCard:
    """Resolve one house card. Sole builder of a continuity-card path.

    Candidates, in order: ``{id}-card.md``, ``{id}-continuity-card.md``,
    ``{id}-continuity.md``. A missing house returns status ``missing`` and
    does not invent a URI.
    """
    root = cortex_files_root()
    tried: list[str] = []
    for rel in _continuity_card_candidate_relpaths(house_id):
        tried.append(rel)
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        return ContinuityCard(
            status="found",
            tried=tuple(tried),
            relpath=rel,
            uri=f"cortex://{rel}",
            text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
    return ContinuityCard(status="missing", tried=tuple(tried))


def extract_pools_block(card_text: str) -> str | None:
    """Return the canonical pools block from marker through last table row."""
    marker_idx = card_text.find(_POOLS_MARKER)
    if marker_idx < 0:
        return None
    section = card_text[marker_idx:]
    heading_idx = section.find(_POOLS_HEADING)
    if heading_idx < 0:
        return None
    lines = section[heading_idx:].splitlines()
    block_lines: list[str] = [lines[0]]
    in_table = False
    for line in lines[1:]:
        stripped = line.strip()
        if in_table:
            if stripped.startswith("|"):
                block_lines.append(line)
                continue
            break
        if stripped.startswith("|"):
            in_table = True
            block_lines.append(line)
    if len(block_lines) < 3:
        return None
    return "\n".join(block_lines).rstrip() + "\n"


def pools_block_sha256(card_text: str) -> str | None:
    """Full sha256 hex digest of the extracted pools block."""
    block = extract_pools_block(card_text)
    if block is None:
        return None
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def pools_residue_token(card_text: str) -> str | None:
    """Return ``Pools: <sha8>`` for CHECKPOINT authored residue."""
    digest = pools_block_sha256(card_text)
    if digest is None:
        return None
    return f"Pools: {digest[:8]}"


def extract_pools_residue_sha8(residue: str) -> str | None:
    """Parse an authored ``Pools: <sha8>`` line from checkpoint residue."""
    match = _POOLS_RESIDUE_RE.search(residue)
    if match is None:
        return None
    return match.group(1).lower()


def pools_residue_matches_card(residue: str, card_text: str) -> bool:
    """True when residue ``Pools:`` token matches the card block digest."""
    expected = pools_block_sha256(card_text)
    observed = extract_pools_residue_sha8(residue)
    if expected is None or observed is None:
        return False
    return observed == expected[:8]


def _validate_status(status: str, *, cell: str) -> None:
    cleaned = status.strip()
    if (
        _STATUS_OPEN.match(cleaned)
        or _STATUS_BLOCKED.match(cleaned)
        or _STATUS_SERIAL.match(cleaned)
    ):
        return
    raise PoolsParseError(
        f"invalid pool status vocabulary: {status!r}",
        cell=cell,
    )


def _split_list_field(raw: str) -> tuple[str, ...]:
    parts = [part.strip() for part in _SKILL_SPLIT.split(raw) if part.strip()]
    return tuple(parts)


def _parse_table(block: str) -> dict[str, PoolRow]:
    lines = [
        line.strip() for line in block.splitlines() if line.strip().startswith("|")
    ]
    if len(lines) < 2:
        raise PoolsParseError("## Pools table missing header or rows")
    header_cells = [cell.strip().lower() for cell in lines[0].strip("|").split("|")]
    if header_cells != list(POOL_COLUMNS):
        unknown = [cell for cell in header_cells if cell not in POOL_COLUMNS]
        extra = [col for col in POOL_COLUMNS if col not in header_cells]
        detail = unknown or extra or header_cells
        raise PoolsParseError(
            f"unknown or misordered Pools columns: {detail}",
            cell="header",
        )
    rows: dict[str, PoolRow] = {}
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(POOL_COLUMNS):
            raise PoolsParseError(
                f"row has {len(cells)} cells, expected {len(POOL_COLUMNS)}",
                cell=f"row:{cells[0] if cells else '?'}",
            )
        data = dict(zip(POOL_COLUMNS, cells, strict=True))
        pool_name = data["pool"]
        _validate_status(data["status"], cell=f"status@{pool_name}")
        rows[pool_name] = PoolRow(
            pool=pool_name,
            executor=data["executor"],
            status=data["status"],
            must_load=_split_list_field(data["must_load"]),
            must_read=_split_list_field(data["must_read"]),
            closeout=data["closeout"],
            forbidden=data["forbidden"],
        )
    if not rows:
        raise PoolsParseError("## Pools table has no data rows")
    return rows


def parse_pools(card_text: str) -> dict[str, PoolRow]:
    """Parse ``## Pools`` into a pool-name → row map."""
    block = extract_pools_block(card_text)
    if block is None:
        raise PoolsParseError("continuity card has no ## Pools block")
    return _parse_table(block)


def pool_status_is_open(status: str) -> bool:
    """True only for ``open`` — serial and blocked are not open."""
    return bool(_STATUS_OPEN.match(status.strip()))


def pool_status_is_blocked(status: str) -> bool:
    """True when status begins with the blocked vocabulary."""
    return status.strip().lower().startswith("blocked")


def resolve_house_thread_id(
    thread_id: str | None,
    *,
    context_text: str = "",
) -> str | None:
    """Resolve a house thread id that owns a ``## Pools`` block."""
    candidates: list[str] = []
    if thread_id and thread_id.strip().isdigit():
        candidates.append(thread_id.strip())
    for match in _HOUSE_THREAD_RE.finditer(context_text or ""):
        candidates.extend(value for value in match.groups() if value)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        card = load_continuity_card(candidate)
        if card.status == "found" and card.text and extract_pools_block(card.text):
            return candidate
    return None


def parse_closeout_thread_id(closeout: str) -> str | None:
    """Extract the first ``agent-bus:{id}`` target from a closeout cell."""
    match = _CLOSEOUT_THREAD_RE.search(closeout)
    return match.group(1) if match else None


def conductor_pool_gate(house_id: str) -> ConductorPoolGate:
    """Classify conductor admission. Refuse only when the pool is blocked.

    A missing card is logged and admitted (basis ``card_missing``). A card
    with no conductor row admits as ``no_pools``.
    """
    card = load_continuity_card(house_id)
    if card.status == "missing":
        logger.info(
            "card_missing house_id=%s tried=%s",
            _normalized_house_id(house_id),
            list(card.tried),
        )
        return ConductorPoolGate(basis="card_missing")
    if card.text is None:
        return ConductorPoolGate(basis="no_pools")
    try:
        row = parse_pools(card.text)["conductor"]
    except (PoolsParseError, KeyError):
        return ConductorPoolGate(basis="no_pools")
    if pool_status_is_blocked(row.status):
        return ConductorPoolGate(basis="blocked", pool_status=row.status)
    return ConductorPoolGate(basis="open", pool_status=row.status)


def format_house_read_first_block(
    *,
    house_id: str,
    row: PoolRow,
    card: ContinuityCard | None = None,
) -> str:
    """Build the CDP ``## House (read first)`` staging block for one pool row.

    The card line is the resolved URI when the card was found. A missing
    card omits that line. ``card`` skips a second resolve when the caller
    already holds one.
    """
    resolved = card if card is not None else load_continuity_card(house_id)
    pointers: list[str] = []
    if resolved.status == "found" and resolved.uri:
        pointers.append(resolved.uri)
    for item in row.must_read:
        cleaned = item.strip()
        if cleaned.startswith("this card"):
            continue
        if cleaned.startswith("tip CP"):
            pointers.append(
                f"tip CHECKPOINT on agent-bus:{house_id.strip().removeprefix('agent-bus:')}"
            )
            continue
        if ".md" in cleaned or cleaned.startswith("cortex://"):
            pointers.append(cleaned)
            continue
        if cleaned.endswith(".md"):
            pointers.append(
                f"cortex://notes/system/threads/{house_id.strip().removeprefix('agent-bus:')}-{cleaned}"
            )
            continue
        pointers.append(cleaned)
    lines = ["## House (read first)", ""] + [f"- {pointer}" for pointer in pointers]
    lines.extend(
        [
            "",
            "Reply: send(thread=<lane>, from_agent=web-anthropic, "
            "sidecar_content=…) — the poll_hint waits on proof_reply_from web-anthropic.",
        ]
    )
    return "\n".join(lines)


def merge_house_pool_skills(
    skills: list[str] | None,
    *,
    extra: tuple[str, ...],
) -> list[str]:
    """Prepend pool ``must_load`` slugs idempotently (bare slug match)."""
    caller = [str(item).strip() for item in (skills or []) if str(item).strip()]
    have = {item.lstrip("/").split("(", 1)[0].strip().lower() for item in caller}
    missing: list[str] = []
    for slug in extra:
        bare = slug.split("(", 1)[0].strip().lstrip("/").lower()
        if bare and bare not in have:
            missing.append(slug.split("(", 1)[0].strip())
            have.add(bare)
    return missing + caller


def apply_fable_house_staging(
    body: str,
    *,
    house_id: str,
    skills: list[str] | None,
) -> tuple[str, list[str]]:
    """Prepend House block and merge fable ``must_load`` when the card resolves."""
    card = load_continuity_card(house_id)
    if card.status != "found" or not card.text:
        return body, list(skills or [])
    rows = parse_pools(card.text)
    row = rows.get("fable")
    if row is None:
        return body, list(skills or [])
    if pool_status_is_blocked(row.status):
        raise PoolsParseError(
            f"fable pool blocked: {row.status}",
            cell="status@fable",
        )
    merged_skills = merge_house_pool_skills(skills, extra=row.must_load)
    block = format_house_read_first_block(house_id=house_id, row=row, card=card)
    if body.lstrip().startswith("## House (read first)"):
        return body, merged_skills
    return f"{block}\n\n{body.lstrip()}", merged_skills


def inject_pools_checkpoint_projection(projected_body: str, house_id: str) -> str:
    """Add Pools block anchor to CHECKPOINT derived zone (AC-P-5 resolver)."""
    card = load_continuity_card(house_id)
    if card.status != "found" or not card.text:
        return projected_body
    digest = pools_block_sha256(card.text)
    if not digest:
        return projected_body
    anchor = f"- Pools block · sha256:{digest}"
    if anchor in projected_body:
        return projected_body
    marker = "### Artifact anchors"
    idx = projected_body.find(marker)
    if idx < 0:
        return projected_body
    line_end = projected_body.find("\n", idx)
    if line_end < 0:
        return projected_body
    return (
        projected_body[: line_end + 1] + anchor + "\n" + projected_body[line_end + 1 :]
    )


__all__ = [
    "POOL_COLUMNS",
    "PoolRow",
    "PoolsParseError",
    "ConductorPoolGate",
    "ContinuityCard",
    "apply_fable_house_staging",
    "conductor_pool_gate",
    "extract_pools_block",
    "extract_pools_residue_sha8",
    "inject_pools_checkpoint_projection",
    "load_continuity_card",
    "merge_house_pool_skills",
    "parse_closeout_thread_id",
    "parse_pools",
    "pool_status_is_blocked",
    "pool_status_is_open",
    "pools_block_sha256",
    "pools_residue_matches_card",
    "pools_residue_token",
    "resolve_house_thread_id",
]
