"""Charter root birth ceremony — mint → seed → tag-commit as one idempotent entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from . import bus_client, telemetry
from .admission.typed_work_item import (
    TypedAdmitError,
    TypedWorkItemAdmit,
    typed_record_valid,
    validate_typed_admit,
)
from .root_ledger import admit_work_item, load_root, open_default_ledger


@dataclass(frozen=True)
class BirthOutcome:
    """Result of ``birth_work_item`` — one root's atomic birth ceremony."""

    slug: str
    root_id: str
    minted: bool
    reclaimed: bool
    seeded: bool
    enrolled: bool
    tip_posted: bool
    duration_s: float


class BirthError(ValueError):
    """Raised when birth fails; tag is never committed after a failed seed."""

    error_code = "birth_failed"

    def __init__(
        self, *, step: str, detail: str, root_id: str | None = None
    ) -> None:
        super().__init__(detail)
        self.step = step
        self.detail = detail
        self.root_id = root_id


async def _emit_step(
    *,
    slug: str,
    root_id: str,
    step: str,
    outcome: str,
    detail: str = "",
) -> None:
    await telemetry.emit_birth_step(
        slug=slug,
        root_id=root_id,
        step=step,
        outcome=outcome,
        detail=detail,
    )


async def birth_work_item(
    *,
    slug: str,
    pickup_gid: str,
    pickup_lane: str,
    attendance: str,
    pickup_executor: str | None = None,
    scoreboard_uri: str = "",
    summary: str = "",
    tags: Sequence[str] | None = None,
    on_existing: Literal["preserve", "readmit"] = "preserve",
    post_tip: str | None = None,
) -> BirthOutcome:
    """Birth one charter root: thread mint, typed ledger seed, enrollment tag commit.

    Ordering is load-bearing: the bus thread is minted **without** the enrollment
    tag, the typed ledger row is seeded next, and the ``charter-runner`` tag is
    committed last so no tick observes a half-born root. Re-running converges via
    slug reclaim and idempotent upsert/enroll — no duplicate threads or status
    resets when ``on_existing=\"preserve\"`` and the row is already valid.
    """
    started = time.monotonic()
    root_id = ""
    minted = False
    reclaimed = False
    seeded = False
    enrolled = False
    tip_posted = False

    preflight_admit = TypedWorkItemAdmit(
        root_id="preflight",
        pickup_gid=pickup_gid,
        pickup_lane=pickup_lane,
        attendance=attendance,
        pickup_executor=pickup_executor,
        scoreboard_uri=scoreboard_uri,
    )
    try:
        validate_typed_admit(preflight_admit)
    except TypedAdmitError as exc:
        await _emit_step(
            slug=slug,
            root_id="",
            step="preflight",
            outcome="failed",
            detail=exc.detail,
        )
        raise BirthError(step="preflight", detail=exc.detail) from exc
    await _emit_step(slug=slug, root_id="", step="preflight", outcome="ok")

    existing_id = await bus_client.find_thread_id_by_slug(slug)
    if existing_id:
        root_id = existing_id
        reclaimed = True
        await _emit_step(
            slug=slug,
            root_id=root_id,
            step="reclaim",
            outcome="ok",
        )
    else:
        root_id = await bus_client.create_thread(
            slug=slug,
            summary=summary,
            tags=list(tags) if tags else None,
            enroll_charter_runner=False,
        )
        minted = True
        await _emit_step(
            slug=slug,
            root_id=root_id,
            step="mint",
            outcome="ok",
        )

    conn = open_default_ledger()
    try:
        existing_row = load_root(conn, root_id)
        skip_seed = (
            on_existing == "preserve"
            and existing_row is not None
            and typed_record_valid(existing_row)
        )
        if skip_seed:
            await _emit_step(
                slug=slug,
                root_id=root_id,
                step="seed",
                outcome="noop",
            )
        else:
            try:
                admit_work_item(
                    conn,
                    TypedWorkItemAdmit(
                        root_id=root_id,
                        pickup_gid=pickup_gid,
                        pickup_lane=pickup_lane,
                        attendance=attendance,
                        pickup_executor=pickup_executor,
                        scoreboard_uri=scoreboard_uri,
                    ),
                )
            except TypedAdmitError as exc:
                await _emit_step(
                    slug=slug,
                    root_id=root_id,
                    step="seed",
                    outcome="failed",
                    detail=exc.detail,
                )
                raise BirthError(
                    step="seed", detail=exc.detail, root_id=root_id
                ) from exc
            seeded = True
            await _emit_step(
                slug=slug,
                root_id=root_id,
                step="seed",
                outcome="ok",
            )
    finally:
        conn.close()

    enroll_result = await bus_client.enroll_root(root_id)
    enrolled = bool(enroll_result.get("enrolled"))
    await _emit_step(
        slug=slug,
        root_id=root_id,
        step="commit",
        outcome="ok" if enrolled else "noop",
    )

    if post_tip:
        await bus_client.post_root_checkpoint(
            root_id,
            subject="CHECKPOINT",
            body=post_tip,
        )
        tip_posted = True
        await _emit_step(
            slug=slug,
            root_id=root_id,
            step="tip",
            outcome="ok",
        )

    duration_s = time.monotonic() - started
    await telemetry.emit_birth_completed(
        slug=slug,
        root_id=root_id,
        minted=minted,
        reclaimed=reclaimed,
        seeded=seeded,
        enrolled=enrolled,
        tip_posted=tip_posted,
        duration_s=duration_s,
    )
    return BirthOutcome(
        slug=slug,
        root_id=root_id,
        minted=minted,
        reclaimed=reclaimed,
        seeded=seeded,
        enrolled=enrolled,
        tip_posted=tip_posted,
        duration_s=duration_s,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Birth one charter root.")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--gid", required=True, dest="pickup_gid")
    parser.add_argument("--lane", required=True, dest="pickup_lane")
    parser.add_argument("--attendance", required=True)
    parser.add_argument("--executor", default=None, dest="pickup_executor")
    parser.add_argument("--scoreboard", default="", dest="scoreboard_uri")
    parser.add_argument("--summary", default="")
    parser.add_argument("--tag", action="append", default=None, dest="tags")
    parser.add_argument(
        "--readmit",
        action="store_true",
        help="Overwrite pickup fields on an existing valid row.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


async def _main_async(argv: Sequence[str] | None = None) -> BirthOutcome:
    args = _parse_args(argv)
    on_existing: Literal["preserve", "readmit"] = (
        "readmit" if args.readmit else "preserve"
    )
    return await birth_work_item(
        slug=args.slug,
        pickup_gid=args.pickup_gid,
        pickup_lane=args.pickup_lane,
        attendance=args.attendance,
        pickup_executor=args.pickup_executor,
        scoreboard_uri=args.scoreboard_uri,
        summary=args.summary,
        tags=args.tags,
        on_existing=on_existing,
    )


def _main(argv: Sequence[str] | None = None) -> None:
    outcome = asyncio.run(_main_async(argv))
    print(json.dumps(asdict(outcome), indent=2))


if __name__ == "__main__":
    _main()
