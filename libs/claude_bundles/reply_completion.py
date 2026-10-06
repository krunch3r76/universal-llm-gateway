"""Structural completion predicates for claude.ai assistant reply harvest."""

from __future__ import annotations

from chat_harvest.chrome import is_chrome_only, is_tool_status_body
from review_verdict.grammar import has_parseable_verdict

from claude_bundles.cse_idle_probe import in_flight_from_state


def _is_cowork_cse_url(url: str) -> bool:
    return "/cowork/cse_" in (url or "")


def badge_only_body(state: dict) -> bool:
    """True when the scrape is tool-badge chrome, not assistant prose."""
    body = str(state.get("body") or "")
    return is_chrome_only(body) or is_tool_status_body(body)


def error_banner_message(state: dict, *, on_timeout: bool = False) -> str:
    """Human-readable HarvestIncompleteError detail including matched banner text."""
    kind = "error_banner on timeout" if on_timeout else "error_banner detected"
    match = (state.get("error_banner_match") or "").strip()
    text = (state.get("error_banner_text") or "").strip()
    bits = [
        kind,
        f"url={state.get('url')}",
        f"len={state.get('body_len')}",
    ]
    if match:
        bits.append(f"match={match!r}")
    if text and text.lower() != match.lower():
        bits.append(f"ctx={text[:200]!r}")
    return " ".join(bits)


def fatal_error_banner(state: dict) -> bool:
    """True when a banner is present AND the turn is idle (not recovering)."""
    return bool(state.get("error_banner")) and not in_flight_from_state(state)


def complete_enough(
    state: dict,
    *,
    base_len: int,
    base_n: int,
    min_growth: int,
    min_body: int,
    ignore_in_flight: bool = False,
    require_review_verdict: bool = False,
) -> bool:
    """Structural turn complete — ¬ a prose-length gate."""
    del min_growth, min_body, base_len
    if badge_only_body(state):
        return False
    if require_review_verdict and not has_parseable_verdict(
        str(state.get("body") or "")
    ):
        return False
    cur_len = state.get("body_len", 0)
    cur_n = state.get("n", 0)
    in_flight = in_flight_from_state(state) and not ignore_in_flight
    return bool(cur_n > base_n and cur_len > 0 and not in_flight)


def cowork_complete_enough(
    state: dict,
    *,
    base_len: int,
    base_n: int,
    min_growth: int,
    min_body: int,
    saw_working: bool,
    ignore_in_flight: bool = False,
    require_review_verdict: bool = False,
) -> bool:
    """URL-guarded Cowork fallback (24864) with positive new-turn guard."""
    if not _is_cowork_cse_url(state.get("url", "")):
        return False
    if in_flight_from_state(state) and not ignore_in_flight:
        return False
    del min_body, min_growth, saw_working
    cur_len = state.get("body_len", 0)
    cur_n = state.get("n", 0)
    if cur_len < 1 or badge_only_body(state):
        return False
    if require_review_verdict and not has_parseable_verdict(
        str(state.get("body") or "")
    ):
        return False

    grew_n = cur_n > base_n
    return bool(grew_n)
