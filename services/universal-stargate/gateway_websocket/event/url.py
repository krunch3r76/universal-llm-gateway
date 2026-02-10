"""URL conversion utilities."""


def ws_url_to_http(ws_url: str) -> str:
    """
    Convert WebSocket URL to HTTP gateway URL.

    Single source of truth for ws→http mapping.

    Args:
        ws_url: WebSocket URL (e.g., "ws://host:port/ws/stargate")

    Returns:
        HTTP gateway URL (e.g., "http://host:port")

    Invariant:
        ws://host:port/ws/stargate ⟹ http://host:port
        wss://host:port/ws/stargate ⟹ https://host:port
    """
    return (
        ws_url.replace("ws://", "http://")
        .replace("wss://", "https://")
        .replace("/ws/stargate", "")
    )
