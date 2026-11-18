"""Gate C: build caller-inventory block for generate-lane review prompts."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from implement_admission.consumer_import_verify import repo_root as _default_repo_root
from implement_admission.domain_change_caller_gate import (
    ImporterInventory,
    build_importer_inventory,
)
from universal_logging import get_logger

from .packet_review_surface import (
    CALLER_INVENTORY_INSTRUCTION,
    classify_review_surface,
    files_expected_paths,
)

logger = get_logger(__name__)

BLOCK_SITE_LINE_CAP = 200
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")


def render_not_computed(reason: str) -> str:
    lines = [
        f'<caller_inventory data_only="true" status="not_computed" reason="{reason}">',
        "</caller_inventory>",
        CALLER_INVENTORY_INSTRUCTION,
    ]
    return "\n".join(lines)


def render_caller_inventory_block(inv: ImporterInventory) -> str:
    if inv.status == "incomplete":
        opener = (
            f'<caller_inventory data_only="true" status="incomplete" '
            f'reason="{inv.incomplete_reason}" modules="{len(inv.modules)}" '
            f'sites="{len(inv.sites)}" sha256="{inv.sha256}">'
        )
    else:
        opener = (
            f'<caller_inventory data_only="true" status="complete" '
            f'modules="{len(inv.modules)}" sites="{len(inv.sites)}" '
            f'sha256="{inv.sha256}">'
        )
    body: list[str] = [opener]
    written = 0
    for site in inv.sites:
        if written >= BLOCK_SITE_LINE_CAP:
            break
        if not _SAFE_PATH_RE.match(site.path):
            continue
        if site.names:
            line = (
                f"{site.path}:{site.line} imports {', '.join(site.names)} "
                f"from {site.module}"
            )
        else:
            line = f"{site.path}:{site.line} imports {site.module}"
        if not site.in_files_expected:
            line += " [outside files_expected]"
        body.append(line)
        written += 1
    if written < len(inv.sites):
        body.append(f"… {len(inv.sites) - written} more site(s) not listed")
    body.append("</caller_inventory>")
    body.append(CALLER_INVENTORY_INSTRUCTION)
    return "\n".join(body)


async def build_caller_inventory_block(
    packet_text: str | None,
    *,
    repo_root: Path | None = None,
) -> str | None:
    if packet_text is None or not packet_text.strip():
        return None
    if classify_review_surface(packet_text) != "source":
        return None
    paths = files_expected_paths(packet_text)
    lib_paths = [p for p in paths if p.startswith("libs/") and p.endswith(".py")]
    if not lib_paths:
        return render_not_computed("no_lib_paths_in_files_expected")
    root = repo_root or _default_repo_root()
    try:
        inv = await asyncio.to_thread(
            build_importer_inventory,
            lib_paths,
            repo_root=root,
            files_expected=paths,
        )
    except Exception:
        logger.warning("caller inventory scan_error", exc_info=True)
        return render_not_computed("scan_error")
    if inv.status == "no_lib_paths":
        return render_not_computed("no_lib_paths_in_files_expected")
    return render_caller_inventory_block(inv)
