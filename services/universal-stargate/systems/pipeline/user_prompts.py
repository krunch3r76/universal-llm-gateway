"""
User prompts directory loading.

Enables clients to deploy custom prompts via simple file copying,
without requiring modification of pipeline_prompts*.yaml files.

Pattern mirrors user_handlers.py for consistency.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from universal_hot_reload import read_text_preserving_timestamps
from universal_logging import get_logger

logger = get_logger(__name__)


def load_user_prompts(
    prompts_dir_config: str,
    config_base_dir: Path | None = None,
) -> dict[str, dict]:
    """
    Load custom prompts from configured directory.

    Discovers structured prompt YAML files from a directory tree:

    prompts/
      consensus/
        statement_generation.yaml
        statement_verification.yaml
      translation/
        formal.yaml

    Prompts are namespaced by directory: consensus.statement_generation

    Resolves user_prompts_dir from config:
    - Relative paths → relative to config_base_dir
      (default: ~/.local/share/universal-stargate/)
    - Absolute paths → used as-is
    - Default: prompts/ if not configured

    Args:
        prompts_dir_config: Path from config (can be relative or absolute)
        config_base_dir: Base directory for resolving relative paths
                        (default: ~/.local/share/universal-stargate/)

    Returns:
        Dictionary of loaded prompts (namespaced by directory)

    Invariants:
    - ∀ prompt.yaml: must have 'template' field (system_prompt, json_schema optional)
    - Files starting with '_' are skipped (e.g., _template.yaml, _notes.yaml)
    - Directory structure defines namespace (e.g., consensus/gen.yaml → consensus.gen)
    """
    # Default config base directory
    if config_base_dir is None:
        config_base_dir = Path.home() / ".local/share/universal-stargate"

    # Resolve prompts directory
    prompts_path = Path(prompts_dir_config)
    if not prompts_path.is_absolute():
        prompts_dir = config_base_dir / prompts_path
    else:
        prompts_dir = prompts_path

    if not prompts_dir.exists():
        logger.debug(f"User prompts directory not found: {prompts_dir}")
        return {}

    if not prompts_dir.is_dir():
        logger.warning(f"User prompts path is not a directory: {prompts_dir}")
        return {}

    # Discover and load prompt YAML files
    loaded_prompts = {}
    prompt_count = 0

    for prompt_file in prompts_dir.rglob("*.yaml"):
        # Skip private/special files
        if prompt_file.name.startswith("_"):
            continue

        # Calculate namespace from directory structure
        # prompts/consensus/generation.yaml → consensus.generation
        relative_path = prompt_file.relative_to(prompts_dir)
        namespace_parts = list(relative_path.parent.parts) + [relative_path.stem]
        prompt_ref = ".".join(namespace_parts)

        try:
            # Read without triggering editor change notifications
            content = read_text_preserving_timestamps(prompt_file)
            prompt_data = yaml.safe_load(content) or {}

            # Validate required fields
            if "template" not in prompt_data:
                logger.warning(
                    f"Prompt '{prompt_ref}' missing required 'template' field, skipping"
                )
                continue

            # Store with namespace
            # Navigate nested dict structure to place prompt
            current = loaded_prompts
            for part in namespace_parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]

            current[namespace_parts[-1]] = prompt_data
            prompt_count += 1
            logger.debug(f"Loaded user prompt: {prompt_ref}")

        except Exception as e:
            logger.warning(
                f"Failed to load prompt from {prompt_file}: {e}", exc_info=True
            )

    if prompt_count:
        logger.info(f"✅ Loaded {prompt_count} user prompt(s) from {prompts_dir}")
    else:
        logger.debug(f"No user prompts found in {prompts_dir}")

    return loaded_prompts


def get_prompts_directory_path(
    prompts_dir_config: str,
    config_base_dir: Path | None = None,
) -> Path:
    """
    Resolve prompts directory path from config.

    Utility for testing and diagnostics.

    Args:
        prompts_dir_config: Path from config (relative or absolute)
        config_base_dir: Base directory for relative paths

    Returns:
        Resolved absolute path
    """
    if config_base_dir is None:
        config_base_dir = Path.home() / ".local/share/universal-stargate"

    prompts_path = Path(prompts_dir_config)
    if not prompts_path.is_absolute():
        return config_base_dir / prompts_path
    return prompts_path
