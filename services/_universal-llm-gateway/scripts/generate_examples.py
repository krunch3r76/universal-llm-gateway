#!/usr/bin/env python3
"""
Generate YAML configuration examples from Pydantic schemas

This script generates annotated YAML examples by introspecting Pydantic schemas
(the source of truth). Examples include explicit type annotations for all fields.
"""

import argparse
import inspect
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Union, get_args, get_origin

import yaml

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from pydantic import BaseModel, ValidationError  # noqa: E402
from pydantic.fields import FieldInfo  # noqa: E402
from pydantic_core import PydanticUndefined  # noqa: E402

from src.schemas.yaml_config import GGUFModelConfig, HFModelConfig  # noqa: E402

# Import ruamel.yaml for comment preservation
try:
    from ruamel.yaml import YAML as RuamelYAML

    RUAMEL_AVAILABLE = True
except ImportError:
    RUAMEL_AVAILABLE = False


class SchemaIntrospector:
    """Extract type information from Pydantic schemas"""

    def get_type_annotation(self, field_info: FieldInfo) -> str:
        """
        Generate explicit type annotation string

        Examples:
        - str → "string"
        - Optional[int] → "int | null"
        - List[str] → "array[string]"
        - Literal["a", "b"] → "string (one of: 'a', 'b')"
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
        """Map Python type to YAML type string"""
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
        """Generate appropriate example value"""
        # Use default if available (but not PydanticUndefined or Ellipsis)
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


class SchemaExampleGenerator:
    """Generate examples from Pydantic schemas"""

    def __init__(self):
        self.introspector = SchemaIntrospector()
        self.schemas = {"gguf": GGUFModelConfig, "hf": HFModelConfig}

    def generate_example(
        self, format_type: str, redact_paths: bool = True, timestamp: bool = False
    ) -> str:
        """Generate complete example for format"""

        schema = self.schemas[format_type]

        # Generate header
        header = self._generate_header(format_type, timestamp)

        # Generate resource_management section
        resource_section = self._generate_resource_management()

        # Generate models section
        models_section = self._generate_models_section(
            schema, format_type, redact_paths
        )

        return f"{header}\n{resource_section}\n\n{models_section}"

    def _generate_header(self, format_type: str, timestamp: bool) -> str:
        """Generate example header"""
        ts = f"\n# Generated: {datetime.now().isoformat()}" if timestamp else ""

        return f"""# Universal LLM Gateway - {format_type.upper()} Model Configuration Example
# Source of Truth: src/schemas/yaml_config.py::{format_type.upper()}ModelConfig{ts}
# 
# This example is GENERATED from Pydantic schemas to ensure accuracy.
# All fields are shown with explicit type annotations.
#
# Type Notation:
# - type       : Required field
# - type | null: Optional field (can be null)
#
# Usage:
# 1. Copy this structure to model_loaders.yaml under models:
# 2. Replace example values with your model's actual values
# 3. Ensure types match exactly (string, int, boolean, null, etc.)
# 4. All optional fields shown for completeness (can be null)"""

    def _generate_resource_management(self) -> str:
        """Generate resource_management section"""
        return """resource_management:
  max_concurrent_models: 100  # int - Maximum concurrent models"""

    def _generate_models_section(
        self, schema, format_type: str, redact_paths: bool
    ) -> str:
        """Generate models section with full field coverage"""
        lines = ["models:", "  example-model-id:"]

        # Generate each top-level section
        for field_name, field_info in schema.model_fields.items():
            section_lines = self._generate_section(
                field_name, field_info, indent=2, redact_paths=redact_paths
            )
            lines.extend(section_lines)

        return "\n".join(lines)

    def _generate_section(
        self, name: str, field_info: FieldInfo, indent: int, redact_paths: bool
    ) -> list[str]:
        """Generate a configuration section recursively"""
        lines = []
        ind = "  " * indent

        # Get nested schema if this is a BaseModel
        annotation = field_info.annotation
        origin = get_origin(annotation)

        # Handle Union (strip None)
        if origin is Union:
            args = [a for a in get_args(annotation) if a is not type(None)]
            annotation = args[0] if args else annotation

        # Check if BaseModel
        if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
            lines.append(f"{ind}{name}:")

            for sub_name, sub_info in annotation.model_fields.items():
                sub_lines = self._generate_section(
                    sub_name, sub_info, indent + 1, redact_paths
                )
                lines.extend(sub_lines)

        # Handle Dict (profiles, etc.)
        elif origin is dict:
            lines.append(f"{ind}{name}:")
            if name == "profiles":
                # Generate example profile
                lines.extend(self._generate_profile_example(indent + 1))

        # Scalar field
        else:
            value = self.introspector.get_example_value(field_info, name)
            if name == "path" and redact_paths:
                value = self._redact_path(value)

            yaml_value = self._to_yaml_value(value)
            type_ann = self.introspector.get_type_annotation(field_info)
            desc = field_info.description or ""
            comment = f"# {type_ann} - {desc}" if desc else f"# {type_ann}"

            lines.append(f"{ind}{name}: {yaml_value}  {comment}")

        return lines

    def _to_yaml_value(self, value: Any) -> str:
        """Convert value to YAML string"""
        if value is None:
            return "null"
        elif isinstance(value, bool):
            return "true" if value else "false"
        elif isinstance(value, str):
            return f'"{value}"'
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, list):
            if not value:
                return "[]"
            return yaml.safe_dump(value, default_flow_style=True).strip()
        else:
            return yaml.safe_dump(value).strip()

    def _redact_path(self, path: str) -> str:
        """Redact absolute paths"""
        if "/mnt/" in path or path.startswith("/") or path.startswith("~"):
            return path.replace("/mnt/torus/models/", "/path/to/models/").replace(
                "~/.models/", "/path/to/models/"
            )
        return path

    def _generate_profile_example(self, indent: int) -> list[str]:
        """Generate example profile entries"""
        ind = "  " * indent
        return [
            f'{ind}"32768":  # string - Context length as key',
            f"{ind}  loader:",
            f"{ind}    n_ctx: 32768  # int - Context window length",
            f"{ind}    n_gpu_layers: -1  # int - GPU layers (-1 = all)",
            f"{ind}  resources:",
            f"{ind}    ram_mb: null  # int | null - RAM requirement in MB",
            f"{ind}    vram_mb: null  # int | null - VRAM requirement in MB",
            f"{ind}  default: true  # boolean - Whether this is the default profile",
        ]

    def refresh_config(
        self,
        config_path: str,
        output_path: str = None,
        dry_run: bool = False,
        add_comments: bool = False,
    ):
        """
        Refresh/normalize YAML config:
        - Reorder fields to match schema order
        - Add missing optional fields with null
        - Preserve existing values
        - Optionally add type comments
        """
        # Load existing config with regular PyYAML
        with open(config_path) as f:
            data = yaml.safe_load(f)

        # Process each model
        refreshed_models = OrderedDict()
        changes = []

        for model_id, model_config in data["models"].items():
            if not isinstance(model_config, dict):
                # Keep aliases as-is
                refreshed_models[model_id] = model_config
                continue

            format_type = model_config.get("info", {}).get("format")

            if format_type == "gguf":
                schema = GGUFModelConfig
            elif format_type == "hf":
                schema = HFModelConfig
            else:
                # Keep unsupported formats as-is
                refreshed_models[model_id] = model_config
                continue

            # Refresh model config in schema order
            original_keys = set(model_config.keys())
            refreshed = self._refresh_model_config(
                model_config, schema, add_comments=add_comments
            )
            new_keys = set(refreshed.keys())

            # Check if any changes were made
            if original_keys != new_keys or any(
                model_config.get(k) != refreshed.get(k) for k in original_keys
            ):
                changes.append(model_id)

            refreshed_models[model_id] = refreshed

        data["models"] = refreshed_models

        if dry_run:
            print(f"🔍 Dry run - found {len(changes)} models with changes:")
            for model_id in changes:
                print(f"  - {model_id}")

            if changes:
                print(f"\nFirst changed model ({changes[0]}) preview:")
                print("=" * 60)
                # Convert OrderedDict to regular dict for clean YAML output
                preview_data = self._convert_ordered_dict_to_dict(
                    {changes[0]: refreshed_models[changes[0]]}
                )
                print(
                    yaml.dump(preview_data, default_flow_style=False, sort_keys=False)
                )
                print("=" * 60)
                print("\nRun without --dry-run to apply changes")
            else:
                print("✅ No changes needed - config is already normalized")
        else:
            output = output_path or config_path
            # Convert OrderedDict to regular dict for clean YAML output
            clean_data = self._convert_ordered_dict_to_dict(data)
            with open(output, "w") as f:
                yaml.dump(
                    clean_data,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                )

            if changes:
                print(f"✅ Refreshed {len(changes)} models, written to: {output}")
            else:
                print(f"✅ Config already normalized, written to: {output}")

        return True

    def _refresh_model_config(
        self, model_config: dict, schema: type[BaseModel], add_comments: bool = False
    ) -> OrderedDict:
        """
        Refresh a single model config:
        - Reorder to match schema field order
        - Add missing optional fields
        - Preserve existing values
        """
        refreshed = OrderedDict()

        for field_name, field_info in schema.model_fields.items():
            if field_name in model_config:
                # Preserve existing value
                value = model_config[field_name]

                # Recursively refresh nested schemas
                annotation = self._get_field_annotation(field_info)
                if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
                    value = self._refresh_model_config(value, annotation, add_comments)

                refreshed[field_name] = value
            else:
                # Add missing field with default
                refreshed[field_name] = self._get_field_default(field_info)

        return refreshed

    def _get_field_annotation(self, field_info: FieldInfo):
        """Get the annotation, handling Union types"""
        annotation = field_info.annotation
        origin = get_origin(annotation)

        if origin is Union:
            # For Optional types, get the non-None type
            args = get_args(annotation)
            non_none = [a for a in args if a is not type(None)]
            return non_none[0] if non_none else annotation

        return annotation

    def _get_field_default(self, field_info: FieldInfo) -> Any:
        """Get appropriate default for a field"""
        if field_info.default is not PydanticUndefined:
            return field_info.default

        if (
            field_info.default_factory
            and field_info.default_factory is not PydanticUndefined
        ):
            try:
                return field_info.default_factory()
            except Exception:
                pass

        # Optional fields get None
        origin = get_origin(field_info.annotation)
        if origin is Union:
            return None

        # Required fields - should not happen if YAML is valid
        raise ValueError("Required field has no value and no default")

    def _convert_ordered_dict_to_dict(self, obj):
        """Recursively convert OrderedDict to regular dict for clean YAML output"""
        if isinstance(obj, OrderedDict):
            return {k: self._convert_ordered_dict_to_dict(v) for k, v in obj.items()}
        elif isinstance(obj, dict):
            return {k: self._convert_ordered_dict_to_dict(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_ordered_dict_to_dict(item) for item in obj]
        else:
            return obj


def validate_config_file(config_path: str):
    """Validate config file against schemas"""
    with open(config_path) as f:
        data = yaml.safe_load(f)

    models = data.get("models", {})
    errors = []

    for model_id, model_config in models.items():
        if not isinstance(model_config, dict):
            continue  # Skip aliases

        format_type = model_config.get("info", {}).get("format")

        try:
            if format_type == "gguf":
                GGUFModelConfig(**model_config)
            elif format_type == "hf":
                HFModelConfig(**model_config)
            print(f"✅ {model_id} ({format_type})")
        except ValidationError as e:
            errors.append(f"❌ {model_id}: {e}")

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Generate YAML examples from Pydantic schemas"
    )
    parser.add_argument(
        "--format",
        choices=["gguf", "hf"],
        default="gguf",
        help="Model format (default: gguf)",
    )
    parser.add_argument(
        "--file",
        "--output",
        metavar="PATH",
        help="Write to file (default: stdout only)",
    )
    parser.add_argument(
        "--no-redact",
        dest="redact_paths",
        action="store_false",
        default=True,
        help="Keep actual paths (default: redact)",
    )
    parser.add_argument(
        "--timestamp", action="store_true", help="Include generation timestamp"
    )
    parser.add_argument(
        "--validate-config",
        metavar="PATH",
        help="Validate a config file against schemas",
    )
    parser.add_argument(
        "--refresh",
        metavar="PATH",
        help="Refresh/normalize config file (backfill + reorder)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing (use with --refresh)",
    )
    parser.add_argument(
        "--with-comments",
        action="store_true",
        help="Add type annotation comments (use with --refresh)",
    )

    args = parser.parse_args()

    try:
        generator = SchemaExampleGenerator()

        # Refresh config mode
        if args.refresh:
            success = generator.refresh_config(
                args.refresh,
                output_path=args.file,
                dry_run=args.dry_run,
                add_comments=args.with_comments,
            )
            return 0 if success else 1

        # Validate config mode
        if args.validate_config:
            validate_config_file(args.validate_config)
            return 0

        # Generate example
        example = generator.generate_example(
            format_type=args.format,
            redact_paths=args.redact_paths,
            timestamp=args.timestamp,
        )

        # Always print to stdout (default behavior)
        print(example)

        # Optionally write to file
        if args.file:
            Path(args.file).parent.mkdir(parents=True, exist_ok=True)
            with open(args.file, "w") as f:
                f.write(example)
            print(f"\n✅ Also saved to: {args.file}", file=sys.stderr)

        return 0

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
