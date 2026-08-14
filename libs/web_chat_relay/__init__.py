"""Grok.com ↔ claude.ai Cowork session relay (v1 dogfood, not a CDP satellite)."""

from web_chat_relay.loop import RelayConfig, body_sha, should_relay

__all__ = ["RelayConfig", "body_sha", "should_relay"]
