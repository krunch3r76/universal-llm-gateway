"""Admission gate, commit/release lifecycle, latch, and status policy (leg A3)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from event_store.model_rate_table import resolve_pinned_rate

from .config import CreditConfig, CreditConfigInvalid, load_credit_config
from .cycle_window import meter_window
from .ledger import CreditLedger, LedgerUnavailable, parse_utc_iso, validate_ledger_path
from .reservation import Reservation, ReservationRefused, worst_case_usd
from .usage_cost import usage_cost_usd_for_config

EventEmitter = Callable[[str, dict[str, Any]], None]


def _noop_emit(_name: str, _payload: dict[str, Any]) -> None:
    """Default no-op event emitter when no service EventBus hook is registered."""


_module_emit: EventEmitter = _noop_emit


def emit(name: str, payload: dict[str, Any]) -> None:
    """Invoke the configured module-level event emitter hook."""
    _module_emit(name, payload)


def set_event_emitter(fn: EventEmitter | None) -> None:
    """Set or reset the module-level event emitter hook."""
    global _module_emit
    _module_emit = fn if fn is not None else _noop_emit


@dataclass(frozen=True, slots=True)
class Admit:
    """Admission grant returned by ``policy.admit``."""

    request_id: str
    model: str
    reservation_usd: Decimal
    headroom_usd: Decimal
    window_start: datetime
    reset_at: datetime
    mcp_class: bool
    reservation: Reservation
    admitted: Literal[True] = True


@dataclass(frozen=True, slots=True)
class Refuse:
    """Admission refusal returned by ``policy.admit``."""

    code: str
    http_status: int
    reservation_usd: Decimal | None
    headroom_usd: Decimal | None
    detail: str
    admitted: Literal[False] = False


class AnthropicCreditRefused(Exception):  # noqa: N818
    """Exception wrapper for ``Refuse`` decisions at adapter boundaries."""

    def __init__(
        self,
        refuse_or_code: Refuse | str,
        detail: str = "",
        *,
        http_status: int = 429,
        reservation_usd: Decimal | None = None,
        headroom_usd: Decimal | None = None,
    ) -> None:
        if isinstance(refuse_or_code, Refuse):
            self.refuse = refuse_or_code
        else:
            self.refuse = Refuse(
                code=refuse_or_code,
                http_status=http_status,
                reservation_usd=reservation_usd,
                headroom_usd=headroom_usd,
                detail=detail,
            )
        super().__init__(f"{self.refuse.code}: {self.refuse.detail}")
        self.code = self.refuse.code
        self.http_status = self.refuse.http_status
        self.reservation_usd = self.refuse.reservation_usd
        self.headroom_usd = self.refuse.headroom_usd
        self.detail = self.refuse.detail


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=UTC)
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.tzinfo.utcoffset(now) is None
    ):
        raise ValueError("now must be a timezone-aware datetime")
    return now.astimezone(UTC)


def _normalize_model_id(raw_model: str) -> str:
    text = str(raw_model or "").strip()
    if not text:
        return ""
    if "/" in text:
        return text
    return f"anthropic/{text}"


class CreditPolicy:
    """Anthropic cycle dollar admission gate and reservation ledger policy."""

    def __init__(
        self,
        cfg: CreditConfig | None = None,
        *,
        config_path: Path | None = None,
        ledger_path: Path | None = None,
        rates_path: Path | None = None,
        busy_timeout_ms: int = 1000,
        emit_fn: EventEmitter | None = None,
        ledger: CreditLedger | None = None,
    ) -> None:
        self._cfg = cfg
        self._config_path = config_path
        self._ledger_path_override = ledger_path
        self._rates_path = rates_path
        self._busy_timeout_ms = busy_timeout_ms
        self._emit_fn = emit_fn
        self._ledger: CreditLedger | None = ledger
        self._overrun_multiplier_by_window: dict[str, int] = {}
        if self._cfg is not None and self._ledger is None:
            try:
                self._resolve_config_and_ledger()
            except Exception:
                self._ledger = None

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        target = self._emit_fn if self._emit_fn is not None else emit
        target(name, payload)

    def _resolve_config_and_ledger(self) -> tuple[CreditConfig, CreditLedger]:
        cfg = self._cfg
        if cfg is None:
            cfg = load_credit_config(self._config_path)
            self._cfg = cfg
        if cfg.fail_mode != "closed":
            raise CreditConfigInvalid('fail_mode must be "closed"')
        if not 1 <= cfg.cycle.anchor_day <= 28:
            raise CreditConfigInvalid("cycle.anchor_day must be in 1..28")

        target_path = (
            self._ledger_path_override
            if self._ledger_path_override is not None
            else cfg.ledger_path
        )
        validate_ledger_path(target_path)

        if self._ledger is None:
            self._ledger = CreditLedger(
                target_path,
                busy_timeout_ms=self._busy_timeout_ms,
                create_if_missing=False,
            )
        return cfg, self._ledger

    @staticmethod
    def _compute_caps(
        cfg: CreditConfig,
        *,
        committed_usd: Decimal,
        open_usd: Decimal,
        spend_since_as_of_usd: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return ``(cap_usd, hard_stop_headroom_usd, mcp_headroom_usd)``."""
        window_total = committed_usd + open_usd
        if cfg.available_balance.as_of is not None:
            balance_cap = window_total + (
                cfg.available_balance.usd - spend_since_as_of_usd
            )
        else:
            balance_cap = min(cfg.grant_usd, cfg.available_balance.usd)

        cap_usd = max(Decimal("0"), min(cfg.hard_stop_usd, balance_cap))
        mcp_cap_usd = max(Decimal("0"), min(cap_usd, cfg.mcp_connector_stop_usd))
        hard_headroom = max(Decimal("0"), cap_usd - window_total)
        mcp_headroom = max(Decimal("0"), mcp_cap_usd - window_total)
        return cap_usd, hard_headroom, mcp_headroom

    def _effective_mcp_allowance(
        self,
        cfg: CreditConfig,
        *,
        window_start_iso: str,
        db_overrun_count: int,
    ) -> Decimal:
        mem_count = self._overrun_multiplier_by_window.get(window_start_iso, 0)
        doublings = max(mem_count, db_overrun_count)
        return cfg.mcp_connector_allowance_usd * (Decimal(2) ** doublings)

    def admit(
        self,
        request_id: str,
        model: str,
        upstream_body: dict[str, Any],
        tag: str | None = None,
        cost_intent: str | None = None,
        token_count: int | None = None,
        *,
        now: datetime | None = None,
    ) -> Admit | Refuse:
        """Evaluate and atomically reserve budget for one upstream Anthropic call."""
        _ = cost_intent  # Override gate handles cost_intent; policy gates dollars.
        try:
            now_utc = _normalize_now(now)
            cfg, ledger = self._resolve_config_and_ledger()
            window_start, reset_at = meter_window(now_utc, cfg)
            win_start_iso = window_start.isoformat()
            as_of_dt = (
                parse_utc_iso(cfg.available_balance.as_of)
                if cfg.available_balance.as_of
                else None
            )

            if not request_id or not str(request_id).strip():
                return Refuse(
                    code="anthropic_reservation_refused",
                    http_status=422,
                    reservation_usd=None,
                    headroom_usd=None,
                    detail="request_id must be non-empty",
                )

            canonical_model = _normalize_model_id(model)
            if canonical_model not in cfg.metered_models:
                return Refuse(
                    code="anthropic_model_unpriced",
                    http_status=422,
                    reservation_usd=None,
                    headroom_usd=None,
                    detail=f"model {model!r} is not in metered_models",
                )
            rate_row = resolve_pinned_rate(canonical_model, path=self._rates_path)
            if rate_row is None:
                return Refuse(
                    code="anthropic_model_unpriced",
                    http_status=422,
                    reservation_usd=None,
                    headroom_usd=None,
                    detail=f"model {canonical_model!r} has no pinned rate row",
                )

            def _txn(conn: Any) -> Admit | Refuse:
                CreditLedger.expire_stale_reservations(
                    conn,
                    now=now_utc,
                    ttl_minutes=cfg.reservation_ttl_minutes,
                    window_start=window_start,
                    reset_at=reset_at,
                )
                snap = CreditLedger.compute_window_snapshot(
                    conn,
                    window_start=window_start,
                    reset_at=reset_at,
                    as_of=as_of_dt,
                    now=now_utc,
                    reservation_ttl_minutes=cfg.reservation_ttl_minutes,
                )
                existing_any = conn.execute(
                    "SELECT 1 FROM entries WHERE request_id = ? LIMIT 1",
                    (request_id,),
                ).fetchone()
                if existing_any is not None:
                    return Refuse(
                        code="anthropic_reservation_refused",
                        http_status=422,
                        reservation_usd=None,
                        headroom_usd=None,
                        detail=f"request_id {request_id!r} already has ledger rows",
                    )
                allowance = self._effective_mcp_allowance(
                    cfg,
                    window_start_iso=win_start_iso,
                    db_overrun_count=snap.overrun_count,
                )
                effective_cfg = (
                    dataclasses.replace(cfg, mcp_connector_allowance_usd=allowance)
                    if allowance != cfg.mcp_connector_allowance_usd
                    else cfg
                )
                reservation = worst_case_usd(
                    upstream_body, rate_row, token_count, effective_cfg
                )

                cap_usd, hard_headroom, mcp_headroom = self._compute_caps(
                    cfg,
                    committed_usd=snap.committed_usd,
                    open_usd=snap.open_reservations_usd,
                    spend_since_as_of_usd=snap.spend_since_as_of_usd,
                )

                if snap.latch_active:
                    return Refuse(
                        code="anthropic_cycle_budget_exhausted",
                        http_status=429,
                        reservation_usd=reservation.usd,
                        headroom_usd=Decimal("0"),
                        detail=(
                            f"credit latch active until {reset_at.isoformat()}: "
                            f"{snap.latch_reason}"
                        ),
                    )

                window_total = snap.committed_usd + snap.open_reservations_usd
                if reservation.mcp_class:
                    if snap.open_mcp_count > 0:
                        return Refuse(
                            code="anthropic_cycle_budget_exhausted",
                            http_status=429,
                            reservation_usd=reservation.usd,
                            headroom_usd=mcp_headroom,
                            detail=(
                                "MCP-connector requests are serialized one at a time; "
                                "another MCP reservation is open"
                            ),
                        )
                    mcp_cap = min(cap_usd, cfg.mcp_connector_stop_usd)
                    if window_total + reservation.usd > mcp_cap:
                        return Refuse(
                            code="anthropic_cycle_budget_exhausted",
                            http_status=429,
                            reservation_usd=reservation.usd,
                            headroom_usd=mcp_headroom,
                            detail=(
                                f"MCP reservation {reservation.usd} USD exceeds "
                                f"headroom {mcp_headroom} USD (MCP cap {mcp_cap} USD)"
                            ),
                        )
                    remaining_headroom = mcp_cap - (window_total + reservation.usd)
                else:
                    if window_total + reservation.usd > cap_usd:
                        return Refuse(
                            code="anthropic_cycle_budget_exhausted",
                            http_status=429,
                            reservation_usd=reservation.usd,
                            headroom_usd=hard_headroom,
                            detail=(
                                f"reservation {reservation.usd} USD exceeds "
                                f"cycle headroom {hard_headroom} USD (cap {cap_usd} USD)"
                            ),
                        )
                    remaining_headroom = cap_usd - (window_total + reservation.usd)

                conn.execute(
                    """
                    INSERT INTO entries (
                        kind, request_id, model, usd, created_at, window_start, tag, caller_tag, mcp_class
                    ) VALUES ('reserve', ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        request_id,
                        canonical_model,
                        str(reservation.usd),
                        now_utc.isoformat(),
                        win_start_iso,
                        tag,
                        1 if reservation.mcp_class else 0,
                    ),
                )
                return Admit(
                    request_id=request_id,
                    model=canonical_model,
                    reservation_usd=reservation.usd,
                    headroom_usd=remaining_headroom,
                    window_start=window_start,
                    reset_at=reset_at,
                    mcp_class=reservation.mcp_class,
                    reservation=reservation,
                )

            return ledger.run_immediate(_txn)

        except ReservationRefused as exc:
            return Refuse(
                code="anthropic_reservation_refused",
                http_status=422,
                reservation_usd=None,
                headroom_usd=None,
                detail=exc.detail,
            )
        except (CreditConfigInvalid, LedgerUnavailable, Exception) as exc:
            return Refuse(
                code="anthropic_credit_gate_unavailable",
                http_status=503,
                reservation_usd=None,
                headroom_usd=None,
                detail=str(exc),
            )

    def commit(
        self,
        request_id: str,
        usage: dict[str, Any] | None,
        *,
        now: datetime | None = None,
        floor: bool = False,
    ) -> Decimal:
        """Commit actual usage cost (or ``max(computed, reservation)`` on partial usage)."""
        now_utc = _normalize_now(now)
        cfg, ledger = self._resolve_config_and_ledger()

        partial_event: dict[str, Any] | None = None
        overrun_event: dict[str, Any] | None = None

        def _txn(conn: Any) -> Decimal:
            nonlocal partial_event, overrun_event
            existing_release = conn.execute(
                """
                SELECT id FROM entries
                WHERE request_id = ? AND kind = 'release'
                LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if existing_release is not None:
                raise LedgerUnavailable(
                    f"cannot commit already-released request_id={request_id!r}"
                )

            reserve_row = conn.execute(
                """
                SELECT model, usd, window_start, tag, mcp_class
                FROM entries
                WHERE request_id = ? AND kind = 'reserve'
                ORDER BY id DESC LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if reserve_row is None:
                raise LedgerUnavailable(
                    f"no reservation found for request_id={request_id!r}"
                )

            model_id = str(reserve_row["model"])
            reservation_usd = Decimal(str(reserve_row["usd"]))
            win_start_iso = str(reserve_row["window_start"])
            tag = reserve_row["tag"]
            mcp_class = int(reserve_row["mcp_class"] or 0)

            commit_rows = conn.execute(
                """
                SELECT usd, tag FROM entries
                WHERE request_id = ? AND kind = 'commit'
                ORDER BY id ASC
                """,
                (request_id,),
            ).fetchall()
            prior_total = sum(Decimal(str(r["usd"])) for r in commit_rows)
            only_ttl = bool(commit_rows) and all(
                (r["tag"] or "") == "ttl_expired" for r in commit_rows
            )
            if commit_rows and not only_ttl and not floor:
                return prior_total

            rate_row = resolve_pinned_rate(model_id, path=self._rates_path)
            if rate_row is None:
                computed: Decimal | None = None
                missing_fields: tuple[str, ...] = (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                    "output_tokens",
                )
                unknown_server_tool = False
            else:
                computed, missing_fields, unknown_server_tool = usage_cost_usd_for_config(
                    usage, rate_row, cfg
                )

            partial = bool(missing_fields) or computed is None or unknown_server_tool
            if partial or floor:
                target_total = max(
                    computed if computed is not None else Decimal("0"),
                    reservation_usd,
                )
            else:
                target_total = computed if computed is not None else reservation_usd

            insert_usd = target_total - prior_total
            must_finalize_after_ttl = only_ttl and insert_usd <= Decimal("0")
            first_zero_commit = not commit_rows and insert_usd <= Decimal("0")
            if insert_usd <= Decimal("0") and not (
                must_finalize_after_ttl or first_zero_commit
            ):
                return prior_total
            if insert_usd <= Decimal("0"):
                insert_usd = Decimal("0")

            if partial:
                partial_event = {
                    "request_id": request_id,
                    "model": model_id,
                    "missing_fields": list(missing_fields),
                    "computed_usd": str(computed) if computed is not None else None,
                    "reservation_usd": str(reservation_usd),
                    "committed_usd": str(target_total),
                }

            conn.execute(
                """
                INSERT INTO entries (
                    kind, request_id, model, usd, created_at, window_start, tag, caller_tag, mcp_class
                ) VALUES ('commit', ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    request_id,
                    model_id,
                    str(insert_usd),
                    now_utc.isoformat(),
                    win_start_iso,
                    tag,
                    mcp_class,
                ),
            )

            if target_total > reservation_usd:
                self._overrun_multiplier_by_window[win_start_iso] = (
                    self._overrun_multiplier_by_window.get(win_start_iso, 0) + 1
                )
                overrun_event = {
                    "request_id": request_id,
                    "model": model_id,
                    "reservation_usd": str(reservation_usd),
                    "actual_usd": str(target_total),
                    "mcp_class": bool(mcp_class),
                    "window_start": win_start_iso,
                }

            return target_total

        committed = ledger.run_immediate(_txn)
        if partial_event is not None:
            self._emit("anthropic.credit.usage_partial", partial_event)
        if overrun_event is not None:
            self._emit("anthropic.credit.overrun", overrun_event)
        return committed

    def commit_full(
        self,
        request_id: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> Decimal:
        """Commit the full reservation amount on stream abort, cancel, timeout, or upstream error."""
        now_utc = _normalize_now(now)
        _cfg, ledger = self._resolve_config_and_ledger()

        def _txn(conn: Any) -> Decimal:
            existing_commit = conn.execute(
                """
                SELECT usd FROM entries
                WHERE request_id = ? AND kind = 'commit'
                ORDER BY id DESC LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if existing_commit is not None:
                return Decimal(str(existing_commit["usd"]))

            reserve_row = conn.execute(
                """
                SELECT model, usd, window_start, tag, mcp_class
                FROM entries
                WHERE request_id = ? AND kind = 'reserve'
                ORDER BY id DESC LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if reserve_row is None:
                raise LedgerUnavailable(
                    f"no reservation found for request_id={request_id!r}"
                )

            reservation_usd = Decimal(str(reserve_row["usd"]))
            commit_tag = reason if reason else reserve_row["tag"]
            conn.execute(
                """
                INSERT INTO entries (
                    kind, request_id, model, usd, created_at, window_start, tag, caller_tag, mcp_class
                ) VALUES ('commit', ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    request_id,
                    reserve_row["model"],
                    str(reservation_usd),
                    now_utc.isoformat(),
                    reserve_row["window_start"],
                    commit_tag,
                    int(reserve_row["mcp_class"] or 0),
                ),
            )
            return reservation_usd

        return ledger.run_immediate(_txn)

    def release(
        self,
        request_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """Release a reservation that provably never reached upstream."""
        now_utc = _normalize_now(now)
        _cfg, ledger = self._resolve_config_and_ledger()

        def _txn(conn: Any) -> None:
            existing_release = conn.execute(
                """
                SELECT id FROM entries
                WHERE request_id = ? AND kind = 'release'
                LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if existing_release is not None:
                return

            existing_commit = conn.execute(
                """
                SELECT id FROM entries
                WHERE request_id = ? AND kind = 'commit'
                LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if existing_commit is not None:
                raise LedgerUnavailable(
                    f"cannot release already-committed request_id={request_id!r}"
                )

            reserve_row = conn.execute(
                """
                SELECT model, window_start, tag, mcp_class
                FROM entries
                WHERE request_id = ? AND kind = 'reserve'
                ORDER BY id DESC LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if reserve_row is None:
                raise LedgerUnavailable(
                    f"no reservation found for request_id={request_id!r}"
                )

            conn.execute(
                """
                INSERT INTO entries (
                    kind, request_id, model, usd, created_at, window_start, tag, caller_tag, mcp_class
                ) VALUES ('release', ?, ?, '0.000000', ?, ?, ?, NULL, ?)
                """,
                (
                    request_id,
                    reserve_row["model"],
                    now_utc.isoformat(),
                    reserve_row["window_start"],
                    reserve_row["tag"],
                    int(reserve_row["mcp_class"] or 0),
                ),
            )

        ledger.run_immediate(_txn)

    def latch(
        self,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """Write a cycle latch refusing all admissions until ``reset_at``."""
        now_utc = _normalize_now(now)
        cfg, ledger = self._resolve_config_and_ledger()
        window_start, _reset_at = meter_window(now_utc, cfg)
        win_start_iso = window_start.isoformat()

        def _txn(conn: Any) -> None:
            conn.execute(
                """
                INSERT INTO entries (
                    kind, request_id, model, usd, created_at, window_start, tag, caller_tag, mcp_class
                ) VALUES ('latch', NULL, NULL, '0.000000', ?, ?, ?, NULL, 0)
                """,
                (
                    now_utc.isoformat(),
                    win_start_iso,
                    reason or "credit_balance_too_low",
                ),
            )

        ledger.run_immediate(_txn)

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        """Return current window bounds, spend, headroom, latch state, and last 20 entries."""
        now_utc = _normalize_now(now)
        cfg, ledger = self._resolve_config_and_ledger()
        window_start, reset_at = meter_window(now_utc, cfg)
        win_start_iso = window_start.isoformat()
        reset_at_iso = reset_at.isoformat()
        as_of_dt = (
            parse_utc_iso(cfg.available_balance.as_of)
            if cfg.available_balance.as_of
            else None
        )

        def _txn(conn: Any) -> dict[str, Any]:
            CreditLedger.expire_stale_reservations(
                conn,
                now=now_utc,
                ttl_minutes=cfg.reservation_ttl_minutes,
                window_start=window_start,
                reset_at=reset_at,
            )
            snap = CreditLedger.compute_window_snapshot(
                conn,
                window_start=window_start,
                reset_at=reset_at,
                as_of=as_of_dt,
                now=now_utc,
                reservation_ttl_minutes=cfg.reservation_ttl_minutes,
            )
            entries = CreditLedger.recent_entries(conn, limit=20)
            cap_usd, hard_headroom, mcp_headroom = self._compute_caps(
                cfg,
                committed_usd=snap.committed_usd,
                open_usd=snap.open_reservations_usd,
                spend_since_as_of_usd=snap.spend_since_as_of_usd,
            )
            allowance = self._effective_mcp_allowance(
                cfg,
                window_start_iso=win_start_iso,
                db_overrun_count=snap.overrun_count,
            )
            entries_dicts = [e.to_dict() for e in entries]
            return {
                "window_start": win_start_iso,
                "reset_at": reset_at_iso,
                "window_reset_at": reset_at_iso,
                "committed": snap.committed_usd,
                "committed_usd": snap.committed_usd,
                "open_reservations": snap.open_reservations_usd,
                "open_reservations_usd": snap.open_reservations_usd,
                "open_reservation_count": snap.open_reservation_count,
                "open_mcp_reservations": snap.open_mcp_count,
                "headroom_usd": hard_headroom,
                "hard_stop_headroom_usd": hard_headroom,
                "mcp_headroom_usd": mcp_headroom,
                "mcp_stop_headroom_usd": mcp_headroom,
                "balance_cap": cap_usd,
                "balance_cap_usd": cap_usd,
                "hard_stop_usd": cfg.hard_stop_usd,
                "mcp_connector_stop_usd": cfg.mcp_connector_stop_usd,
                "mcp_connector_allowance_usd": allowance,
                "latch": {
                    "active": snap.latch_active,
                    "reason": snap.latch_reason,
                    "reset_at": reset_at_iso if snap.latch_active else None,
                },
                "latched": snap.latch_active,
                "latch_reason": snap.latch_reason,
                "entries": entries_dicts,
                "last_entries": entries_dicts,
            }

        return ledger.run_immediate(_txn)

    def healthy(self) -> bool:
        """Return True iff config validates and the SQLite ledger is reachable."""
        try:
            _cfg, ledger = self._resolve_config_and_ledger()
            ledger.run_immediate(
                lambda conn: conn.execute("SELECT 1 FROM entries LIMIT 1").fetchone()
            )
            return True
        except Exception:
            return False


_singleton_policy: CreditPolicy | None = None


def get_policy() -> CreditPolicy:
    """Lazily build and return the module-level ``CreditPolicy`` singleton."""
    global _singleton_policy
    if _singleton_policy is None:
        _singleton_policy = CreditPolicy()
    return _singleton_policy


def set_policy(policy: CreditPolicy | None) -> None:
    """Inject or reset the module-level ``CreditPolicy`` singleton (for tests)."""
    global _singleton_policy
    _singleton_policy = policy


def admit(
    request_id: str,
    model: str,
    upstream_body: dict[str, Any],
    tag: str | None = None,
    cost_intent: str | None = None,
    token_count: int | None = None,
    *,
    now: datetime | None = None,
) -> Admit | Refuse:
    """Module-level convenience wrapper delegating to ``get_policy().admit``."""
    return get_policy().admit(
        request_id,
        model,
        upstream_body,
        tag,
        cost_intent,
        token_count,
        now=now,
    )


def commit(
    request_id: str,
    usage: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    floor: bool = False,
) -> Decimal:
    """Module-level convenience wrapper delegating to ``get_policy().commit``."""
    return get_policy().commit(request_id, usage, now=now, floor=floor)


def commit_full(
    request_id: str,
    reason: str,
    *,
    now: datetime | None = None,
) -> Decimal:
    """Module-level convenience wrapper delegating to ``get_policy().commit_full``."""
    return get_policy().commit_full(request_id, reason, now=now)


def release(
    request_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Module-level convenience wrapper delegating to ``get_policy().release``."""
    get_policy().release(request_id, now=now)


def latch(
    reason: str,
    *,
    now: datetime | None = None,
) -> None:
    """Module-level convenience wrapper delegating to ``get_policy().latch``."""
    get_policy().latch(reason, now=now)


def status(now: datetime | None = None) -> dict[str, Any]:
    """Module-level convenience wrapper delegating to ``get_policy().status``."""
    return get_policy().status(now=now)


def healthy() -> bool:
    """Module-level convenience wrapper delegating to ``get_policy().healthy``."""
    return get_policy().healthy()
