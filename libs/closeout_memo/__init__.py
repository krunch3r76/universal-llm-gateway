"""Pointer-only closeout memo contract shared by GIW and the closeout-memo pipeline."""

from closeout_memo.models import CloseoutMemoRequest, memo_id_for
from closeout_memo.render import MEMO_OPENING, STANCE_LINE, render_memos

__all__ = [
    "MEMO_OPENING",
    "STANCE_LINE",
    "CloseoutMemoRequest",
    "memo_id_for",
    "render_memos",
]
