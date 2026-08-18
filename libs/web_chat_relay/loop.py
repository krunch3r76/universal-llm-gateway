"""Poll grok, relay each new turn through a fresh claude ask, relay the reply back.

Baseline-then-delta on the grok side: history is never dumped, and echo is
broken by comparing sha256 of the last pasted body in each direction. The
claude side carries no session state between turns — see ``claude_leg.py``
module docstring for why a cached session handle does not survive the
satellite's dormant/relaunch lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from web_chat_relay import claude_leg, events, grok_session
from web_chat_relay.claude_leg import ClaudeLegError
from web_chat_relay.grok_session import GrokAuthError

GROK_PREFIX = "Grok:"
CLAUDE_PREFIX = "Claude:"


def body_sha(text: str) -> str:
    """Stable fingerprint of a harvested assistant body."""
    return hashlib.sha256((text or "").encode()).hexdigest()


def has_prefix(text: str, prefix: str) -> bool:
    """True when *text* is already attributed to *prefix* (case-insensitive)."""
    return text.lstrip().lower().startswith(prefix.lower())


def should_relay(
    *,
    new_sha: str,
    baseline_sha: str,
    last_sent_sha: str | None,
    last_received_sha: str | None,
) -> bool:
    """True when *new_sha* is a post-baseline turn we did not just paste."""
    if not new_sha or new_sha == body_sha(""):
        return False
    if new_sha == baseline_sha:
        return False
    if last_sent_sha and new_sha == last_sent_sha:
        return False
    if last_received_sha and new_sha == last_received_sha:
        return False
    return True


@dataclass
class RelayConfig:
    grok_url: str
    grok_cdp_url: str = grok_session.DEFAULT_CDP_URL
    project_ask_url: str = claude_leg.DEFAULT_PROJECT_ASK_URL
    poll_s: float = 5.0
    max_relays: int | None = None
    seed_grok: str = ""
    claude_opener: str = ""
    stop_file: Path = Path("/tmp/grok-claude-relay.stop")
    state_file: Path = Path("/tmp/grok-claude-relay.state.json")
    url_substr: str = grok_session.DEFAULT_GROK_CHAT_ID


@dataclass
class RelayState:
    grok_baseline_sha: str = ""
    last_to_claude: str | None = None
    last_to_grok: str | None = None
    relays: int = 0
    stop_reason: str | None = None


def _stop_requested(cfg: RelayConfig, state: RelayState) -> str | None:
    if state.stop_reason:
        return state.stop_reason
    if cfg.stop_file.exists():
        return "stop_file"
    if cfg.max_relays is not None and state.relays >= cfg.max_relays:
        return "max_relays"
    return None


async def run_relay(cfg: RelayConfig) -> RelayState:
    """Attach grok, seed it if configured, then poll/relay until stop."""
    state = RelayState()

    def _sigint(*_args: object) -> None:
        state.stop_reason = "sigint"

    signal.signal(signal.SIGINT, _sigint)

    pw, _browser, _ctx, grok_page = await grok_session.attach_grok_page(
        cdp_url=cfg.grok_cdp_url, url_substr=cfg.url_substr
    )
    try:
        try:
            grok0 = await grok_session.require_signed_in(grok_page, grok_url=cfg.grok_url)
        except GrokAuthError:
            events.emit(
                events.web_chat_relay_auth_missing(
                    grok_url=cfg.grok_url, page_url=grok_page.url
                )
            )
            raise
        state.grok_baseline_sha = body_sha(grok0.last_assistant)
        events.emit(events.web_chat_relay_started(grok_url=cfg.grok_url, claude_chat_url=None))
        if cfg.seed_grok.strip():
            await grok_session.paste_and_send(grok_page, cfg.seed_grok.strip())
            await grok_session.wait_idle(grok_page)
        await _poll_loop(cfg, state, grok_page)
        return state
    finally:
        events.emit(
            events.web_chat_relay_stopped(
                reason=state.stop_reason or "exit", relays=state.relays
            )
        )
        await pw.stop()


async def _poll_loop(cfg: RelayConfig, state: RelayState, grok_page) -> None:
    """Poll forever. A stall on either leg retries next tick instead of
    ending the process — the documented ``wait_idle`` 180s failure class
    (a live turn still generating) is not a reason to kill an unattended
    relay; ``GrokAuthError`` (real sign-out) still propagates and stops it."""
    while True:
        reason = _stop_requested(cfg, state)
        if reason:
            state.stop_reason = reason
            return
        try:
            await _relay_tick(cfg, state, grok_page)
        except (TimeoutError, ClaudeLegError) as exc:
            events.emit(events.web_chat_relay_tick_retry(detail=repr(exc)[:200]))
        await asyncio.sleep(cfg.poll_s)


def _write_state(cfg: RelayConfig, *, claude_chat_url: str, relays: int) -> None:
    """Best-effort pointer file so a human can find the live claude.ai thread.

    Fresh-per-turn asks (see ``claude_leg.py``) have no fixed URL to bookmark
    -- this is the discoverability substitute: always the most recent one.
    """
    payload = {
        "claude_chat_url": claude_chat_url,
        "relays": relays,
        "updated_at": time.time(),
    }
    with contextlib.suppress(OSError):
        cfg.state_file.write_text(json.dumps(payload))


async def _relay_tick(cfg: RelayConfig, state: RelayState, grok_page) -> None:
    """Harvest grok; if it has a genuinely new, not-already-attributed turn,
    ask claude fresh and relay the reply straight back, each paste tagged
    with its source. A raised error here (network hiccup, still-generating
    turn) leaves state untouched, so the caller's retry re-sends the same
    grok turn rather than dropping or duplicating one."""
    grok = await grok_session.harvest(grok_page)
    if grok.login_wall:
        events.emit(
            events.web_chat_relay_auth_missing(grok_url=cfg.grok_url, page_url=grok.url)
        )
        state.stop_reason = "auth_missing"
        return
    if grok.streaming or grok.stop:
        grok = await grok_session.wait_idle(grok_page)
    grok_text = grok.last_assistant
    grok_sha = body_sha(grok_text)
    if not should_relay(
        new_sha=grok_sha,
        baseline_sha=state.grok_baseline_sha,
        last_sent_sha=state.last_to_grok,
        last_received_sha=state.last_to_claude,
    ):
        return
    if has_prefix(grok_text, CLAUDE_PREFIX):
        # Grok reciting/echoing Claude's own words back -- not new content.
        state.last_to_claude = grok_sha
        events.emit(events.web_chat_relay_turn_filtered(direction="grok_to_claude"))
        return

    body = (
        f"{cfg.claude_opener}\n\n---\n{GROK_PREFIX} {grok_text}\n---"
        if cfg.claude_opener
        else f"{GROK_PREFIX} {grok_text}"
    )
    result = await asyncio.to_thread(
        claude_leg.ask_and_wait, prompt_text=body, base_url=cfg.project_ask_url
    )
    claude_body = str(result.get("body") or "")
    state.last_to_claude = grok_sha
    state.relays += 1
    _write_state(cfg, claude_chat_url=str(result.get("url") or ""), relays=state.relays)
    events.emit(
        events.web_chat_relay_turn_relayed(
            direction="grok_to_claude", body_sha256=grok_sha, relay_index=state.relays
        )
    )

    claude_sha = body_sha(claude_body)
    if not claude_body or claude_sha == state.last_to_grok:
        return
    if has_prefix(claude_body, GROK_PREFIX):
        # Claude quoting Grok's own words back -- not new content for grok.
        state.last_to_grok = claude_sha
        events.emit(events.web_chat_relay_turn_filtered(direction="claude_to_grok"))
        return
    await grok_session.paste_and_send(grok_page, f"{CLAUDE_PREFIX} {claude_body}")
    await grok_session.wait_idle(grok_page)
    state.last_to_grok = claude_sha
    state.relays += 1
    events.emit(
        events.web_chat_relay_turn_relayed(
            direction="claude_to_grok", body_sha256=claude_sha, relay_index=state.relays
        )
    )
