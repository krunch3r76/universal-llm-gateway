from __future__ import annotations

import re

from research_dialogue._types import ParsedContract

_TAG = re.compile(
    r"<(answer|ledger|next_queries|status)>(.*?)</\1>",
    re.DOTALL | re.IGNORECASE,
)


def parse_contract(text: str) -> ParsedContract:
    found: dict[str, str] = {}
    for m in _TAG.finditer(text):
        found[m.group(1).lower()] = m.group(2).strip()
    missing = [t for t in ("answer", "ledger", "next_queries", "status") if t not in found]
    if missing:
        return ParsedContract(
            answer=found.get("answer", ""),
            ledger=found.get("ledger", ""),
            next_queries=[],
            status=found.get("status", ""),
            extra_query_lines=0,
            ok=False,
            error=f"missing tags: {', '.join(missing)}",
        )
    raw_queries = [ln.strip() for ln in found["next_queries"].splitlines() if ln.strip()]
    extra = max(0, len(raw_queries) - 4)
    queries: list[tuple[str, str]] = []
    for ln in raw_queries[:4]:
        if "|" not in ln:
            return ParsedContract(
                answer=found["answer"],
                ledger=found["ledger"],
                next_queries=queries,
                status=found["status"],
                extra_query_lines=extra,
                ok=False,
                error=f"malformed query line: {ln!r}",
            )
        scope, concept = ln.split("|", 1)
        scope = scope.strip()
        concept = concept.strip()
        if not scope or not concept:
            return ParsedContract(
                answer=found["answer"],
                ledger=found["ledger"],
                next_queries=queries,
                status=found["status"],
                extra_query_lines=extra,
                ok=False,
                error=f"empty scope or concept in line: {ln!r}",
            )
        queries.append((scope, concept))
    status = found["status"].strip()
    if not status:
        return ParsedContract(
            answer=found["answer"],
            ledger=found["ledger"],
            next_queries=queries,
            status=status,
            extra_query_lines=extra,
            ok=False,
            error="empty status",
        )
    return ParsedContract(
        answer=found["answer"],
        ledger=found["ledger"],
        next_queries=queries,
        status=status,
        extra_query_lines=extra,
        ok=True,
    )
