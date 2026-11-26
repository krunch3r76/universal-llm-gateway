"""Read-only CDP transport — one guarded socket send, method and script allowlists."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

PAGE_METHODS = frozenset({"Runtime.evaluate"})
BROWSER_METHODS = frozenset({"Target.createTarget", "Target.closeTarget"})
HTTP_PATH_ALLOWLIST = frozenset({"/json/version", "/json/list"})


class ReadOnlyViolation(Exception):  # noqa: N818
    """Attempted to send a forbidden CDP method or evaluate an unregistered script."""


@dataclass(frozen=True, slots=True)
class RegisteredScript:
    """Fixed collector script loaded at import time."""

    name: str
    text: str


def _load_script(filename: str) -> RegisteredScript:
    text = (Path(__file__).with_name(filename)).read_text(encoding="utf-8").strip()
    return RegisteredScript(name=filename, text=text.strip())


READY_SCRIPT = _load_script("dom_observation_ready.js")
COLLECT_SCRIPT = _load_script("dom_observation_collect.js")

_open_connection: Callable[..., Any] | None = None


def conversation_tail(url: str) -> str:
    m = re.search(r"/chat/([^/?#]+)", url, re.I)
    if m:
        return m.group(1).lower()
    parts = url.rstrip("/").split("/")
    return parts[-1].lower() if parts else ""


def _default_open_connection(ws_url: str, *, header: list[str], timeout_s: float):
    import websocket

    return websocket.create_connection(ws_url, timeout=timeout_s, header=header)


def set_open_connection(fn: Callable[..., Any] | None) -> None:
    global _open_connection
    _open_connection = fn


def _connect(ws_url: str, *, port: int, timeout_s: float):
    opener = _open_connection or _default_open_connection
    return opener(
        ws_url,
        header=[f"Origin: http://127.0.0.1:{port}"],
        timeout_s=timeout_s,
    )


def _guarded_send(
    conn: Any,
    method: str,
    params: dict[str, Any],
    *,
    allowlist: frozenset[str],
    frames: list[dict[str, Any]],
    method_counts: dict[str, int],
) -> int:
    if method not in allowlist:
        raise ReadOnlyViolation(f"forbidden CDP method: {method}")
    msg_id = len(frames) + 1
    payload = {"id": msg_id, "method": method, "params": params}
    raw = json.dumps(payload)
    conn.send(raw)
    frames.append({"id": msg_id, "method": method, "params": params, "raw": raw})
    method_counts[method] = method_counts.get(method, 0) + 1
    return msg_id


def _recv_for_id(conn: Any, msg_id: int, *, timeout_s: float) -> dict[str, Any]:
    conn.settimeout(timeout_s)
    while True:
        message = json.loads(conn.recv())
        if message.get("id") == msg_id:
            return message


@dataclass
class ReadonlyCDP:
    """CDP session with recorded frames and method counts."""

    cdp_base: str
    timeout_s: float = 5.0
    frames: list[dict[str, Any]] = field(default_factory=list)
    method_counts: dict[str, int] = field(default_factory=dict)
    _opened_target_id: str | None = None
    _http_get: Callable[[str], Any] | None = None

    @property
    def port(self) -> int:
        parsed = urlparse(self.cdp_base)
        return int(parsed.port or 9222)

    def http_get_json(self, path: str) -> Any:
        if path not in HTTP_PATH_ALLOWLIST:
            raise ReadOnlyViolation(f"forbidden HTTP path: {path}")
        url = f"{self.cdp_base.rstrip('/')}{path}"
        if self._http_get is not None:
            return self._http_get(url)
        try:
            with urlopen(url, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise OSError(str(exc)) from exc

    def list_page_targets(self) -> list[dict[str, Any]]:
        raw = self.http_get_json("/json/list")
        if not isinstance(raw, list):
            return []
        return [t for t in raw if t.get("type") == "page"]

    def _page_connection(self, target: dict[str, Any]) -> Any:
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise OSError("missing webSocketDebuggerUrl")
        return _connect(ws_url, port=self.port, timeout_s=self.timeout_s)

    def _browser_connection(self) -> tuple[Any, str]:
        version = self.http_get_json("/json/version")
        ws_url = version.get("webSocketDebuggerUrl") if isinstance(version, dict) else None
        if not ws_url:
            raise OSError("missing browser webSocketDebuggerUrl")
        return _connect(ws_url, port=self.port, timeout_s=self.timeout_s), ws_url

    def evaluate(
        self,
        conn: Any,
        script: RegisteredScript,
        argument: dict[str, Any],
    ) -> Any:
        if script is not READY_SCRIPT and script is not COLLECT_SCRIPT:
            raise ReadOnlyViolation("script is not registered for read-only evaluate")
        expression = "(" + script.text + ")(" + json.dumps(argument, ensure_ascii=True) + ")"
        msg_id = _guarded_send(
            conn,
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "userGesture": False,
                "awaitPromise": False,
                "includeCommandLineAPI": False,
                "timeout": int(self.timeout_s * 1000),
            },
            allowlist=PAGE_METHODS,
            frames=self.frames,
            method_counts=self.method_counts,
        )
        message = _recv_for_id(conn, msg_id, timeout_s=self.timeout_s)
        if "error" in message:
            raise OSError(str(message["error"]))
        result = message.get("result", {}).get("result", {})
        return result.get("value")

    def open_tab(self, url: str) -> tuple[Any, str]:
        conn, _ = self._browser_connection()
        try:
            msg_id = _guarded_send(
                conn,
                "Target.createTarget",
                {"url": url, "background": True},
                allowlist=BROWSER_METHODS,
                frames=self.frames,
                method_counts=self.method_counts,
            )
            message = _recv_for_id(conn, msg_id, timeout_s=self.timeout_s)
            if "error" in message:
                raise OSError(str(message["error"]))
            target_id = message.get("result", {}).get("targetId")
            if not target_id:
                raise OSError("Target.createTarget returned no targetId")
            self._opened_target_id = str(target_id)
            for target in self.list_page_targets():
                if target.get("id") == target_id or target.get("targetId") == target_id:
                    page_conn = self._page_connection(target)
                    return page_conn, str(target_id)
            raise OSError("minted target not in /json/list")
        finally:
            conn.close()

    def close_tab(self, target_id: str) -> None:
        if self._opened_target_id is None or target_id != self._opened_target_id:
            raise ReadOnlyViolation("refusing to close a target this session did not open")
        conn, _ = self._browser_connection()
        try:
            msg_id = _guarded_send(
                conn,
                "Target.closeTarget",
                {"targetId": target_id},
                allowlist=BROWSER_METHODS,
                frames=self.frames,
                method_counts=self.method_counts,
            )
            _recv_for_id(conn, msg_id, timeout_s=self.timeout_s)
        finally:
            conn.close()
            self._opened_target_id = None

    def find_matching_target(self, chat_url: str) -> dict[str, Any] | None:
        needle = chat_url.rstrip("/").lower()
        for target in self.list_page_targets():
            page_url = (target.get("url") or "").rstrip("/").lower()
            if page_url == needle or needle in page_url:
                return target
        conv = conversation_tail(chat_url)
        if conv:
            for target in self.list_page_targets():
                page_url = (target.get("url") or "").lower()
                if conv in page_url:
                    return target
        return None
