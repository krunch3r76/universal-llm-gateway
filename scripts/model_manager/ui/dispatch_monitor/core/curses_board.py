"""Curses View — full-screen dispatch board from ``SupervisorProjection`` frames.

Pure rendering: reads projection DTOs only, derives nothing. Formatting helpers
mirror :mod:`.watch` so the board shows the same vocabulary as ``--watch live``.
"""

from __future__ import annotations

import curses
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .dtos import AttentionItem, SupervisorProjection
from .watch import SEVERITY_MARK, _cdp_line, _ms, _root_line, _sdk_line, _truncate

_TZ = ZoneInfo("America/Los_Angeles")


def _la_clock() -> str:
    return datetime.now(_TZ).strftime("%H:%M:%S %Z")


def _hold_label(held: bool | None) -> str:
    if held is True:
        return "yes"
    if held is False:
        return "no"
    return "?"


def _section_bar(title: str, count: int, width: int) -> str:
    label = f" {title} ({count}) "
    pad = max(0, width - len(label) - 2)
    return f"─{label}{'─' * pad}"


def _attention_line(item: AttentionItem, width: int) -> str:
    mark = SEVERITY_MARK.get(item.severity, "  ")
    body = f"{mark} [{item.kind}] {item.subject}: {item.title}"
    if item.detail and len(body) < width - 8:
        body = f"{body} — {item.detail[: width - len(body) - 3]}"
    return _truncate(body, width)


def _sdk_active_first(rows: tuple) -> list:
    """In-flight rows first so a full history seed does not crowd the pane."""
    active = [row for row in rows if row.terminal_ms is None]
    done = [row for row in rows if row.terminal_ms is not None]
    return active + done


def _cdp_active_first(rows: tuple) -> list:
    active = [row for row in rows if row.terminal_ms is None]
    done = [row for row in rows if row.terminal_ms is not None]
    return active + done


class CursesBoard:
    """Paint one projection frame onto a curses stdscr."""

    def __init__(self, stdscr: Any) -> None:
        self._scr = stdscr
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(250)
        self._hold: bool | None = None
        self._status = "starting…"
        self._init_colors()

    def _init_colors(self) -> None:
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_CYAN, -1)
        curses.init_pair(4, curses.COLOR_GREEN, -1)

    def set_hold(self, held: bool | None) -> None:
        self._hold = held

    def set_status(self, message: str) -> None:
        self._status = message

    def _pair_for_severity(self, severity: str) -> int:
        if severity == "crit":
            return 1
        if severity == "warn":
            return 2
        return 0

    def paint(self, projection: SupervisorProjection | None) -> None:
        height, width = self._scr.getmaxyx()
        self._scr.erase()
        if projection is None:
            self._safe_addstr(
                0, 0, f"DISPATCH BOARD  {_la_clock()}  {self._status}", 3
            )
            self._safe_addstr(2, 0, "Waiting for first projection frame…", 0)
            self._scr.refresh()
            return

        health = projection.health
        seq = health.seq_high_water if health.seq_high_water is not None else "-"
        header = (
            f"DISPATCH BOARD  {_la_clock()}  hold={_hold_label(self._hold)}  "
            f"tick={_ms(health.tick_last_scan_age_ms)} ago  "
            f"loop={health.charter_loop_state}  seq={seq}  fp={projection.fingerprint[:8]}"
        )
        self._safe_addstr(0, 0, _truncate(header, width), 3)
        sub = (
            f"admitted {health.tick_admitted_last_scan}/{health.tick_admitted_total}  "
            f"roots scanned={health.tick_roots_scanned}  folded={health.records_folded}  "
            f"fold={health.fold_status}  {self._status}"
        )
        self._safe_addstr(1, 0, _truncate(sub, width), 0)

        y = 2
        budgets = self._section_budgets(height, projection)
        y = self._paint_tick_section(projection, y, width, height, budgets["roots"])
        y = self._paint_lease_section(projection, y, width, height)
        y = self._paint_sdk_section(projection, y, width, height, budgets["sdk"])
        y = self._paint_cdp_section(projection, y, width, height, budgets["cdp"])
        self._paint_attention_section(projection, y, width, height, budgets["attention"])
        self._scr.refresh()

    def _section_budgets(self, height: int, projection: SupervisorProjection) -> dict[str, int]:
        """Reserve rows so ATTENTION and CDP stay visible on short panes."""
        attention_n = len(projection.attention)
        cdp_n = len(projection.cdp)
        sdk_n = len(_sdk_active_first(projection.sdk))
        roots_n = len(projection.roots)
        usable = max(8, height - 2)
        att = min(attention_n, max(2, usable // 6)) if attention_n else 1
        cdp = min(cdp_n, max(2, usable // 8)) if cdp_n else 1
        sdk = min(sdk_n, max(3, usable // 5)) if sdk_n else 1
        roots = min(roots_n, max(2, usable // 8)) if roots_n else 1
        lease = 4
        overhead = 2 + att + cdp + sdk + roots + lease + 5  # section bars + lease body
        if overhead > usable:
            sdk = max(1, sdk - (overhead - usable))
        return {"attention": att, "cdp": cdp, "sdk": sdk, "roots": roots}

    def _paint_tick_section(
        self,
        projection: SupervisorProjection,
        y: int,
        width: int,
        height: int,
        row_cap: int,
    ) -> int:
        if y >= height - 1:
            return y
        roots = projection.roots
        self._safe_addstr(y, 0, _section_bar("TICK / ROOTS", len(roots), width), 4)
        y += 1
        shown = 0
        for row in roots:
            if y >= height - 1 or shown >= row_cap:
                break
            self._safe_addstr(y, 0, _root_line(row)[: width - 1], 0)
            y += 1
            shown += 1
        if shown < len(roots) and y < height - 1:
            self._safe_addstr(y, 0, f"  … +{len(roots) - shown} more", 0)
            y += 1
        if not roots and y < height - 1:
            self._safe_addstr(y, 0, "  (no enrolled roots)", 0)
            y += 1
        return y

    def _paint_lease_section(
        self, projection: SupervisorProjection, y: int, width: int, height: int
    ) -> int:
        if y >= height - 1:
            return y
        health = projection.health
        self._safe_addstr(y, 0, _section_bar("LEASE / QUEUE", health.queue_depth, width), 4)
        y += 1
        cap = health.wip_capacity if health.wip_capacity is not None else "?"
        lease = (
            f"  holder={health.lease_holder or '-'}  queue={health.queue_depth}  "
            f"wip={health.wip_in_use}/{cap}  "
            f"dropped={health.events_dropped_ingest}/{health.events_dropped_subscribe}"
        )
        if y < height - 1:
            self._safe_addstr(y, 0, lease[: width - 1], 0)
            y += 1
        if health.skipped_by_reason and y < height - 1:
            hist = "  ".join(
                f"{reason}={count}" for reason, count in health.skipped_by_reason.items()
            )
            self._safe_addstr(y, 0, f"  skips: {hist}"[: width - 1], 2)
            y += 1
        if health.degraded and y < height - 1:
            self._safe_addstr(
                y, 0, f"  DEGRADED: {', '.join(health.degraded)}"[: width - 1], 1
            )
            y += 1
        return y

    def _paint_sdk_section(
        self,
        projection: SupervisorProjection,
        y: int,
        width: int,
        height: int,
        row_cap: int,
    ) -> int:
        if y >= height - 1:
            return y
        rows = _sdk_active_first(projection.sdk)
        total = len(projection.sdk)
        self._safe_addstr(y, 0, _section_bar("SDK", total, width), 4)
        y += 1
        if not rows and y < height - 1:
            self._safe_addstr(y, 0, "  (no sdk dispatches in flight)", 0)
            return y + 2
        shown = 0
        for row in rows:
            if y >= height - 1 or shown >= row_cap:
                break
            pair = 2 if row.divergent_fields else 0
            self._safe_addstr(y, 0, _sdk_line(row)[: width - 1], pair)
            y += 1
            shown += 1
        if shown < len(rows) and y < height - 1:
            self._safe_addstr(y, 0, f"  … +{len(rows) - shown} more", 0)
            y += 1
        return y

    def _paint_cdp_section(
        self,
        projection: SupervisorProjection,
        y: int,
        width: int,
        height: int,
        row_cap: int,
    ) -> int:
        if y >= height - 1:
            return y
        rows = _cdp_active_first(projection.cdp)
        total = len(projection.cdp)
        self._safe_addstr(y, 0, _section_bar("CDP", total, width), 4)
        y += 1
        if not rows and y < height - 1:
            self._safe_addstr(y, 0, "  (no cdp legs in flight)", 0)
            return y + 2
        shown = 0
        for row in rows:
            if y >= height - 1 or shown >= row_cap:
                break
            self._safe_addstr(y, 0, _cdp_line(row)[: width - 1], 0)
            y += 1
            shown += 1
        if shown < len(rows) and y < height - 1:
            self._safe_addstr(y, 0, f"  … +{len(rows) - shown} more", 0)
            y += 1
        return y

    def _paint_attention_section(
        self,
        projection: SupervisorProjection,
        y: int,
        width: int,
        height: int,
        row_cap: int,
    ) -> None:
        if y >= height - 1:
            return
        items = projection.attention
        self._safe_addstr(y, 0, _section_bar("ATTENTION", len(items), width), 4)
        y += 1
        if not items and y < height - 1:
            self._safe_addstr(y, 0, "  (none)", 0)
            return
        shown = 0
        for item in items:
            if y >= height - 1 or shown >= row_cap:
                break
            self._safe_addstr(
                y, 0, _attention_line(item, width - 1), self._pair_for_severity(item.severity)
            )
            y += 1
            shown += 1
        if shown < len(items) and y < height - 1:
            self._safe_addstr(y, 0, f"  … +{len(items) - shown} more", 0)

    def _safe_addstr(self, y: int, x: int, text: str, pair: int) -> None:
        if y < 0 or x < 0:
            return
        height, width = self._scr.getmaxyx()
        if y >= height or x >= width:
            return
        clip = text[: max(0, width - x - 1)]
        try:
            if pair:
                self._scr.addstr(y, x, clip, curses.color_pair(pair))
            else:
                self._scr.addstr(y, x, clip)
        except curses.error:
            pass
