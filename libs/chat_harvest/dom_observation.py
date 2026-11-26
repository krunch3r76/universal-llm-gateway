"""DOM observation models, validation, collector orchestration, and response shaping."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlparse

from browser_governance.contract import EXCLUDED_MESSAGE_ANCESTOR_SELECTOR
from claude_bundles.cdp_registry.ports import PORT_RANGE
from pydantic import BaseModel

# Tests and callers import shaping helpers from this module.
from chat_harvest.readonly_cdp import (  # noqa: F401
    COLLECT_SCRIPT,
    READY_SCRIPT,
    ReadonlyCDP,
    build_observation_block,
    cap_observation_size,
    shape_node,
)

MESSAGE_SELECTORS = [
    "[data-testid='user-message']",
    "[data-testid='assistant-message']",
    "div[class*='font-claude']",
]
COMPOSER_SELECTORS = [
    "[contenteditable='true']",
    "textarea",
    "form",
    "[data-testid='chat-input']",
]
EXCLUDED_PARTS = [p.strip() for p in EXCLUDED_MESSAGE_ANCESTOR_SELECTOR.split(",") if p.strip()]

DEFAULT_MAX_NODES = 20
MAX_NODES_CAP = 50
WALL_BUDGET_S = 30.0
READY_POLL_S = 0.25
READY_BUDGET_S = 10.0
STABLE_POLL_S = 0.5
STABLE_BUDGET_S = 5.0


class ChatObserveRequest(BaseModel):
    url: str
    site: Literal["grok", "claude"] | None = None
    cdp_url: str | None = None
    match_terms: list[str]
    max_nodes: int = DEFAULT_MAX_NODES
    open_on_demand: bool = True


class InvalidPattern(Exception):  # noqa: N818
    def __init__(self, *, field: str, limit: str, got: str, message: str) -> None:
        self.field = field
        self.limit = limit
        self.got = got
        self.message = message
        super().__init__(message)


class InvalidCdpUrl(Exception):  # noqa: N818
    pass


def validate_match_terms(terms: list[str]) -> list[str]:
    if not terms or len(terms) > 8:
        raise InvalidPattern(
            field="match_terms",
            limit="1..8",
            got=str(len(terms)),
            message="match_terms must contain 1 to 8 terms",
        )
    out: list[str] = []
    for raw in terms:
        if not isinstance(raw, str):
            raise InvalidPattern(
                field="match_terms",
                limit="strings only",
                got=type(raw).__name__,
                message="match_terms must be strings",
            )
        term = raw.strip()
        if len(term) < 2 or len(term) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in term):
            raise InvalidPattern(
                field="match_terms",
                limit="2..64 chars, no controls",
                got=str(len(term)),
                message="invalid match term",
            )
        out.append(term)
    return [t.lower() for t in out]


def validate_max_nodes(value: int) -> None:
    if value < 1 or value > MAX_NODES_CAP:
        raise InvalidPattern(
            field="max_nodes",
            limit=f"1..{MAX_NODES_CAP}",
            got=str(value),
            message="max_nodes out of range",
        )


def validate_cdp_url(cdp_url: str) -> str:
    parsed = urlparse(cdp_url.strip())
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise InvalidCdpUrl("cdp_url must be loopback http")
    try:
        port = int(parsed.port or 0)
    except ValueError as exc:
        raise InvalidCdpUrl("invalid cdp port") from exc
    if port != 9222 and port not in PORT_RANGE:
        raise InvalidCdpUrl("cdp_url port not in fleet range")
    host = parsed.hostname
    return f"http://{host}:{port}"


def _script_config(terms: list[str], max_nodes: int) -> dict[str, Any]:
    return {
        "match_terms": terms,
        "max_nodes": max_nodes,
        "message_sel": MESSAGE_SELECTORS,
        "composer_sel": COMPOSER_SELECTORS,
        "excluded_parts": EXCLUDED_PARTS,
        "caps": {
            "text_chars": 500,
            "controls_per_node": 8,
            "control_text": 80,
            "aria_attrs": 12,
            "aria_val": 120,
            "class_prefix": 120,
            "path_entries": 8,
            "path_part": 80,
            "text_nodes_scanned": 50000,
        },
    }


def run_dom_observation(
    *,
    chat_url: str,
    conversation_id: str,
    cdp_url: str,
    match_terms: list[str],
    max_nodes: int,
    open_on_demand: bool,
    scope: str,
    transport: ReadonlyCDP | None = None,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Synchronous DOM observation; returns top-level response fields."""
    clock = monotonic or time.monotonic
    start = clock()
    deadline = start + WALL_BUDGET_S
    cdp = transport or ReadonlyCDP(cdp_url)
    readiness = {"polls": 0, "waited_ms": 0, "turns_seen": False, "stable": False}
    minted_target: str | None = None
    minted_closed: bool | None = None
    tab_info: dict[str, Any] = {
        "matching_tabs": 0,
        "target_id": None,
        "minted_tab_closed": None,
        "visibility_state": None,
        "time_origin_ms": None,
        "origin": None,
    }
    conn: Any = None

    def _budget_left() -> float:
        return deadline - clock()

    try:
        targets = cdp.list_page_targets()
        matching = [t for t in targets if _url_matches(t.get("url") or "", chat_url)]
        tab_info["matching_tabs"] = len(matching)
        target = matching[0] if matching else cdp.find_matching_target(chat_url)
        if target is None:
            if not open_on_demand:
                return _response(
                    outcome="no_tab",
                    looked=False,
                    chat_url=chat_url,
                    conversation_id=conversation_id,
                    observation=None,
                    code=None,
                )
            conn, minted_target = cdp.open_tab(chat_url)
            tab_info["origin"] = "opened_on_demand"
            tab_info["target_id"] = minted_target
        else:
            conn = cdp._page_connection(target)
            tab_info["origin"] = "existing_tab"
            tab_info["target_id"] = target.get("id") or target.get("targetId")

        cfg = _script_config(match_terms, max_nodes)
        cfg_ready = {k: cfg[k] for k in ("message_sel",) if k in cfg}

        ready_deadline = min(deadline, clock() + READY_BUDGET_S)
        login_wall = False
        while clock() < ready_deadline and _budget_left() > 0:
            readiness["polls"] += 1
            t0 = clock()
            ready_val = cdp.evaluate(conn, READY_SCRIPT, cfg_ready)
            readiness["waited_ms"] += int((clock() - t0) * 1000)
            if isinstance(ready_val, dict):
                login_wall = bool(ready_val.get("login_wall"))
                turns = int(ready_val.get("turn_nodes") or 0)
                tab_info["visibility_state"] = ready_val.get("visibility_state")
                if login_wall:
                    return _response(
                        outcome="unauthenticated",
                        looked=False,
                        chat_url=chat_url,
                        conversation_id=conversation_id,
                        observation=build_observation_block(
                            looked=False,
                            raw_collect={"nodes": [], "counts": {"total": 0}, "page": {}},
                            query={"match_terms": match_terms, "max_nodes": max_nodes, "scope": scope},
                            readiness=readiness,
                            tab=tab_info,
                            cdp_methods=cdp.method_counts,
                        ),
                    )
                if turns > 0:
                    readiness["turns_seen"] = True
                    break
            time.sleep(READY_POLL_S)

        stable_deadline = min(deadline, clock() + STABLE_BUDGET_S)
        last_count: int | None = None
        stable_hits = 0
        while clock() < stable_deadline and _budget_left() > 0:
            t0 = clock()
            ready_val = cdp.evaluate(conn, READY_SCRIPT, cfg_ready)
            readiness["waited_ms"] += int((clock() - t0) * 1000)
            readiness["polls"] += 1
            if isinstance(ready_val, dict):
                count = int(ready_val.get("element_count") or 0)
                if last_count is not None and count == last_count:
                    stable_hits += 1
                    if stable_hits >= 3:
                        readiness["stable"] = True
                        break
                else:
                    stable_hits = 1
                last_count = count
            time.sleep(STABLE_POLL_S)

        if _budget_left() <= 0:
            if minted_target:
                with contextlib.suppress(Exception):
                    cdp.close_tab(minted_target)
                tab_info["minted_tab_closed"] = True
            return _response(
                outcome="unreachable",
                looked=False,
                chat_url=chat_url,
                conversation_id=conversation_id,
                observation=build_observation_block(
                    looked=False,
                    raw_collect={"nodes": [], "counts": {"total": 0}, "page": {}},
                    query={"match_terms": match_terms, "max_nodes": max_nodes, "scope": scope},
                    readiness=readiness,
                    tab=tab_info,
                    cdp_methods=cdp.method_counts,
                ),
                code="timeout",
            )

        collect_raw = cdp.evaluate(conn, COLLECT_SCRIPT, cfg)
        if not isinstance(collect_raw, dict):
            collect_raw = {}
        observation = build_observation_block(
            looked=True,
            raw_collect=collect_raw,
            query={"match_terms": match_terms, "max_nodes": max_nodes, "scope": scope},
            readiness=readiness,
            tab=tab_info,
            cdp_methods=dict(cdp.method_counts),
        )
        if minted_target:
            observation.setdefault("tab", {})["minted_tab_closed"] = True
        return _response(
            outcome="observed",
            looked=True,
            chat_url=chat_url,
            conversation_id=conversation_id,
            observation=observation,
            opened_on_demand=bool(minted_target),
        )
    except Exception:
        return _response(
            outcome="unreachable",
            looked=False,
            chat_url=chat_url,
            conversation_id=conversation_id,
            observation=build_observation_block(
                looked=False,
                raw_collect={"nodes": [], "counts": {"total": 0}, "page": {}},
                query={"match_terms": match_terms, "max_nodes": max_nodes, "scope": scope},
                readiness=readiness,
                tab=tab_info,
                cdp_methods=cdp.method_counts if cdp else {},
            ),
            code="unreachable",
        )
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
        if minted_target:
            try:
                cdp.close_tab(minted_target)
                minted_closed = True
            except Exception:
                minted_closed = False
            if minted_closed is not None:
                tab_info["minted_tab_closed"] = minted_closed


def _response(
    *,
    outcome: str,
    looked: bool,
    chat_url: str,
    conversation_id: str,
    observation: dict[str, Any] | None,
    code: str | None = None,
    opened_on_demand: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "outcome": outcome,
        "site": "claude",
        "conversation_id": conversation_id,
        "url": chat_url,
        "opened_on_demand": opened_on_demand,
        "code": code,
        "reason": code,
    }
    if observation is not None:
        observation["looked"] = looked
        body["observation"] = observation
    return body


def _url_matches(page_url: str, chat_url: str) -> bool:
    return _conversation_tail(chat_url) in (page_url or "").lower()


def _conversation_tail(url: str) -> str:
    parts = url.rstrip("/").split("/")
    return parts[-1].lower() if parts else ""
