"""JSON Schema validation for response_format parameter.

Validates that client-provided JSON schemas follow the JSON Schema specification
to prevent silent failures and degraded model responses.
"""

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

# Valid JSON Schema types per specification
VALID_JSON_SCHEMA_TYPES = {
    "null",
    "boolean",
    "object",
    "array",
    "number",
    "string",
    "integer",
}

# Common mistakes users make
TYPE_CORRECTIONS = {
    "int": "integer",
    "bool": "boolean",
    "str": "string",
    "float": "number",
    "list": "array",
    "dict": "object",
}


class SchemaValidationError(Exception):
    """Raised when JSON schema validation fails."""

    def __init__(self, message: str, param: str, suggested_fix: str | None = None):
        self.message = message
        self.param = param
        self.suggested_fix = suggested_fix
        super().__init__(message)


def validate_json_schema(schema: dict[str, Any], path: str = "schema") -> None:
    """Validate a JSON Schema recursively.

    Args:
        schema: The JSON schema to validate
        path: Current path in the schema (for error messages)

    Raises:
        SchemaValidationError: If schema is invalid
    """
    if not isinstance(schema, dict):
        return

    # Check "type" field if present
    if "type" in schema:
        type_value = schema["type"]

        # Handle array of types (e.g., ["integer", "null"])
        if isinstance(type_value, list):
            for i, t in enumerate(type_value):
                if not isinstance(t, str):
                    raise SchemaValidationError(
                        f"Type must be a string, got {type(t).__name__}",
                        param=f"{path}.type[{i}]",
                    )
                if t not in VALID_JSON_SCHEMA_TYPES:
                    suggestion = TYPE_CORRECTIONS.get(t)
                    raise SchemaValidationError(
                        f"'{t}' is not a valid JSON Schema type. "
                        f"Valid types: {', '.join(sorted(VALID_JSON_SCHEMA_TYPES))}",
                        param=f"{path}.type[{i}]",
                        suggested_fix=f'Use "{suggestion}"' if suggestion else None,
                    )
        # Handle single type string
        elif isinstance(type_value, str):
            if type_value not in VALID_JSON_SCHEMA_TYPES:
                suggestion = TYPE_CORRECTIONS.get(type_value)
                raise SchemaValidationError(
                    f"'{type_value}' is not a valid JSON Schema type. "
                    f"Valid types: {', '.join(sorted(VALID_JSON_SCHEMA_TYPES))}",
                    param=f"{path}.type",
                    suggested_fix=f'Use "{suggestion}"' if suggestion else None,
                )
        else:
            raise SchemaValidationError(
                f"Type must be a string or array of strings, got {type(type_value).__name__}",
                param=f"{path}.type",
            )

    # Recursively validate properties
    if "properties" in schema and isinstance(schema["properties"], dict):
        for prop_name, prop_schema in schema["properties"].items():
            if isinstance(prop_schema, dict):
                validate_json_schema(prop_schema, f"{path}.properties.{prop_name}")

    # Recursively validate items (for arrays)
    if "items" in schema and isinstance(schema["items"], dict):
        validate_json_schema(schema["items"], f"{path}.items")

    # Recursively validate additionalProperties
    if "additionalProperties" in schema and isinstance(
        schema["additionalProperties"], dict
    ):
        validate_json_schema(
            schema["additionalProperties"], f"{path}.additionalProperties"
        )


def validate_response_format(response_format: dict[str, Any]) -> None:
    """Validate response_format parameter.

    Args:
        response_format: The response_format dict from request

    Raises:
        SchemaValidationError: If response_format is invalid
    """
    if not isinstance(response_format, dict):
        raise SchemaValidationError(
            "response_format must be an object",
            param="response_format",
        )

    # Validate type field
    if "type" not in response_format:
        raise SchemaValidationError(
            "response_format.type is required",
            param="response_format.type",
        )

    format_type = response_format.get("type")
    if format_type not in ("json_object", "json_schema", "text"):
        raise SchemaValidationError(
            f"response_format.type must be 'json_object', 'json_schema', or 'text', got '{format_type}'",
            param="response_format.type",
        )

    # Validate schema if present
    if "schema" in response_format:
        schema = response_format["schema"]
        if not isinstance(schema, dict):
            raise SchemaValidationError(
                "response_format.schema must be an object",
                param="response_format.schema",
            )

        try:
            validate_json_schema(schema, "response_format.schema")
        except SchemaValidationError:
            # Re-raise with context
            raise

        logger.debug(f"✅ JSON Schema validation passed for {format_type}")
