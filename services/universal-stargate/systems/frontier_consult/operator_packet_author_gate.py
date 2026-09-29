"""Refuse operator-authored generate sidecars without retrieval or author-route line."""

from __future__ import annotations

_OPERATOR_FROM_AGENT_LINE = "from_agent: web-anthropic"
_AUTHORED_BY_PREFIX = "authored_by: operator · reason:"

FIX_HINT = (
    "Author the packet via team_dispatch(seat=cursor-sdk) under "
    "retrieval-before-authoring, "
    "or open the file with 'authored_by: operator · reason: "
    "<why the author route was skipped>'. "
    "lane-act-gates step 3: this seat does not draft the body."
)


def _has_column_zero_retrieval(text: str) -> bool:
    return any(line.startswith("retrieval:") for line in text.splitlines())


def _first_nonempty_after_yaml_front_matter(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            for rest in lines[idx + 1 :]:
                stripped = rest.strip()
                if stripped:
                    return stripped
            return None
    return None


def _first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _has_authored_by_escape(text: str) -> bool:
    if text.startswith("---"):
        first = _first_nonempty_after_yaml_front_matter(text)
    else:
        first = _first_nonempty_line(text)
    return first is not None and first.startswith(_AUTHORED_BY_PREFIX)


def _front_matter_has_web_anthropic_from_agent(text: str) -> bool:
    lines = text.splitlines()
    if not lines:
        return False
    if lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                break
            if lines[idx].strip() == _OPERATOR_FROM_AGENT_LINE:
                return True
        return False
    for line in lines[:40]:
        if line.strip() == _OPERATOR_FROM_AGENT_LINE:
            return True
    return False


def operator_packet_author_refusal(text: str) -> str | None:
    """Return ``FIX_HINT`` when the sidecar looks operator-drafted without an escape.

    Escape A: a column-0 ``retrieval:`` line anywhere in the file.
    Escape B: the first non-empty body line (after YAML front matter when present)
    starts with ``authored_by: operator · reason:``.
    """
    if not _front_matter_has_web_anthropic_from_agent(text):
        return None
    if _has_column_zero_retrieval(text):
        return None
    if _has_authored_by_escape(text):
        return None
    return FIX_HINT
