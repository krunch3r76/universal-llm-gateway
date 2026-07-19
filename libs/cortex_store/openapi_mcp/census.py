"""Four-bucket census — served / R-b-only / neither / untypeable (A2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from cortex_store.dispatch_ops import _OP_SPECS

from ._route_map import TYPED_ROUTE_BY_OP, UNTYPEABLE_OPS

Bucket = str  # served | rb_only | neither | untypeable
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CANONICAL = _REPO_ROOT / "config" / "mcp" / "canonical.yaml"


@dataclass(frozen=True, slots=True)
class FourBucketCensus:
    served: frozenset[str]
    rb_only: frozenset[str]
    neither: frozenset[str]
    untypeable: frozenset[str]

    @property
    def total(self) -> int:
        return (
            len(self.served)
            + len(self.rb_only)
            + len(self.neither)
            + len(self.untypeable)
        )


def _rb_schema_ops(canonical_yaml_path: Path) -> frozenset[str]:
    data: dict[str, Any] = yaml.safe_load(canonical_yaml_path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for row in data.get("tools", []):
        if row.get("domain") != "cortex":
            continue
        if not row.get("json_schema"):
            continue
        out.add(row["dispatcher_call_shape"]["dispatch_value"])
    return frozenset(out)


def build_four_bucket_census(
    *,
    op_specs: dict[str, str] | None = None,
    canonical_yaml_path: Path | None = None,
) -> FourBucketCensus:
    """Partition every dispatch op into the L0 T1-1 four-bucket join."""
    ops = frozenset(op_specs or _OP_SPECS)
    served = frozenset(TYPED_ROUTE_BY_OP) & ops
    untypeable = UNTYPEABLE_OPS & ops
    rb_all = _rb_schema_ops(canonical_yaml_path or _DEFAULT_CANONICAL) & ops
    rb_only = rb_all - served - untypeable
    neither = ops - served - rb_only - untypeable
    return FourBucketCensus(
        served=served,
        rb_only=rb_only,
        neither=neither,
        untypeable=untypeable,
    )


def render_census_markdown(census: FourBucketCensus) -> str:
    """Render durable census artifact body."""
    lines = [
        "# Four-bucket census — todo:openapi-mcp-dispatch-retire (A2)",
        "",
        f"**Total ops:** {census.total} · **Generated:** mechanical from "
        "`libs/cortex_store/openapi_mcp/census.py`",
        "",
        "| Bucket | Count | Meaning |",
        "|---|---:|---|",
        f"| served | {len(census.served)} | Typed OpenAPI route exists (operationId) |",
        f"| R-b-only | {len(census.rb_only)} | canonical.yaml json_schema, no typed route yet |",
        f"| neither | {len(census.neither)} | dispatch-handler only; route to mint or vanish |",
        f"| untypeable | {len(census.untypeable)} | adapter-orchestration; no HTTP SOT target |",
        "",
        "## served",
        "",
    ]
    for op in sorted(census.served):
        route = TYPED_ROUTE_BY_OP[op]
        lines.append(f"- `{op}` → `{route.method} {route.path}` (`{route.operation_id}`)")
    lines.extend(["", "## R-b-only", ""])
    for op in sorted(census.rb_only):
        lines.append(f"- `{op}`")
    lines.extend(["", "## neither", ""])
    for op in sorted(census.neither):
        lines.append(f"- `{op}`")
    lines.extend(["", "## untypeable", ""])
    for op in sorted(census.untypeable):
        lines.append(f"- `{op}`")
    lines.append("")
    return "\n".join(lines)
