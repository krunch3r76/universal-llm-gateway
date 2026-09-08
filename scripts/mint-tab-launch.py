#!/usr/bin/env python3
"""Mint a WORK-tab launch prompt from a house ``## Pools`` row.

Emits ``tmp/prompts/tab-launch-<slice>-<date>.md`` with the pool floor
(skills, reads, closeout shape, forbidden) plus a slice body placeholder.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "libs"))

from agent_bus_store.house_pools import (  # noqa: E402
    PoolsParseError,
    continuity_card_uri,
    load_continuity_card,
    parse_closeout_thread_id,
    parse_pools,
)

_DEFAULT_SPEC_URI = (
    "cortex://notes/system/specs/continuity-house-pool-manifest.md"
)
_OPPORTUNITIES_URI_TEMPLATE = (
    "cortex://notes/system/threads/{house_id}-opportunities.md"
)
_SLICE_HEADING_RE = re.compile(
    r"(?im)^###\s+(?:O\d+\s*[—-]\s*)?{slice}\b"
)


def _opportunity_excerpt(opportunities_text: str, slice_id: str) -> str | None:
    pattern = _SLICE_HEADING_RE.pattern.format(
        slice=re.escape(slice_id.replace("_", "-"))
    )
    match = re.search(pattern, opportunities_text)
    if match is None:
        match = re.search(
            _SLICE_HEADING_RE.pattern.format(slice=re.escape(slice_id)),
            opportunities_text,
        )
    if match is None:
        return None
    tail = opportunities_text[match.start() :]
    next_heading = re.search(r"(?m)^###\s+", tail[len(match.group(0)) :])
    if next_heading is None:
        return tail.strip()
    end = len(match.group(0)) + next_heading.start()
    return tail[:end].strip()


def _skill_lines(slugs: tuple[str, ...]) -> str:
    return "\n".join(f"Use the `{slug}` skill." for slug in slugs)


def _render_prompt(
    *,
    house_id: str,
    slice_id: str,
    row,
    spec_uri: str,
    opportunities_uri: str,
    slice_body: str,
) -> str:
    coord = parse_closeout_thread_id(row.closeout) or "10303"
    house_ref = house_id.strip().removeprefix("agent-bus:")
    closeout_fields = "commit · pytest · files · verdict · bus_posts"
    return (
        f"# WORK {slice_id} — house agent-bus:{house_ref}\n\n"
        f"{_skill_lines(row.must_load)}\n\n"
        f"**Read:**\n"
        f"- `{opportunities_uri}` (row `{slice_id}`)\n"
        f"- `{spec_uri}`\n"
        f"- `{continuity_card_uri(house_id)}`\n\n"
        f"**Closeout:** agent-bus:{coord} — subject "
        f"`CLOSEOUT — WORK {slice_id}`; body fields: {closeout_fields} "
        f"(must read `none on {house_ref}`)\n\n"
        f"**Forbidden:** posting on agent-bus:{house_ref}; deciding a RULING fork "
        f"(post `status:needs-attended` on {coord} instead); touching files outside "
        f"`files_expected`.\n\n"
        f"{slice_body}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--house", required=True, help="House agent-bus thread id")
    parser.add_argument(
        "--pool",
        default="work",
        help="Pool name on the continuity card (default: work)",
    )
    parser.add_argument(
        "--slice",
        required=True,
        help="Opportunity / slice id for the tab title and body",
    )
    parser.add_argument(
        "--spec-uri",
        default=_DEFAULT_SPEC_URI,
        help="Cited spec URI for the Read block",
    )
    parser.add_argument(
        "--output",
        help="Output path (default: tmp/prompts/tab-launch-<slice>-<date>.md)",
    )
    parser.add_argument(
        "--slice-body",
        default="[slice body — authored]",
        help="Body text after the floor header",
    )
    args = parser.parse_args(argv)

    house_id = args.house.strip().removeprefix("agent-bus:")
    card = load_continuity_card(house_id)
    if card is None:
        print(
            f"error: continuity card missing for house {house_id}",
            file=sys.stderr,
        )
        return 1
    try:
        row = parse_pools(card)[args.pool.strip().lower()]
    except (PoolsParseError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    from agent_bus_store.house_pools import continuity_card_path

    opportunities_file = (
        continuity_card_path(house_id).parent / f"{house_id}-opportunities.md"
    )
    opportunities_uri = _OPPORTUNITIES_URI_TEMPLATE.format(house_id=house_id)
    slice_body = args.slice_body
    if opportunities_file.is_file():
        excerpt = _opportunity_excerpt(
            opportunities_file.read_text(encoding="utf-8"),
            args.slice,
        )
        if excerpt:
            slice_body = f"{excerpt}\n\n---\n\n{slice_body}"

    today = date.today().isoformat()
    output = (
        Path(args.output)
        if args.output
        else _REPO_ROOT / "tmp" / "prompts" / f"tab-launch-{args.slice}-{today}.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    prompt = _render_prompt(
        house_id=house_id,
        slice_id=args.slice,
        row=row,
        spec_uri=args.spec_uri,
        opportunities_uri=opportunities_uri,
        slice_body=slice_body,
    )
    output.write_text(prompt, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
