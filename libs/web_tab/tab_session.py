"""TabSession — conversation URL bound to a CDP page target (feature a:40962 shape)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from web_tab.conversation_id import conversation_id_from_url


@dataclass(frozen=True)
class TabSession:
    """One actuation target: CDP targetId + conversation URL + surface."""

    target_id: str
    cdp_url: str
    conversation_url: str
    surface: str
    web_socket_debugger_url: str | None = None
    conversation_id: str | None = None
    created_by_agent: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def tab_session_from_row(
    *,
    target_id: str,
    cdp_url: str,
    conversation_url: str,
    surface: str,
    web_socket_debugger_url: str | None = None,
    created_by_agent: bool = False,
) -> TabSession:
    cid = conversation_id_from_url(conversation_url)
    return TabSession(
        target_id=target_id,
        cdp_url=cdp_url,
        conversation_url=conversation_url,
        surface=surface,
        web_socket_debugger_url=web_socket_debugger_url,
        conversation_id=cid,
        created_by_agent=created_by_agent,
    )
