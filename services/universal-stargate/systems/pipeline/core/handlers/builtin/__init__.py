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

# Import sibling handler modules to trigger their @register_handler
# decorators. These live at the handlers/ level (one package up).
from .. import archive_assistant_turn as _archive_assistant_turn  # noqa: E402, F401
from .. import archive_user_turn as _archive_user_turn  # noqa: E402, F401
from .. import assemble_thread as _assemble_thread  # noqa: E402, F401
from .. import assess_loop as _assess_loop  # noqa: E402, F401
from .. import data_sink as _data_sink  # noqa: E402, F401
from .. import data_source as _data_source  # noqa: E402, F401
from .. import frontier_dispatch as _frontier_dispatch  # noqa: E402, F401
from .. import parse_json as _parse_json  # noqa: E402, F401
from .. import pipeline_call as _pipeline_call  # noqa: E402, F401
from .. import rag_search as _rag_search  # noqa: E402, F401
from .. import select_output as _select_output  # noqa: E402, F401

# generate: imported via GenericGenerateHandler below
from ..generate import GenericGenerateHandler  # noqa: E402, F401

__all__ = [
    "BaseHandler",
    "GenericGenerateHandler",
    "ModelCallResult",
    "RenderedPrompt",
    "SelectWinnerHandler",
]
