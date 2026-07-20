"""Model file existence and readability validation for enabled catalog entries."""

from pathlib import Path
from typing import Any

from ....schemas.model_info import ModelValidationReport, ModelValidationResult


def validate_model_files(
    models: dict[str, Any], fast_mode: bool = True
) -> ModelValidationReport:
    """Validate that model files exist and are accessible."""
    results = []
    valid_count = 0

    for model_id, metadata in models.items():
        if not metadata.enabled:
            continue

        result = validate_single_model(model_id, metadata, fast_mode)
        results.append(result)

        if result.exists and result.readable:
            valid_count += 1

    return ModelValidationReport(
        total_models=len([m for m in models.values() if m.enabled]),
        valid_models=valid_count,
        results=results,
    )


def validate_single_model(
    model_id: str, metadata: Any, fast_mode: bool = True
) -> ModelValidationResult:
    """Validate a single model file or directory - optimized for fast startup."""
    path = Path(metadata.path)

    try:
        exists = path.exists()
        readable = False
        size_mb = None
        error = None

        if exists:
            try:
                if path.is_dir():
                    config_file = path / "config.json"
                    model_index_file = path / "model_index.json"

                    if not config_file.exists() and not model_index_file.exists():
                        error = (
                            "Directory missing config.json or model_index.json "
                            "(invalid model directory)"
                        )
                        readable = False
                    else:
                        readable = True
                        if not fast_mode:
                            total_size = sum(
                                f.stat().st_size for f in path.rglob("*") if f.is_file()
                            )
                            size_mb = total_size / (1024 * 1024)
                else:
                    with open(path, "rb") as f:
                        f.read(1)
                    readable = True

                    if not fast_mode:
                        size_mb = path.stat().st_size / (1024 * 1024)

                if readable and hasattr(metadata, "loader_config"):
                    loader_config = metadata.loader_config
                    clip_path = loader_config.get("clip_model_path")
                    if clip_path:
                        clip_file = Path(clip_path)
                        if not clip_file.exists():
                            error = f"Vision model CLIP file not found: {clip_path}"
                            readable = False
                        elif not clip_file.is_file():
                            error = f"Vision model CLIP path is not a file: {clip_path}"
                            readable = False

            except PermissionError:
                error = "Permission denied"
            except Exception as e:
                error = f"Cannot read file: {e}"
        else:
            error = "File does not exist"

        return ModelValidationResult(
            model_id=model_id,
            exists=exists,
            path=str(path),
            size_mb=size_mb,
            readable=readable,
            error=error,
        )

    except Exception as e:
        return ModelValidationResult(
            model_id=model_id,
            exists=False,
            path=str(path),
            size_mb=None,
            readable=False,
            error=f"Validation error: {e}",
        )
