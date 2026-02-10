"""
Pure validation helpers for configuration classes.

Provides reusable validation functions to reduce code duplication
and ensure consistent validation logic across configuration classes.
"""


def validate_positive(value: float | int, name: str) -> None:
    """
    Validate that a value is positive.

    Args:
        value: Value to validate
        name: Name of the field for error messages

    Raises:
        ValueError: If value is not positive
    """
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


def validate_non_negative(value: float | int, name: str) -> None:
    """
    Validate that a value is non-negative.

    Args:
        value: Value to validate
        name: Name of the field for error messages

    Raises:
        ValueError: If value is negative
    """
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")


def validate_minimum(value: float | int, minimum: float | int, name: str) -> None:
    """
    Validate that a value meets a minimum threshold.

    Args:
        value: Value to validate
        minimum: Minimum allowed value
        name: Name of the field for error messages

    Raises:
        ValueError: If value is below minimum
    """
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")


def validate_string_not_empty(value: str, name: str) -> None:
    """
    Validate that a string is not empty.

    Args:
        value: String to validate
        name: Name of the field for error messages

    Raises:
        ValueError: If string is empty
    """
    if not value or not value.strip():
        raise ValueError(f"{name} cannot be empty")


def validate_choice(value: str, choices: list[str], name: str) -> None:
    """
    Validate that a value is one of the allowed choices.

    Args:
        value: Value to validate
        choices: List of allowed values
        name: Name of the field for error messages

    Raises:
        ValueError: If value is not in choices
    """
    if value not in choices:
        raise ValueError(f"{name} must be one of {choices}, got {value}")
