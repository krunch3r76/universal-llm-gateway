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
    "cursor": {**_FULL_CAPACITY, "self_entity_id": "role:cursor-claude"},
    "web": {**_FULL_CAPACITY, "self_entity_id": "role:web-claude"},
    "api": {**_FULL_CAPACITY, "self_entity_id": "role:api-claude"},
    "api_claude": {**_FULL_CAPACITY, "self_entity_id": "role:api-claude"},
    "oppie": {**_FULL_CAPACITY, "self_entity_id": "role:oppie"},
    "orion": {**_FULL_CAPACITY, "self_entity_id": "role:orion"},
    "bard": {**_FULL_CAPACITY, "self_entity_id": "role:bard"},
    "forge": {**_FULL_CAPACITY, "self_entity_id": "role:forge"},
    "superheavy": {**_FULL_CAPACITY, "self_entity_id": "role:superheavy"},
    "web-grok": {**_FULL_CAPACITY, "self_entity_id": "role:web-grok"},
    "subagent": {**_FULL_CAPACITY},
}
