"""Profile resource section validation for model loader YAML configurations."""

from typing import Any

from universal_logging import get_logger

from .types import ProfileIssue

logger = get_logger(__name__)


def validate_profile_resources(models_config: dict[str, Any]) -> list[ProfileIssue]:
    """Validate that all profiles have complete resource sections."""
    issues = []

    for model_id, model_data in models_config.items():
        if not isinstance(model_data, dict):
            continue

        for profile_type in ["profiles", "cpu_profiles"]:
            profiles = model_data.get(profile_type, {})

            for profile_key, profile_config in profiles.items():
                if not isinstance(profile_config, dict):
                    continue

                resources = profile_config.get("resources")

                loader_config = profile_config.get("loader", {})
                n_gpu_layers = profile_config.get("n_gpu_layers")
                if n_gpu_layers is None:
                    n_gpu_layers = loader_config.get("n_gpu_layers", -1)
                is_hybrid = n_gpu_layers > 0 and profile_type == "profiles"

                if resources is None:
                    issues.append(
                        ProfileIssue(
                            model_id=model_id,
                            profile_key=profile_key,
                            profile_type=profile_type,
                            issue="Missing 'resources' section",
                            impact=(
                                "Model eviction will fail - router cannot "
                                "calculate freeable resources"
                            ),
                            is_hybrid=is_hybrid,
                        )
                    )
                    continue

                is_cpu_profile = profile_type == "cpu_profiles"

                ram_mb = resources.get("ram_mb")
                vram_mb = resources.get("vram_mb")

                if ram_mb is None:
                    issues.append(
                        ProfileIssue(
                            model_id=model_id,
                            profile_key=profile_key,
                            profile_type=profile_type,
                            issue="Missing 'ram_mb' in resources section",
                            impact=(
                                "Resource tracking incomplete - may cause "
                                "incorrect capacity calculations"
                            ),
                            is_hybrid=is_hybrid,
                        )
                    )

                if vram_mb is None:
                    issues.append(
                        ProfileIssue(
                            model_id=model_id,
                            profile_key=profile_key,
                            profile_type=profile_type,
                            issue="Missing 'vram_mb' in resources section",
                            impact=(
                                "Resource tracking incomplete - may cause "
                                "incorrect capacity calculations"
                            ),
                            is_hybrid=is_hybrid,
                        )
                    )

                if is_cpu_profile and vram_mb is not None and vram_mb != 0:
                    issues.append(
                        ProfileIssue(
                            model_id=model_id,
                            profile_key=profile_key,
                            profile_type=profile_type,
                            issue=(f"CPU profile has vram_mb={vram_mb} (should be 0)"),
                            impact=(
                                "CPU model may be incorrectly considered for "
                                "GPU resource calculations"
                            ),
                            is_hybrid=is_hybrid,
                        )
                    )

    return issues


def log_profile_validation_results(issues: list[ProfileIssue]) -> None:
    """Log profile validation issues in a readable format."""
    if not issues:
        logger.info(
            "✅ Profile validation: All profiles have complete resource sections"
        )
        return

    logger.warning(f"⚠️  Found {len(issues)} incomplete profile configuration(s):")

    for issue in issues:
        synthetic_id = f"{issue.model_id}-{issue.profile_key}"
        if issue.profile_type == "cpu_profiles":
            synthetic_id += "-cpu"
        elif issue.is_hybrid:
            synthetic_id += "-hybrid"

        logger.warning(
            f"  • {synthetic_id}: {issue.issue}\n"
            f"    Impact: {issue.impact}\n"
            "    Location: models.%s.%s.%s",
            issue.model_id,
            issue.profile_type,
            issue.profile_key,
        )
