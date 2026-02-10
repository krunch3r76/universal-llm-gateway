"""
Schema Registry - Central registry for engine schemas.

Usage:
    from core.catalog.schemas import SchemaRegistry

    schema = SchemaRegistry.get_for_model(entry)
    if schema:
        issues = schema.validate(model_id, entry)
        converted = schema.convert(model_id, entry)

Invariants:
    ∀ schema S: |{S' | S'.registry_key = S.registry_key}| ≤ 1  (unique key)
    ∀ format F: |{S | F ∈ S.formats}| = 1  (unique schema per format)

Registry key: schema_name if set, else engine (decouples YAML schema: name
from engine dispatch type, e.g., schema_name="llama-cpp" engine="native").

V2 Schema Enforcement (Phase 3):
    - V2 catalog entries MUST have 'schema' field (NO derivation from format)
    - get_for_model() returns None if schema missing → caller must error
    - NO fallback to format (removed in Phase 3)
"""

from typing import Any

from .ctranslate2 import CTranslate2Schema
from .diffusers import DiffusersSchema
from .exllamav3 import ExllamaV3Schema
from .faster_whisper import FasterWhisperSchema
from .llama_cpp import LlamaCppSchema
from .schema import BaseEngineSchema
from .types import ConvertedModel, ValidationIssue
from .vllm import VllmSchema

__all__ = [
    "BaseEngineSchema",
    "ConvertedModel",
    "ValidationIssue",
    "SchemaRegistry",
    "LlamaCppSchema",
    "VllmSchema",
    "ExllamaV3Schema",
    "FasterWhisperSchema",
    "DiffusersSchema",
    "CTranslate2Schema",
]


class SchemaRegistry:
    """
    Registry for engine schemas.

    Provides lookup by engine name or model format.
    """

    _schemas: dict[str, BaseEngineSchema] = {}
    _format_to_schema: dict[str, BaseEngineSchema] = {}
    _initialized: bool = False

    @classmethod
    def _ensure_initialized(cls) -> None:
        """
        Lazy initialization of schema registry.

        Raises:
            ValueError: If duplicate engine or format detected
        """
        if cls._initialized:
            return

        cls.register(LlamaCppSchema())
        cls.register(VllmSchema())
        cls.register(ExllamaV3Schema())
        cls.register(FasterWhisperSchema())
        cls.register(DiffusersSchema())
        cls.register(CTranslate2Schema())
        cls._initialized = True

    @classmethod
    def register(cls, schema: BaseEngineSchema) -> None:
        """
        Register a schema instance.

        Registry key: schema_name if set, else engine.
        This decouples YAML schema: name from engine dispatch type.

        Args:
            schema: Schema instance to register

        Raises:
            ValueError: If registry key or format already registered
        """
        key = schema.schema_name or schema.engine
        if key in cls._schemas:
            raise ValueError(f"Schema conflict: key '{key}' already registered")

        cls._schemas[key] = schema

        for fmt in schema.formats:
            if fmt in cls._format_to_schema:
                existing_key = (
                    cls._format_to_schema[fmt].schema_name
                    or cls._format_to_schema[fmt].engine
                )
                raise ValueError(
                    f"Schema conflict: format '{fmt}' already registered "
                    f"by '{existing_key}' (attempted by '{key}')"
                )
            cls._format_to_schema[fmt] = schema

    @classmethod
    def get_by_engine(cls, engine: str) -> BaseEngineSchema | None:
        """
        Get schema by registry key (schema_name or engine).

        Args:
            engine: Schema identifier (e.g., 'llama-cpp', 'vllm')
                Uses schema_name when set, else engine.

        Returns:
            Schema instance or None if not found
        """
        cls._ensure_initialized()
        return cls._schemas.get(engine)

    @classmethod
    def get_by_format(cls, format_name: str) -> BaseEngineSchema | None:
        """
        Get schema by model format.

        Args:
            format_name: Format identifier (e.g., 'gguf', 'awq')

        Returns:
            Schema instance or None if not found
        """
        cls._ensure_initialized()
        return cls._format_to_schema.get(format_name)

    @classmethod
    def get_for_model(cls, entry: dict[str, Any]) -> BaseEngineSchema | None:
        """
        Get appropriate schema for a model entry.

        V2 Resolution (STRICT):
            - entry.schema (explicit schema reference) - REQUIRED
            - NO fallback to format (removed in Phase 3)

        Args:
            entry: Catalog model entry dict

        Returns:
            Schema instance or None if schema field missing/unknown

        Raises:
            None - returns None for missing/unknown schemas (caller handles error)
        """
        cls._ensure_initialized()

        # V2: Explicit schema reference (REQUIRED - no fallback)
        schema_name = entry.get("schema")
        if not schema_name:
            return None  # Caller must handle missing schema

        return cls.get_by_engine(schema_name)

    @classmethod
    def is_registered_format(cls, format_name: str) -> bool:
        """
        Check if a format has a registered schema.

        Args:
            format_name: Format identifier

        Returns:
            True if format has a schema
        """
        cls._ensure_initialized()
        return format_name in cls._format_to_schema

    @classmethod
    def all_schemas(cls) -> list[BaseEngineSchema]:
        """Get all registered schemas."""
        cls._ensure_initialized()
        return list(cls._schemas.values())

    @classmethod
    def all_engines(cls) -> list[str]:
        """Get all registered engine names."""
        cls._ensure_initialized()
        return list(cls._schemas.keys())

    @classmethod
    def all_formats(cls) -> list[str]:
        """Get all registered format names."""
        cls._ensure_initialized()
        return list(cls._format_to_schema.keys())
