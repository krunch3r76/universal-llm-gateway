"""Poll both product tabs and relay completed assistant turns.

Baseline-then-delta: history is never dumped. Echo is broken by comparing
sha256 of the last pasted body in each direction.
"""

from __future__ import annotations

import asyncio
import hashlib
import signal
from dataclasses import dataclass
from pathlib import Path

from web_chat_relay import claude_leg, events, grok_session
from web_chat_relay.claude_leg import ClaudeSession
from web_chat_relay.grok_session import GrokAuthError


def body_sha(text: str) -> str:
    """Stable fingerprint of a harvested assistant body."""
    return hashlib.sha256((text or "").encode()).hexdigest()


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
    stop_file: Path = Path("/tmp/grok-claude-relay.stop")
    url_substr: str = grok_session.DEFAULT_GROK_CHAT_ID


@dataclass
class RelayState:
    grok_baseline_sha: str = ""
    claude_baseline_sha: str = ""
    last_to_claude: str | None = None
    last_to_grok: str | None = None
    relays: int = 0
    stop_reason: str | None = None
    claude: ClaudeSession | None = None


def _stop_requested(cfg: RelayConfig, state: RelayState) -> str | None:
    if state.stop_reason:
        return state.stop_reason
    if cfg.stop_file.exists():
        return "stop_file"
    if cfg.max_relays is not None and state.relays >= cfg.max_relays:
        return "max_relays"
    return None


async def run_relay(cfg: RelayConfig) -> RelayState:
    """Attach grok, open retained Cowork, then poll/relay until stop."""
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

        state.claude = claude_leg.open_retained_session(
            grok_url=cfg.grok_url, base_url=cfg.project_ask_url
        )
        state.claude_baseline_sha = body_sha(state.claude.baseline_body)
        events.emit(
            events.web_chat_relay_started(
                grok_url=cfg.grok_url,
                claude_chat_url=state.claude.chat_url,
            )
        )
        if cfg.seed_grok.strip():
            await grok_session.paste_and_send(grok_page, cfg.seed_grok.strip())
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
    assert state.claude is not None
    claude = state.claude
    while True:
        reason = _stop_requested(cfg, state)
        if reason:
            state.stop_reason = reason
            return

        grok = await grok_session.harvest(grok_page)
        if grok.login_wall:
            events.emit(
                events.web_chat_relay_auth_missing(
                    grok_url=cfg.grok_url, page_url=grok.url
                )
            )
            state.stop_reason = "auth_missing"
            return
        if grok.streaming or grok.stop:
            grok = await grok_session.wait_idle(grok_page)
        grok_sha = body_sha(grok.last_assistant)
        if should_relay(
            new_sha=grok_sha,
            baseline_sha=state.grok_baseline_sha,
            last_sent_sha=state.last_to_grok,
            last_received_sha=state.last_to_claude,
        ):
            if not claude.chat_url:
                raise RuntimeError("Cowork chat_url missing after submit")
            claude_leg.followup_paste(
                prompt_text=grok.last_assistant,
                chat_url=claude.chat_url,
                base_url=cfg.project_ask_url,
                registration_id=claude.registration_id,
                execution_id=claude.execution_id,
            )
            state.last_to_claude = grok_sha
            state.relays += 1
            events.emit(
                events.web_chat_relay_turn_relayed(
                    direction="grok_to_claude",
                    body_sha256=grok_sha,
                    relay_index=state.relays,
                )
            )
            if claude.cdp_url and claude.chat_url:
                reply = await claude_leg.wait_next_assistant(
                    cdp_url=claude.cdp_url, chat_url=claude.chat_url
                )
                claude_body = str(reply.get("body") or "")
                claude_sha = body_sha(claude_body)
                if should_relay(
                    new_sha=claude_sha,
                    baseline_sha=state.claude_baseline_sha,
                    last_sent_sha=state.last_to_claude,
                    last_received_sha=state.last_to_grok,
                ):
                    await grok_session.paste_and_send(grok_page, claude_body)
                    await grok_session.wait_idle(grok_page)
                    state.last_to_grok = claude_sha
                    state.relays += 1
                    events.emit(
                        events.web_chat_relay_turn_relayed(
                            direction="claude_to_grok",
                            body_sha256=claude_sha,
                            relay_index=state.relays,
                        )
                    )

        await asyncio.sleep(cfg.poll_s)
