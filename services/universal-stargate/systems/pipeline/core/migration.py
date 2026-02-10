"""
Schema migration for pipeline YAML files.

Version history:
- v4: inputs/outputs fields
- v5: handler_inputs/handler_outputs (this phase)
- v6: provenance_mode/originator_mapping (provenance tracking)
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SchemaMigrator:
    """Apply migrations from older schema versions."""

    CURRENT_VERSION = 6

    def migrate(self, pipeline_dict: dict[str, Any]) -> dict[str, Any]:
        """
        Migrate pipeline dict to current schema version.

        Modifies and returns the input dict.
        """
        version = pipeline_dict.get("schema_version", 1)

        if version > self.CURRENT_VERSION:
            raise ValueError(
                f"Unknown schema version: {version} (current: {self.CURRENT_VERSION})"
            )

        if version == self.CURRENT_VERSION:
            return pipeline_dict

        # Apply migrations sequentially
        for v in range(version, self.CURRENT_VERSION):
            migration_fn = getattr(self, f"_migrate_v{v}_to_v{v + 1}", None)
            if migration_fn:
                logger.info(f"Migrating pipeline from v{v} to v{v + 1}")
                pipeline_dict = migration_fn(pipeline_dict)

        pipeline_dict["schema_version"] = self.CURRENT_VERSION
        return pipeline_dict

    def _migrate_v4_to_v5(self, d: dict[str, Any]) -> dict[str, Any]:
        """
        v4→v5: Rename inputs→handler_inputs, outputs→handler_outputs.

        Also removes deprecated depends_on (now computed).
        """
        for step in d.get("steps", []):
            # Rename fields
            if "inputs" in step:
                step["handler_inputs"] = step.pop("inputs")
            if "outputs" in step:
                step["handler_outputs"] = step.pop("outputs")

            # Remove deprecated depends_on (now computed from handler_inputs)
            if "depends_on" in step:
                logger.warning(
                    f"Step '{step.get('name', '?')}': 'depends_on' removed in v5 "
                    f"(auto-computed from handler_inputs)"
                )
                step.pop("depends_on")

        return d

    def _migrate_v5_to_v6(self, d: dict[str, Any]) -> dict[str, Any]:
        """
        v5→v6: Add provenance tracking fields.

        Extracts originator_mapping from generation_parameters if present.
        Sets provenance_mode="aggregate" for steps with originator_mapping.
        New pipelines must explicitly declare provenance_mode.
        """
        for step in d.get("steps", []):
            gen_params = step.get("generation_parameters", {})

            # Extract originator_mapping if it was in generation_parameters
            if "originator_mapping" in gen_params:
                step["originator_mapping"] = gen_params.pop("originator_mapping")
                # If originator_mapping exists, this is an aggregation step
                step["provenance_mode"] = "aggregate"
                logger.info(
                    f"Step '{step.get('name', '?')}': migrated originator_mapping "
                    f"from generation_parameters to schema field"
                )

            # Clean up empty generation_parameters
            if not gen_params:
                step.pop("generation_parameters", None)

        return d
