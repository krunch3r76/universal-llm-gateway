"""Test-only guard errors that must not be swallowed by broad ``except Exception`` handlers."""


class MechanicalLandHermeticViolation(BaseException):
    """Raised when offline mechanical-land tests would touch live bus/network."""
