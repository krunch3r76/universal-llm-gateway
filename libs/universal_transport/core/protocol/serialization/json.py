"""
JSON serialization implementation.

Provides human-readable serialization with wide compatibility.
"""

import json
from typing import Any

from .base import DeserializeError, SerializeError, Serializer


class JSONSerializer(Serializer):
    """
    JSON serialization (UTF-8 encoded).

    Provides human-readable serialization with wide compatibility.
    Compatible with process_ipc JSON format.

    Features:
    - Human-readable output
    - Wide language support
    - UTF-8 encoding
    - Compact output (no whitespace)
    - process_ipc compatibility

    Attributes:
        ensure_ascii: Whether to escape non-ASCII characters
        sort_keys: Whether to sort dictionary keys for deterministic output
    """

    def __init__(self, ensure_ascii: bool = False, sort_keys: bool = False):
        """
        Initialize JSON serializer.

        Args:
            ensure_ascii: Escape non-ASCII characters (default False for smaller output)
            sort_keys: Sort dictionary keys for deterministic output
        """
        super().__init__("JSON", "application/json")
        self.ensure_ascii = ensure_ascii
        self.sort_keys = sort_keys

    def serialize(self, data: Any) -> bytes:
        """Serialize data to UTF-8 encoded JSON."""
        try:
            json_str = json.dumps(
                data,
                ensure_ascii=self.ensure_ascii,
                sort_keys=self.sort_keys,
                separators=(",", ":"),  # Compact format
            )
            return json_str.encode("utf-8")
        except (TypeError, ValueError) as e:
            raise SerializeError(f"Failed to serialize data as JSON: {e}")
        except Exception as e:
            raise SerializeError(f"Unexpected error during JSON serialization: {e}")

    def deserialize(self, data: bytes) -> Any:
        """Deserialize UTF-8 encoded JSON to Python object."""
        try:
            json_str = data.decode("utf-8")
            return json.loads(json_str)
        except UnicodeDecodeError as e:
            raise DeserializeError(f"Failed to decode bytes as UTF-8: {e}")
        except json.JSONDecodeError as e:
            raise DeserializeError(f"Failed to parse JSON: {e}")
        except Exception as e:
            raise DeserializeError(f"Unexpected error during JSON deserialization: {e}")
