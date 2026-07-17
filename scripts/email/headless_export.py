#!/usr/bin/env python3
"""Headless M365 export CLI — Graph MIME → matter-tree .eml + receipt."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from email_export.graph_client import (  # noqa: E402
    GraphAuthError,
    GraphClient,
    GraphNotFoundError,
    fixture_mime_bytes,
)
from email_export.imprint import imprint_eml  # noqa: E402
from email_export.intent import EmailSyncIntent, Selector, load_intent  # noqa: E402
from email_export.receipt import ExportReceipt, ReceiptEntry  # noqa: E402
from email_export.sink import resolve_sink_dir, write_eml  # noqa: E402


def _selector_label(selector: Selector) -> str:
    if selector.kind == "fingerprint":
        return f"{selector.fingerprint_from}|{selector.fingerprint_subject}"
    return selector.value


def _maybe_imprint(
    *,
    intent: EmailSyncIntent,
    sink_path: Path,
    dry_run: bool,
    imprint: bool,
) -> tuple[str | None, str | None, str | None]:
    """Return (imprint_status, entity_id, error) when --imprint is set."""
    if not imprint:
        return None, None, None
    if dry_run:
        return None, None, None
    result = imprint_eml(sink_path, matter_id=intent.matter_id)
    return result.status, result.entity_id, result.error


def _process_selector(
    *,
    intent: EmailSyncIntent,
    selector: Selector,
    sink_dir: Path,
    dry_run: bool,
    graph: GraphClient | None,
    imprint: bool,
) -> ReceiptEntry:
    label = _selector_label(selector)
    try:
        if dry_run:
            mime_bytes = fixture_mime_bytes(selector, intent.account)
        elif graph is None:
            raise GraphAuthError("Graph client unavailable")
        else:
            mime_bytes = graph.fetch_mime(intent.account, selector)
        result = write_eml(sink_dir, mime_bytes)
        imprint_status, entity_id, imprint_error = _maybe_imprint(
            intent=intent,
            sink_path=result.sink_path,
            dry_run=dry_run,
            imprint=imprint,
        )
        entry_status = result.status
        if imprint_status == "imprint_failed":
            entry_status = "imprint_failed"
        return ReceiptEntry(
            selector_kind=selector.kind,
            selector_value=label,
            status=entry_status,
            sink_path=str(result.sink_path),
            content_hash=result.content_hash,
            imprint_status=imprint_status,
            entity_id=entity_id,
            error=imprint_error,
        )
    except GraphAuthError as exc:
        return ReceiptEntry(
            selector_kind=selector.kind,
            selector_value=label,
            status="auth_failed",
            error=str(exc),
        )
    except GraphNotFoundError as exc:
        return ReceiptEntry(
            selector_kind=selector.kind,
            selector_value=label,
            status="not_found",
            error=str(exc),
        )


def run_export(
    intent: EmailSyncIntent,
    *,
    dry_run: bool,
    sink_override: Path | None,
    imprint: bool = False,
) -> ExportReceipt:
    receipt = ExportReceipt(
        idempotency_key=intent.idempotency_key,
        matter_id=intent.matter_id,
        account=intent.account,
        fetch_path=intent.fetch_path,
        dry_run=dry_run,
    )
    sink_dir = resolve_sink_dir(intent.sink_uri, override=sink_override)

    graph: GraphClient | None = None
    if not dry_run:
        try:
            graph = GraphClient.from_env()
        except GraphAuthError as exc:
            for selector in intent.selectors:
                receipt.add(
                    ReceiptEntry(
                        selector_kind=selector.kind,
                        selector_value=_selector_label(selector),
                        status="auth_failed",
                        error=str(exc),
                    )
                )
            return receipt

    for selector in intent.selectors:
        receipt.add(
            _process_selector(
                intent=intent,
                selector=selector,
                sink_dir=sink_dir,
                dry_run=dry_run,
                graph=graph,
                imprint=imprint,
            )
        )
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Headless M365 export — Graph MIME to matter-tree .eml"
    )
    parser.add_argument(
        "--intent",
        required=True,
        help="Path or cortex:// URI to email-sync intent sidecar YAML",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write fixture MIME without calling Graph",
    )
    parser.add_argument(
        "--receipt-out",
        help="Write receipt JSON to this path (default: stdout)",
    )
    parser.add_argument(
        "--sink-override",
        type=Path,
        help="Override sink directory (for dry-run dogfood prep)",
    )
    parser.add_argument(
        "--imprint",
        action="store_true",
        help="After sink write, create sparse correspondence:* (skipped on --dry-run)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        intent = load_intent(args.intent)
    except (FileNotFoundError, ValueError) as exc:
        print(f"intent error: {exc}", file=sys.stderr)
        return 2

    receipt = run_export(
        intent,
        dry_run=args.dry_run,
        sink_override=args.sink_override,
        imprint=args.imprint,
    )
    payload = receipt.to_json()
    if args.receipt_out:
        Path(args.receipt_out).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return receipt.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
