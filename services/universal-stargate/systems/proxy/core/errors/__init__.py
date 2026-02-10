"""
Error builders subpackage.

Centralized error builders for consistent HTTP exception formatting.

Usage:
    from systems.proxy.core.errors import TokenErrorBuilder, ModelErrorBuilder

    # Or import all:
    from systems.proxy.core.errors import (
        TokenErrorBuilder,
        RequestErrorBuilder,
        ModelErrorBuilder,
        AuthErrorBuilder,
        raise_if_none,
    )
"""

from .auth_errors import AuthErrorBuilder
from .model_errors import ModelErrorBuilder
from .request_errors import RequestErrorBuilder
from .token_errors import TokenErrorBuilder
from .utils import raise_if_none

__all__ = [
    "AuthErrorBuilder",
    "ModelErrorBuilder",
    "RequestErrorBuilder",
    "TokenErrorBuilder",
    "raise_if_none",
]
