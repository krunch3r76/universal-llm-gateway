"""Grok.com ↔ claude.ai Cowork session relay (v1 dogfood, not a CDP satellite).

Intentionally exports nothing. Import submodules directly (``web_chat_relay.loop``,
``web_chat_relay.orion_wake``): the relay loop pulls in playwright, which the
edge/stargate container image does not ship, and Stargate imports
``web_chat_relay.orion_wake`` at startup.
"""
