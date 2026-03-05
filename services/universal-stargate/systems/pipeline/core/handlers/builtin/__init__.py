"""
Builtin step handlers subpackage.

Re-exports the public API of the original builtin.py so existing
``from .builtin import X`` imports continue to work unchanged.
"""

from ..registry import register_handler  # noqa: E402
from .base import BaseHandler
from .select_winner import SelectWinnerHandler
from .types import ModelCallResult, RenderedPrompt

register_handler(SelectWinnerHandler)

# Import sibling handler modules to trigger their @register_handler decorators.
# These live at the handlers/ level (one package up), not inside builtin/.
from .. import assess_loop as _assess_loop  # noqa: E402, F401
from .. import generate as _generate  # noqa: E402, F401
from .. import pipeline_call as _pipeline_call  # noqa: E402, F401
from .. import select_output as _select_output  # noqa: E402, F401
from ..generate import GenericGenerateHandler  # noqa: E402, F401

__all__ = [
    "BaseHandler",
    "GenericGenerateHandler",
    "ModelCallResult",
    "RenderedPrompt",
    "SelectWinnerHandler",
]
