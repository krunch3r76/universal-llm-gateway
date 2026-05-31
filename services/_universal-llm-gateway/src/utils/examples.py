"""
Schema-driven example generation for model configurations.

Provides utilities to generate complete, validated configuration examples
from Pydantic schemas. Extracted from scripts/generate_examples.py for
library use in API endpoints and CLI tools.
"""

import inspect
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

try:
    from ..schemas.yaml_config import GGUFModelConfig, HFModelConfig
except ImportError:
    from src.schemas.yaml_config import GGUFModelConfig, HFModelConfig


class SchemaIntrospector:
    """Extract type information and metadata from Pydantic schemas"""

    def get_type_annotation(self, field_info: FieldInfo) -> str:
        """
        Generate explicit type annotation string for documentation.

        Args:
            field_info: Pydantic field information

        Returns:
            Human-readable type string (e.g., "string", "int | null", "array[string]")
        """
        annotation = field_info.annotation

        # Handle Optional/Union types
        origin = get_origin(annotation)
        if origin is Union:
            args = get_args(annotation)
            if type(None) in args:
                non_none = [a for a in args if a is not type(None)][0]
                base_type = self._get_base_type(non_none)
                return f"{base_type} | null"

        return self._get_base_type(annotation)

    def _get_base_type(self, annotation) -> str:
        """Map Python type annotation to YAML type string"""
        if annotation is str:
            return "string"
        elif annotation is int:
            return "int"
        elif annotation is float:
            return "float"
        elif annotation is bool:
            return "boolean"

        origin = get_origin(annotation)

        if origin is list:
            item_type = get_args(annotation)[0]
            return f"array[{self._get_base_type(item_type)}]"

        elif origin is dict:
            return "object"

        elif (
            hasattr(annotation, "__origin__")
            and str(annotation.__origin__) == "typing.Literal"
        ):
            # Handle Literal types
            try:
                values = get_args(annotation)
                quoted = [f"'{v}'" for v in values]
                return f"string (one of: {', '.join(quoted)})"
            except Exception:
                return "string"

        else:
            return "object"

    def get_example_value(self, field_info: FieldInfo, field_name: str) -> Any:
        """
        Generate appropriate example value for a field.

        Args:
            field_info: Pydantic field information
            field_name: Name of the field

        Returns:
            Example value appropriate for the field type
        """
        # Use default if available
        if field_info.default is not PydanticUndefined and field_info.default not in (
            None,
            ...,
            Ellipsis,
        ):
            return field_info.default

        # Handle factory defaults
        if (
            field_info.default_factory
            and field_info.default_factory is not PydanticUndefined
        ):
            try:
                return field_info.default_factory()
            except Exception:
                pass

        # Field-specific placeholders
        if field_name == "path":
            return "/path/to/model/file.gguf"
        elif field_name == "name":
            return "Example Model Name"
        elif field_name == "id":
            return "example-model-id"
        elif field_name == "family":
            return "llama"
        elif field_name == "arch":
            return "llama-3.1-8b"
        elif field_name == "format":
            # Get from annotation if Literal
            annotation = field_info.annotation
            try:
                values = get_args(annotation)
                if values:
                    return values[0]
            except Exception:
                pass

        # Type-based defaults
        annotation = field_info.annotation
        origin = get_origin(annotation)

        if origin is Union:
            # Optional - use None
            return None

        if annotation is str:
            return "example_value"
        elif annotation is int:
            return 0
        elif annotation is float:
            return 0.0
        elif annotation is bool:
            return True
        elif origin is list:
            return []
        elif origin is dict:
            return {}
        elif (
            hasattr(annotation, "__origin__")
            and str(annotation.__origin__) == "typing.Literal"
        ):
            # Use first literal value
            try:
                values = get_args(annotation)
                return values[0] if values else "value"
            except Exception:
                return "value"

        return None

    def get_field_annotation(self, field_info: FieldInfo):
        """
        Get the annotation type, handling Union types.

        For Optional[T], returns T. For other types, returns as-is.
        """
        annotation = field_info.annotation
        origin = get_origin(annotation)

        if origin is Union:
            # For Optional types, get the non-None type
            args = get_args(annotation)
            non_none = [a for a in args if a is not type(None)]
            return non_none[0] if non_none else annotation

        return annotation


class ExampleGenerator:
    """
    Generate complete configuration examples from Pydantic schemas.

    Produces schema-compliant example configurations for supported formats.
    """

    SUPPORTED_FORMATS = ["gguf", "hf", "gptq", "awq"]

    def __init__(self):
        self.introspector = SchemaIntrospector()
        self.schemas = {
            "gguf": GGUFModelConfig,
            "hf": HFModelConfig,
            "gptq": HFModelConfig,  # GPTQ uses same schema as HF
            "awq": HFModelConfig,  # AWQ uses same schema as HF
        }

    def get_example_dict(self, format_type: str) -> dict[str, Any]:
        """
        Generate example configuration as dictionary.

        Args:
            format_type: Model format ('gguf' or 'hf')

        Returns:
            Complete example configuration dictionary

        Raises:
            ValueError: If format_type is not supported
        """
        if format_type not in self.schemas:
            raise ValueError(
                f"Unsupported format '{format_type}'. "
                f"Supported formats: {', '.join(self.SUPPORTED_FORMATS)}"
            )

        schema = self.schemas[format_type]
        return self._generate_config_dict(schema)

    def _generate_config_dict(self, schema: type[BaseModel]) -> dict[str, Any]:
        """Generate configuration dictionary from schema"""
        config = {}

        for field_name, field_info in schema.model_fields.items():
            annotation = self.introspector.get_field_annotation(field_info)

            # Handle nested BaseModel
            if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
                config[field_name] = self._generate_config_dict(annotation)
            # Handle Dict (profiles, cpu_profiles, etc.)
            elif get_origin(annotation) is dict:
                if field_name == "profiles":
                    config[field_name] = self._generate_profile_example()
                elif field_name == "cpu_profiles":
                    config[field_name] = self._generate_cpu_profile_example()
                else:
                    config[field_name] = {}
            # Scalar field
            else:
                config[field_name] = self.introspector.get_example_value(
                    field_info, field_name
                )

        return config

    def _generate_profile_example(self) -> dict[str, Any]:
        """Generate example GGUF profile"""
        return {
            "32768": {
                "loader": {"n_ctx": 32768, "n_gpu_layers": -1},
                "resources": {"ram_mb": None, "vram_mb": None},
            }
        }

    def _generate_cpu_profile_example(self) -> dict[str, Any]:
        """Generate example GGUF CPU profile"""
        return {
            "8192": {
                "loader": {
                    "n_ctx": 8192,
                    "n_gpu_layers": 0,  # Always 0 for CPU
                },
                "resources": {"ram_mb": 8500, "vram_mb": 0},
            }
        }

    def get_schema_info(self, format_type: str) -> dict[str, Any]:
        """
        Get schema metadata for a format.

        Args:
            format_type: Model format ('gguf' or 'hf')

        Returns:
            Dictionary with schema_fields, required_fields, optional_fields
        """
        if format_type not in self.schemas:
            raise ValueError(f"Unsupported format: {format_type}")

        schema = self.schemas[format_type]
        all_fields = []
        required_fields = []
        optional_fields = []

        self._extract_field_info(schema, all_fields, required_fields, optional_fields)

        return {
            "schema_fields": all_fields,
            "required_fields": required_fields,
            "optional_fields": optional_fields,
        }

    def _extract_field_info(
        self,
        schema: type[BaseModel],
        all_fields: list[str],
        required_fields: list[str],
        optional_fields: list[str],
        prefix: str = "",
    ):
        """Recursively extract field information from schema"""
        for field_name, field_info in schema.model_fields.items():
            full_name = f"{prefix}.{field_name}" if prefix else field_name
            all_fields.append(full_name)

            # Check if required
            annotation = field_info.annotation
            origin = get_origin(annotation)
            is_optional = origin is Union and type(None) in get_args(annotation)

            if field_info.is_required() and not is_optional:
                required_fields.append(full_name)
            else:
                optional_fields.append(full_name)

            # Recurse into nested models
            nested_annotation = self.introspector.get_field_annotation(field_info)
            if inspect.isclass(nested_annotation) and issubclass(
                nested_annotation, BaseModel
            ):
                self._extract_field_info(
                    nested_annotation,
                    all_fields,
                    required_fields,
                    optional_fields,
                    prefix=full_name,
                )


def get_example_for_format(format_type: str) -> dict[str, Any]:
    """
    Convenience function to get example configuration.

    Args:
        format_type: Model format ('gguf' or 'hf')

    Returns:
        Complete example configuration dictionary
    """
    generator = ExampleGenerator()
    return generator.get_example_dict(format_type)


def get_schema_info(format_type: str) -> dict[str, Any]:
    """
    Convenience function to get schema metadata.

    Args:
        format_type: Model format ('gguf' or 'hf')

    Returns:
        Dictionary with schema_fields, required_fields, optional_fields
    """
    generator = ExampleGenerator()
    return generator.get_schema_info(format_type)
