"""Boot profile constants: full-capacity defaults and per-agent profiles."""

from __future__ import annotations

from typing import Any

_FULL_CAPACITY: dict[str, Any] = {
    "include_deadlines": True,
    "include_review_queue": True,
    "session_agent_filter": None,
    "session_limit": 3,
    "self_reflections_limit": 5,
}

_BOOT_PROFILES: dict[str, dict[str, Any]] = {
    "cursor": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:cursor-claude"},
    "web": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:web-claude"},
    "api": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:api-claude"},
    "api_claude": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:api-claude"},
    "oppie": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:oppie"},
    "orion": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:orion"},
    "bard": {**_FULL_CAPACITY, "self_entity_id": "ai_agent:bard"},
    "subagent": {**_FULL_CAPACITY},
}
