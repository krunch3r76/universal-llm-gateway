"""Image generation request validation."""

from fastapi import HTTPException


def parse_and_validate_size(size: str) -> tuple[int, int]:
    """
    Parse and validate image size string.

    FLUX.2 supports up to 4MP resolution.

    Args:
        size: Size string in format "WIDTHxHEIGHT" (e.g., "1024x1024")

    Returns:
        Tuple of (width, height) in pixels

    Raises:
        HTTPException: If size format is invalid or dimensions not supported
    """
    # Parse size
    try:
        width_str, height_str = size.split("x")
        width = int(width_str)
        height = int(height_str)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid size format: {size}. "
                "Expected format: WIDTHxHEIGHT (e.g., 1024x1024)"
            ),
        )

    # Validate dimensions (FLUX.2 expanded sizes)
    valid_sizes = {512, 768, 1024, 1280, 1536, 2048}
    if width not in valid_sizes or height not in valid_sizes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid size: {size}. "
                f"Width and height must be one of: {sorted(valid_sizes)}"
            ),
        )

    return width, height
