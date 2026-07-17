#!/usr/bin/env python3
"""Standalone CLI — imprint sparse correspondence:* from sink .eml files."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from email_export.imprint import ImprintResult, imprint_eml  # noqa: E402


@dataclass(slots=True)
class ImprintReceipt:
    eml_path: str
    matter_id: str | None
    dry_run: bool
    result: ImprintResult

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "eml_path": self.eml_path,
            "matter_id": self.matter_id,
            "dry_run": self.dry_run,
            "status": self.result.status,
            "entity_id": self.result.entity_id,
            "error": self.result.error,
        }
        if self.result.planned_payload is not None:
            payload["planned_entity"] = self.result.planned_payload
        return payload

    def exit_code(self) -> int:
        if self.result.status == "imprint_failed":
            return 1
        return 0


def _collect_eml_paths(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(target.glob("*.eml"))
    raise FileNotFoundError(f"eml path not found: {target}")


def run_imprint(
    *,
    eml_paths: list[Path],
    matter_id: str | None,
    link_to: str | None,
    dry_run: bool,
) -> list[ImprintReceipt]:
    receipts: list[ImprintReceipt] = []
    for eml_path in eml_paths:
        result = imprint_eml(
            eml_path,
            matter_id=matter_id,
            link_to=link_to,
            dry_run=dry_run,
        )
        receipts.append(
            ImprintReceipt(
                eml_path=str(eml_path),
                matter_id=matter_id,
                dry_run=dry_run,
                result=result,
            )
        )
    return receipts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Imprint sparse correspondence:* entities from sink .eml files"
    )
    parser.add_argument(
        "--eml",
        required=True,
        type=Path,
        help="Path to a .eml file or directory of *.eml files",
    )
    parser.add_argument(
        "--matter-id",
        help="Matter id stored in attributes.link_to (e.g. work:rxrelief)",
    )
    parser.add_argument(
        "--link-to",
        help="Override link_to attribute (defaults to --matter-id)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse headers and print planned entity payload; no POST",
    )
    parser.add_argument(
        "--receipt-out",
        help="Write receipt JSON to this path (default: stdout)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        eml_paths = _collect_eml_paths(args.eml.expanduser())
    except FileNotFoundError as exc:
        print(f"eml error: {exc}", file=sys.stderr)
        return 2
    if not eml_paths:
        print(f"eml error: no *.eml files under {args.eml}", file=sys.stderr)
        return 2

    receipts = run_imprint(
        eml_paths=eml_paths,
        matter_id=args.matter_id,
        link_to=args.link_to,
        dry_run=args.dry_run,
    )
    payload = [receipt.to_dict() for receipt in receipts]
    text = json.dumps(payload if len(payload) > 1 else payload[0], indent=2)
    if args.receipt_out:
        Path(args.receipt_out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)

    return max(receipt.exit_code() for receipt in receipts)


if __name__ == "__main__":
    raise SystemExit(main())
