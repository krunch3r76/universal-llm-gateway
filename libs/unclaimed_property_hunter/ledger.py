"""Human-reviewable run ledger — renders persisted JSON without laundering verdicts.

Regenerates wholesale from normalized sidecars after each persist. Cadence gap
rows mark Thursdays with no bulk_extract run so silence is louder than zero hits.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from unclaimed_property_hunter.result_surface import execution_block_reason, verdict_token

_LEDGER_REL = Path("notes/system/unclaimed-property/ledger.md")
_RUNS_REL = Path("notes/system/unclaimed-property/runs")
_MONITOR_TZ = ZoneInfo("America/Los_Angeles")
_CADENCE_KIND = "bulk_extract"


def _files_root() -> Path:
    return Path(os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files"))


@dataclass(frozen=True)
class GapRow:
    """A Thursday monitor slot with no recorded bulk_extract run."""

    thursday: date


def ledger_uri() -> str:
    """Cortex URI for the generated markdown ledger."""
    return "cortex://" + _LEDGER_REL.as_posix()


def _parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)


def _la_date(ts: str) -> date:
    return _parse_utc(ts).astimezone(_MONITOR_TZ).date()


def _thursday_on_or_before(day: date) -> date:
    return day - timedelta(days=(day.weekday() - 3) % 7)


def _thursday_on_or_after(day: date) -> date:
    return day + timedelta(days=(3 - day.weekday()) % 7)


def _iter_thursdays(start: date, end: date) -> list[date]:
    cur = _thursday_on_or_after(start)
    out: list[date] = []
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=7)
    return out


def normalize_sidecar(data: dict) -> dict:
    """Derive honesty fields from a sidecar without inventing search outcomes."""
    search_executed = bool(data.get("search_executed", False))
    raw_hits = data.get("hits") or []
    raw_count = data.get("hit_count")
    if not search_executed:
        hit_count: int | None = None
    elif raw_count is None:
        hit_count = len(raw_hits)
    else:
        hit_count = int(raw_count)
    rows_scanned = None
    fp = data.get("corpus_fingerprint") or {}
    if fp:
        rows_scanned = fp.get("rows_scanned")
    reason = data.get("execution_block_reason")
    if reason is None and not search_executed:
        reason = execution_block_reason(
            data.get("run_kind", "bulk_extract"),
            search_executed,
            corpus_rows_scanned=rows_scanned,
        )
    verdict = data.get("verdict")
    if not verdict:
        verdict = verdict_token(search_executed=search_executed, hit_count=hit_count)
    return {
        **data,
        "search_executed": search_executed,
        "hit_count": hit_count,
        "verdict": verdict,
        "execution_block_reason": reason,
        "check_failed": bool(data.get("check_failed", False)),
        "check_failure_reason": data.get("check_failure_reason") or None,
    }


def load_all_run_dicts() -> list[dict]:
    """All normalized sidecars, oldest first."""
    folder = _files_root() / _RUNS_REL
    if not folder.is_dir():
        return []
    runs: list[dict] = []
    for path in sorted(folder.glob("*.normalized.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        runs.append(normalize_sidecar(data))
    return runs


def cadence_gaps(runs: list[dict], *, today: date | None = None) -> list[GapRow]:
    """Thursdays in the monitor window that lack any bulk_extract sidecar."""
    bulk = [r for r in runs if r.get("run_kind") == _CADENCE_KIND]
    if not bulk:
        return []
    la_today = today or datetime.now(_MONITOR_TZ).date()
    last_expected = _thursday_on_or_before(la_today)
    covered = {_la_date(r["utc_timestamp"]) for r in bulk}
    first_la = min(_la_date(r["utc_timestamp"]) for r in bulk)
    start = _thursday_on_or_before(first_la)
    gaps: list[GapRow] = []
    for thursday in _iter_thursdays(start, last_expected):
        if thursday not in covered:
            gaps.append(GapRow(thursday=thursday))
    return gaps


def _record_link(data: dict) -> str:
    run_id = data["run_id"]
    rel = f"cortex://notes/system/unclaimed-property/runs/{run_id}.normalized.json"
    return f"[{run_id}]({rel})"


def _detail_cell(data: dict) -> str:
    parts: list[str] = []
    if data.get("check_failed"):
        reason = data.get("check_failure_reason") or "unknown"
        parts.append(f"CHECK FAILED: {reason}")
    if not data.get("search_executed"):
        block = data.get("execution_block_reason") or "search_not_executed"
        parts.append(f"search did not execute — {block}")
    elif data.get("hit_count") == 0:
        parts.append("completed search — zero hits")
    else:
        parts.append(f"completed search — {data.get('hit_count')} hit(s)")
    return "; ".join(parts)


def render_run_row(data: dict) -> str:
    """One markdown table row for a persisted run."""
    verdict = str(data.get("verdict", ""))
    surname = str(data.get("query", {}).get("surname", ""))
    kind = str(data.get("run_kind", ""))
    ts = str(data.get("utc_timestamp", ""))
    return (
        f"| {ts} | {surname} | {kind} | **{verdict}** | {_detail_cell(data)} | "
        f"{_record_link(data)} |"
    )


def render_gap_row(gap: GapRow) -> str:
    """Cadence gap row — missing Thursday monitor run."""
    label = gap.thursday.isoformat()
    return (
        f"| {label} (expected) | — | **CADENCE GAP** | **NO THURSDAY RUN** | "
        f"Expected Thursday monitor ({label}) — no {_CADENCE_KIND} run recorded | — |"
    )


def _sort_key_run(data: dict) -> datetime:
    return _parse_utc(data["utc_timestamp"])


def _sort_key_gap(gap: GapRow) -> datetime:
    noon = datetime.combine(gap.thursday, datetime.min.time(), tzinfo=_MONITOR_TZ)
    return noon.astimezone(UTC)


def render_ledger_markdown(runs: list[dict], gaps: list[GapRow]) -> str:
    """Full markdown document, newest entries first."""
    bulk = [r for r in runs if r.get("run_kind") == _CADENCE_KIND]
    last_bulk = max(bulk, key=_sort_key_run)["utc_timestamp"] if bulk else "never"
    entries: list[tuple[datetime, str]] = []
    for run in runs:
        entries.append((_sort_key_run(run), render_run_row(run)))
    for gap in gaps:
        entries.append((_sort_key_gap(gap), render_gap_row(gap)))
    entries.sort(key=lambda item: item[0], reverse=True)
    body = "\n".join(row for _, row in entries) if entries else "| — | — | — | — | no runs yet | — |"
    generated = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return "\n".join(
        [
            "# CA Unclaimed Property Hunt — Run Ledger",
            "",
            f"Generated: {generated}",
            "",
            "Schedule: Thursday 06:00 America/Los_Angeles (`bulk_extract` via systemd timer).",
            f"Last `{_CADENCE_KIND}` run: {last_bulk}",
            "",
            "Verdict legend: `NOT EXECUTED` ≠ `EXECUTED ZERO` — only the latter is a completed search with no hits.",
            "",
            "| When (UTC) | Surname | Kind | Verdict | Detail | Record |",
            "| --- | --- | --- | --- | --- | --- |",
            body,
            "",
        ]
    )


def regenerate_ledger() -> str:
    """Rebuild ledger.md from all normalized sidecars; return cortex URI."""
    from unclaimed_property_hunter.record import _write_bytes

    runs = load_all_run_dicts()
    gaps = cadence_gaps(runs)
    markdown = render_ledger_markdown(runs, gaps)
    _write_bytes(_LEDGER_REL, markdown.encode("utf-8"))
    return ledger_uri()
