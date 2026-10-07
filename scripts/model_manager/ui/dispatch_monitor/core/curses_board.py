"""Curses View — posture dashboard from ``SupervisorProjection`` (Rival A bind).

Governing invariant: a row is painted iff it is an open obligation.
``terminal_ms is not None`` ⇒ never a row in SDK/CDP. Terminals survive as
ribbon counters, ATTENTION judgments, or ``watch-dispatch-tail`` history.

Bind: ``cortex://notes/system/threads/dispatch-board-redesign-opus/bind.md``.
"""

from __future__ import annotations

import curses
from typing import Any

from .board_lines import (
    active_roots,
    aside_roots,
    cdp_ribbon,
    count_by_root,
    hold_label,
    la_clock,
    live_cdp,
    live_sdk,
    oldest_elapsed_ms,
    primary_sdk_dispatch_for_root,
    primary_tick_objective,
    resolve_hold,
    root_line_live,
    sdk_live_line,
    sdk_live_posture,
    sdk_posture_legend,
    sdk_ribbon,
    sdk_terminal_tally,
    section_bar,
    tick_objective_line,
)
from .curses_sections import _section_bar_attr, paint_attention, paint_lease
from .dtos import CdpLegRow, CharterRootRow, SdkDispatchRow, SupervisorProjection
from .sdk_posture import row_role
from .watch import _cdp_line, _ms, _truncate, cdp_id_legend

HEADER_ROWS = 3
LOGICAL_HEIGHT = 100_000


def section_row_caps(
    *,
    sdk_n: int,
    cdp_n: int,
    roots_n: int,
    aside_n: int,
    attention_n: int,
) -> dict[str, int]:
    """Every row is paintable. The viewport scrolls; caps are not a ceiling."""
    return {
        "attention": attention_n if attention_n else 1,
        "cdp": cdp_n,
        "sdk": sdk_n,
        "roots": roots_n if roots_n else 1,
        "aside": aside_n,
    }


def clamp_scroll(scroll: int, content_end: int, screen_h: int) -> int:
    """Keep the viewport inside painted content.

    ``content_end`` is one past the last logical line. Header rows stay fixed.
    """
    visible = max(1, screen_h - HEADER_ROWS)
    content_lines = max(0, content_end - HEADER_ROWS)
    max_scroll = max(0, content_lines - visible)
    return min(max(0, scroll), max_scroll)


def scroll_to_show_logical_line(
    scroll: int,
    logical_y: int,
    *,
    content_end: int,
    screen_h: int,
) -> int:
    """Adjust scroll so a logical body line is visible on screen."""
    if logical_y < HEADER_ROWS:
        return scroll
    screen_row = logical_y - scroll
    if screen_row < HEADER_ROWS:
        return clamp_scroll(logical_y - HEADER_ROWS, content_end, screen_h)
    if screen_row >= screen_h:
        return clamp_scroll(logical_y - screen_h + 1, content_end, screen_h)
    return scroll


class CursesBoard:
    """Paint one projection frame onto a curses stdscr."""

    def __init__(self, stdscr: Any, *, seed_minutes: int = 60) -> None:
        self._scr = stdscr
        self._seed_minutes = seed_minutes
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(250)
        self._hold: bool | None = None
        self._status = "starting…"
        self._scroll = 0
        self._content_end = HEADER_ROWS
        self._screen_h = 24
        self._selection_logical_y: int | None = None
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

    def scroll_by(self, delta: int) -> None:
        """Move the body viewport. The three header rows stay put."""
        self._scroll = clamp_scroll(
            self._scroll + delta, self._content_end, self._screen_h
        )

    def adjust_scroll_for_selection(self) -> bool:
        """Scroll the viewport until the selected row is on screen."""
        y = self._selection_logical_y
        if y is None:
            return False
        new_scroll = scroll_to_show_logical_line(
            self._scroll,
            y,
            content_end=self._content_end,
            screen_h=self._screen_h,
        )
        if new_scroll == self._scroll:
            return False
        self._scroll = new_scroll
        return True

    def _pair_for_severity(self, severity: str) -> int:
        if severity == "crit":
            return 1
        if severity == "warn":
            return 2
        return 0

    def paint(
        self,
        projection: SupervisorProjection | None,
        *,
        selected_key: str | None = None,
        focused_section: str = "sdk",
        section_zoom: bool = False,
    ) -> None:
        screen_h, width = self._scr.getmaxyx()
        self._screen_h = screen_h
        self._content_end = HEADER_ROWS
        self._selection_logical_y = None
        height = LOGICAL_HEIGHT
        self._scr.erase()
        if projection is None:
            self._safe_addstr(0, 0, f"DISPATCH BOARD  {la_clock()}  {self._status}", 3)
            self._safe_addstr(2, 0, "Waiting for first projection frame…", 0)
            self._scr.refresh()
            return

        sdk_live = live_sdk(projection.sdk)
        cdp_live = live_cdp(projection.cdp)
        roots_active = active_roots(projection.roots)
        roots_aside = aside_roots(projection.roots)
        by_sdk = count_by_root(sdk_live)
        by_cdp = count_by_root(cdp_live)

        self._paint_header(projection, sdk_live, cdp_live, width)
        y = 3
        budgets = self._section_budgets(
            height, sdk_live, cdp_live, roots_active, roots_aside, projection
        )

        def _show(section_id: str) -> bool:
            return not section_zoom or focused_section == section_id

        if _show("tick"):
            y = self._paint_tick(
                roots_active,
                roots_aside,
                projection,
                by_sdk,
                by_cdp,
                y,
                width,
                height,
                budgets["roots"],
                budgets["aside"],
                focused_section=focused_section,
            )
        if _show("lease"):
            y = self._paint_lease(
                projection, y, width, height, focused_section=focused_section
            )
        if _show("sdk"):
            y = self._paint_sdk(
                projection,
                sdk_live,
                y,
                width,
                height,
                budgets["sdk"],
                selected_key=selected_key,
                focused_section=focused_section,
            )
        if _show("cdp"):
            y = self._paint_cdp(
                projection,
                cdp_live,
                y,
                width,
                height,
                budgets["cdp"],
                selected_key=selected_key,
                focused_section=focused_section,
            )
        if _show("attention"):
            paint_attention(
                self,
                projection,
                y,
                width,
                height,
                budgets["attention"],
                focused_section=focused_section,
            )
        self._scr.refresh()

    def _paint_header(
        self,
        projection: SupervisorProjection,
        sdk_live: list[SdkDispatchRow],
        cdp_live: list[CdpLegRow],
        width: int,
    ) -> None:
        health = projection.health
        seq = health.seq_high_water if health.seq_high_water is not None else "-"
        held = resolve_hold(from_events=health.charter_hold, from_poll=self._hold)
        hold_txt = hold_label(held)
        if held and health.charter_hold_reason:
            hold_txt = f"{hold_txt}({_truncate(health.charter_hold_reason, 28)})"
        header = (
            f"DISPATCH BOARD  {la_clock()}  hold={hold_txt}  "
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
        crit = sum(1 for item in projection.attention if item.severity == "crit")
        warn = sum(1 for item in projection.attention if item.severity == "warn")
        cap = health.wip_capacity if health.wip_capacity is not None else "?"
        flight = (
            f"flight: sdk {len(sdk_live)} (oldest el {_ms(oldest_elapsed_ms(sdk_live))}) · "
            f"cdp {len(cdp_live)} (oldest el {_ms(oldest_elapsed_ms(cdp_live))}) · "
            f"queue {health.queue_depth} wip {health.wip_in_use}/{cap} · "
            f"attn {crit}crit {warn}warn"
        )
        self._safe_addstr(2, 0, _truncate(flight, width), 3)

    def _section_budgets(
        self,
        height: int,
        sdk_live: list[SdkDispatchRow],
        cdp_live: list[CdpLegRow],
        roots_active: list[CharterRootRow],
        roots_aside: list[CharterRootRow],
        projection: SupervisorProjection,
    ) -> dict[str, int]:
        del height
        return section_row_caps(
            sdk_n=len(sdk_live),
            cdp_n=len(cdp_live),
            roots_n=len(roots_active),
            aside_n=len(roots_aside),
            attention_n=len(projection.attention),
        )

    def _paint_tick(
        self,
        roots_active: list[CharterRootRow],
        roots_aside: list[CharterRootRow],
        projection: SupervisorProjection,
        by_root_sdk: dict[str, int],
        by_root_cdp: dict[str, int],
        y: int,
        width: int,
        height: int,
        active_cap: int,
        aside_cap: int,
        *,
        focused_section: str | None = None,
    ) -> int:
        if y >= height - 1:
            return y
        closed = sum(1 for row in projection.roots if row.closed or row.unenrolled)
        bar = f" TICK / ACTIVE ({len(roots_active)} enqueued · +{closed} closed) "
        self._safe_addstr(
            y,
            0,
            f"─{bar}{'─' * max(0, width - len(bar) - 2)}",
            4,
            _section_bar_attr(focused_section, "tick"),
        )
        y += 1
        primary = primary_tick_objective(roots_active)
        subtitle_root: str | None = None
        if primary and y < height - 1:
            root_id, kind, text = primary
            subtitle_root = root_id
            self._safe_addstr(
                y,
                0,
                tick_objective_line(root_id, text, width - 1, kind=kind),
                3,
            )
            y += 1
        y = self._paint_root_rows(
            roots_active,
            by_root_sdk,
            by_root_cdp,
            y,
            width,
            height,
            active_cap,
            empty="  idle — no root enqueued",
            sdk_rows=projection.sdk,
            identity_on_subtitle_root=subtitle_root,
        )
        if roots_aside and y < height - 1:
            aside_bar = f" SET ASIDE ({len(roots_aside)}) "
            self._safe_addstr(
                y, 0, f"─{aside_bar}{'─' * max(0, width - len(aside_bar) - 2)}", 2
            )
            y += 1
            y = self._paint_root_rows(
                roots_aside,
                by_root_sdk,
                by_root_cdp,
                y,
                width,
                height,
                aside_cap,
                empty=None,
                color=2,
                sdk_rows=projection.sdk,
            )
        return y

    def _paint_root_rows(
        self,
        rows: list[CharterRootRow],
        by_root_sdk: dict[str, int],
        by_root_cdp: dict[str, int],
        y: int,
        width: int,
        height: int,
        row_cap: int,
        *,
        empty: str | None,
        color: int = 0,
        sdk_rows: tuple[SdkDispatchRow, ...] = (),
        identity_on_subtitle_root: str | None = None,
    ) -> int:
        shown = 0
        for row in rows:
            if y >= height - 1 or shown >= row_cap:
                break
            rid = row.root_id
            line = root_line_live(
                row,
                sdk_n=by_root_sdk.get(rid, 0),
                cdp_n=by_root_cdp.get(rid, 0),
                width=width,
                sdk_dispatch_id=primary_sdk_dispatch_for_root(rid, sdk_rows),
                omit_identity_tail=identity_on_subtitle_root == rid,
            )
            pair = 1 if row.state == "failed" else color
            self._safe_addstr(y, 0, line[: width - 1], pair)
            y += 1
            shown += 1
        if shown < len(rows) and y < height - 1:
            self._safe_addstr(y, 0, f"  … +{len(rows) - shown} more", 0)
            y += 1
        if not rows and empty and y < height - 1:
            self._safe_addstr(y, 0, empty, 0)
            y += 1
        return y

    def _paint_lease(
        self,
        projection: SupervisorProjection,
        y: int,
        width: int,
        height: int,
        *,
        focused_section: str | None = None,
    ) -> int:
        return paint_lease(
            self, projection, y, width, height, focused_section=focused_section
        )

    def _paint_sdk(
        self,
        projection: SupervisorProjection,
        live: list[SdkDispatchRow],
        y: int,
        width: int,
        height: int,
        row_cap: int,
        selected_key: str | None = None,
        *,
        focused_section: str | None = None,
    ) -> int:
        if y >= height - 1:
            return y
        window_done = sum(1 for row in projection.sdk if row.terminal_ms is not None)
        posture = sdk_live_posture(live)
        title = "SDK" if posture == "solo" else f"SDK · {posture}"
        self._safe_addstr(
            y,
            0,
            section_bar(
                title,
                len(live),
                window_done,
                width,
                unit=f"done/{self._seed_minutes}m",
            ),
            4,
            _section_bar_attr(focused_section, "sdk"),
        )
        y += 1
        legend = sdk_posture_legend(posture)
        if legend and y < height - 1:
            self._safe_addstr(y, 0, legend[: width - 1], 3)
            y += 1
        if not live and y < height - 1:
            self._safe_addstr(y, 0, "  idle — no sdk dispatch in flight", 0)
            y += 1
        shown = 0
        for row in live:
            if y >= height - 1 or shown >= row_cap:
                break
            role = row_role(row, live, posture)
            if row.divergent_fields:
                pair = 2
            elif role == "ghost":
                pair = 3
            else:
                pair = 0
            line = sdk_live_line(
                row,
                live=live,
                posture=posture,
                width=width - 1,
                relations=projection.relations,
            )
            attr = curses.A_REVERSE if row.dispatch_id == selected_key else 0
            if row.dispatch_id == selected_key:
                self._selection_logical_y = y
            self._safe_addstr(y, 0, line[: width - 1], pair, attr)
            y += 1
            shown += 1
        if shown < len(live) and y < height - 1:
            self._safe_addstr(y, 0, f"  … +{len(live) - shown} more", 0)
            y += 1
        if y < height - 1:
            ribbon = sdk_ribbon(
                projection.sdk,
                now_ms=projection.generated_at_ms,
                window_m=self._seed_minutes,
            )
            _, fail_n, _, _ = sdk_terminal_tally(projection.sdk)
            self._safe_addstr(y, 0, ribbon[: width - 1], 1 if fail_n > 0 else 0)
            y += 1
        return y

    def _paint_cdp(
        self,
        projection: SupervisorProjection,
        live: list[CdpLegRow],
        y: int,
        width: int,
        height: int,
        row_cap: int,
        selected_key: str | None = None,
        *,
        focused_section: str | None = None,
    ) -> int:
        if y >= height - 1:
            return y
        window_done = sum(1 for row in projection.cdp if row.terminal_ms is not None)
        self._safe_addstr(
            y,
            0,
            section_bar(
                "CDP", len(live), window_done, width, unit=f"done/{self._seed_minutes}m"
            ),
            4,
            _section_bar_attr(focused_section, "cdp"),
        )
        y += 1
        if not live and y < height - 1:
            self._safe_addstr(y, 0, "  idle — no cdp legs in flight", 0)
            y += 1
        if live and y < height - 1:
            self._safe_addstr(y, 0, cdp_id_legend()[: width - 1], 3)
            y += 1
        shown = 0
        for row in live:
            if y >= height - 1 or shown >= row_cap:
                break
            cdp_key = row.chat_url or row.registration_id
            attr = curses.A_REVERSE if cdp_key and cdp_key == selected_key else 0
            if cdp_key and cdp_key == selected_key:
                self._selection_logical_y = y
            self._safe_addstr(y, 0, _cdp_line(row, width=width - 1), 0, attr)
            y += 1
            shown += 1
        if shown < len(live) and y < height - 1:
            self._safe_addstr(y, 0, f"  … +{len(live) - shown} more", 0)
            y += 1
        if y < height - 1:
            ribbon = cdp_ribbon(
                projection.cdp,
                now_ms=projection.generated_at_ms,
                window_m=self._seed_minutes,
            )
            self._safe_addstr(y, 0, ribbon[: width - 1], 0)
            y += 1
        return y

    def _safe_addstr(self, y: int, x: int, text: str, pair: int, attr: int = 0) -> None:
        if y < 0 or x < 0:
            return
        if y >= HEADER_ROWS:
            self._content_end = max(self._content_end, y + 1)
            y -= self._scroll
            if y < HEADER_ROWS:
                return
        height, width = self._scr.getmaxyx()
        if y >= height or x >= width:
            return
        clip = text[: max(0, width - x - 1)]
        try:
            style = (curses.color_pair(pair) if pair else 0) | attr
            if style:
                self._scr.addstr(y, x, clip, style)
            else:
                self._scr.addstr(y, x, clip)
        except curses.error:
            pass
