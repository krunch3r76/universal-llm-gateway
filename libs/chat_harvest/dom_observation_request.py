"""Request model, argument validation, and collector script configuration."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from browser_governance.contract import EXCLUDED_MESSAGE_ANCESTOR_SELECTOR
from claude_bundles.cdp_registry.ports import PORT_RANGE
from pydantic import BaseModel

from chat_harvest.readonly_cdp import conversation_tail

MESSAGE_SELECTORS = [
    "[data-testid='user-message']",
    "[data-testid='assistant-message']",
    "div[class*='font-claude']",
]
USER_MESSAGE_SELECTORS = ["[data-testid='user-message']"]
COMPOSER_SELECTORS = [
    "[contenteditable='true']",
    "textarea",
    "form",
    "[data-testid='chat-input']",
]
EXCLUDED_PARTS = [p.strip() for p in EXCLUDED_MESSAGE_ANCESTOR_SELECTOR.split(",") if p.strip()]

WALL_BUDGET_S = 30.0
READY_POLL_S = 0.25
READY_BUDGET_S = 10.0
STABLE_POLL_S = 0.5
STABLE_BUDGET_S = 5.0


def script_config(terms: list[str], max_nodes: int) -> dict[str, Any]:
    return {
        "match_terms": terms,
        "max_nodes": max_nodes,
        "message_sel": MESSAGE_SELECTORS,
        "user_message_sel": USER_MESSAGE_SELECTORS,
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


def url_matches(page_url: str, chat_url: str) -> bool:
    tail = conversation_tail(chat_url)
    return bool(tail and tail in (page_url or "").lower())


def open_tab_state() -> dict[str, Any]:
    return {
        "matching_tabs": 0,
        "target_id": None,
        "minted_tab_closed": None,
        "visibility_state": None,
        "time_origin_ms": None,
        "origin": None,
    }

DEFAULT_MAX_NODES = 20
MAX_NODES_CAP = 50


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
