"""The four production JobSpecs. Argv builders return argument lists only.

No shell string is assembled here. Runner-owned flags (the watch state
file) are appended by the runner, not accepted from the create body.
``page: true`` maps to ``--page``; ``--no-page`` is never emitted.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from bus_watch.poll import DEFAULT_WAIT_SLICE_S
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from jobs.spec import GraduationTarget, JobSpec

_SUNSET = date(2027, 4, 1)
_ARM = re.compile(r"^[a-z0-9][a-z0-9 ._-]{0,63}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LABEL = re.compile(r"^[a-z0-9][a-z0-9 ._-]{0,63}$")
_AGENT = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_THREAD = re.compile(r"^[0-9]{1,12}$")
_ARXIV = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")

_TOYS = (
    "refresh-connector",
    "--mcp-url",
    "https://mcp.k-1.me/mcp/life",
    "--connector-name",
    "toys",
)
_ULG_CODE = (
    "refresh-connector",
    "--mcp-url",
    "https://mcp.k-1.me/mcp/code",
    "--connector-name",
    "ulg-code",
    "--add-only",
    "--timeout",
    "90",
)


class _Forbid(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArticleFetchArgs(_Forbid):
    """arXiv id or one https URL, plus force and dry-run. Always download-only.

    The script's ``--subdir`` stays required unless ``--download-only`` is
    set. This job always passes ``--download-only``, so the create body has
    no directory field.
    """

    arxiv: str | None = None
    url: HttpUrl | None = None
    force: bool = False
    dry_run: bool = False

    @model_validator(mode="after")
    def xor_source(self) -> ArticleFetchArgs:
        if bool(self.arxiv) == bool(self.url):
            raise ValueError("exactly one of arxiv or url is required")
        if self.arxiv is not None and _ARXIV.fullmatch(self.arxiv) is None:
            raise ValueError("arxiv id is not in the accepted form")
        if self.url is not None and self.url.scheme != "https":
            raise ValueError("url scheme must be https")
        return self


class ArticleResult(_Forbid):
    """Final stdout JSON line from ``ingest-article --download-only``."""

    model_config = ConfigDict(extra="ignore")

    source_path: str
    sha256: str
    byte_count: int = Field(alias="bytes")
    filename: str
    arxiv_id: str | None = None
    url: str | None = None
    title: str | None = None
    authors: list[str] | str | None = None
    venue: str | None = None
    year: int | str | None = None


class IdeHopArgs(_Forbid):
    """Host hop arguments. GUI host, remote repo, and force stay off the wire."""

    root: str = "10479"
    row: str
    transcript_id: str
    arm: list[str] = Field(default_factory=list)
    no_auto_arm: bool = False
    tip_cp: int | None = Field(default=None, ge=0)
    exclude_lane: list[str] = Field(default_factory=list)
    dry_run: bool = False

    @model_validator(mode="after")
    def shapes(self) -> IdeHopArgs:
        if _THREAD.fullmatch(self.root) is None:
            raise ValueError("root must be a thread id")
        if not 1 <= len(self.row) <= 500 or "\n" in self.row:
            raise ValueError("row must be 1-500 characters without a newline")
        if not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            self.transcript_id,
        ):
            raise ValueError("transcript_id must be a UUID")
        for item in self.arm:
            if _ARM.fullmatch(item) is None:
                raise ValueError("arm label is not accepted")
        for item in self.exclude_lane:
            if _THREAD.fullmatch(item) is None:
                raise ValueError("exclude_lane entry must be a thread id")
        return self


class BusReplyWatchArgs(_Forbid):
    """Bounded watcher arm. ``page`` true becomes ``--page`` and never ``--no-page``."""

    thread: str
    after_turn: int = Field(ge=0)
    from_agent: str = "web-anthropic"
    label: str
    execution_id: str | None = None
    no_producer: bool = False
    page: bool = True
    page_on_arm: bool = True

    @model_validator(mode="after")
    def shapes(self) -> BusReplyWatchArgs:
        if _THREAD.fullmatch(self.thread) is None:
            raise ValueError("thread must be a thread id")
        if _AGENT.fullmatch(self.from_agent) is None:
            raise ValueError("from_agent is not an agent slug")
        if _LABEL.fullmatch(self.label) is None:
            raise ValueError("label is not accepted")
        has_exec = bool(self.execution_id)
        if has_exec == self.no_producer:
            raise ValueError("exactly one of execution_id or no_producer")
        return self


class ClaudeSyncArgs(_Forbid):
    """Discriminated ``action`` for the three code-surface sync verbs."""

    action: Literal["upload", "refresh_connector", "refresh_operator_connectors"]
    slugs: list[str] | None = None
    replace: bool = False
    connector: Literal["toys", "ulg-code"] | None = None

    @model_validator(mode="after")
    def by_action(self) -> ClaudeSyncArgs:
        if self.action == "upload":
            slugs = self.slugs or []
            if not 1 <= len(slugs) <= 50:
                raise ValueError("slugs must contain 1 to 50 entries")
            for slug in slugs:
                if _SLUG.fullmatch(slug) is None:
                    raise ValueError("slug must be lowercase without a slash")
            if self.connector is not None:
                raise ValueError("connector is not used for upload")
            return self
        if self.action == "refresh_connector":
            if self.connector is None:
                raise ValueError("connector is required")
            if self.slugs:
                raise ValueError("slugs are not used for refresh_connector")
            return self
        if self.slugs or self.connector is not None or self.replace:
            raise ValueError("refresh_operator_connectors takes no fields")
        return self


def _article_argv(args: BaseModel) -> list[str]:
    body = args
    assert isinstance(body, ArticleFetchArgs)
    argv = ["--download-only"]
    if body.arxiv:
        argv.extend(["--arxiv", body.arxiv])
    if body.url is not None:
        argv.extend(["--url", str(body.url)])
    if body.force:
        argv.append("--force")
    if body.dry_run:
        argv.append("--dry-run")
    return argv


def _ide_argv(args: BaseModel) -> list[str]:
    body = args
    assert isinstance(body, IdeHopArgs)
    argv = [
        "--root",
        body.root,
        "--row",
        body.row,
        "--transcript-id",
        body.transcript_id,
    ]
    for label in body.arm:
        argv.extend(["--arm", label])
    if body.no_auto_arm:
        argv.append("--no-auto-arm")
    if body.tip_cp is not None:
        argv.extend(["--tip-cp", str(body.tip_cp)])
    for lane in body.exclude_lane:
        argv.extend(["--exclude-lane", lane])
    if body.dry_run:
        argv.append("--dry-run")
    return argv


def _watch_argv(args: BaseModel) -> list[str]:
    body = args
    assert isinstance(body, BusReplyWatchArgs)
    argv = [
        "--thread",
        body.thread,
        "--after-turn",
        str(body.after_turn),
        "--from-agent",
        body.from_agent,
        "--label",
        body.label,
    ]
    if body.page:
        argv.append("--page")
    if body.no_producer:
        argv.append("--no-producer")
    if body.execution_id:
        argv.extend(["--execution-id", body.execution_id])
    return argv


def _sync_argv(args: BaseModel) -> list[str]:
    body = args
    assert isinstance(body, ClaudeSyncArgs)
    if body.action == "upload":
        argv = ["upload", "--slugs", *(body.slugs or []), "--continue-on-error"]
        if body.replace:
            argv.append("--replace")
        return argv
    if body.action == "refresh_connector" and body.connector == "toys":
        return list(_TOYS)
    if body.action == "refresh_connector":
        return list(_ULG_CODE)
    return ["refresh-operator-connectors"]


def _target(kind: Literal["satellite_route", "manage_lifecycle"], owner: str, note: str) -> GraduationTarget:
    return GraduationTarget(kind=kind, owner=owner, note=note)


def production_specs() -> tuple[JobSpec, ...]:
    """Return the four inventory jobs. ``load_registry`` enforces the cap."""
    idle_watch = int(3 * DEFAULT_WAIT_SLICE_S)
    return (
        JobSpec(
            name="article-fetch",
            description="Download one paper without registering or indexing it.",
            surfaces=frozenset({"code"}),
            args_model=ArticleFetchArgs,
            argv=_article_argv,
            executable="scripts/ingest-article",
            result_schema=ArticleResult,
            idle_seconds=120,
            graduates_to=_target(
                "satellite_route",
                "web_fetcher",
                "Outbound fetch belongs to the web-fetcher satellite; register stays rag upsert_article.",
            ),
            sunset=_SUNSET,
            handle="capability:jobs/article-fetch",
        ),
        JobSpec(
            name="ide-hop",
            description="Seal a departing IDE tab and keystroke the successor hop.",
            surfaces=frozenset({"code"}),
            args_model=IdeHopArgs,
            argv=_ide_argv,
            executable="scripts/liaison-ide-hop.py",
            result_schema=None,
            idle_seconds=120,
            graduates_to=_target(
                "manage_lifecycle",
                "liaison",
                "Hop becomes an event-driven action of the Stage 5 managed liaison daemon.",
            ),
            sunset=_SUNSET,
            handle="capability:jobs/ide-hop",
        ),
        JobSpec(
            name="bus-reply-watch",
            description="Arm a bounded agent-bus reply watch on one thread.",
            surfaces=frozenset({"code"}),
            args_model=BusReplyWatchArgs,
            argv=_watch_argv,
            executable="scripts/watch-bus-consult-and-page.py",
            result_schema=None,
            idle_seconds=idle_watch,
            graduates_to=_target(
                "manage_lifecycle",
                "watch",
                "Arming moves to the Stage 5 managed watch daemon.",
            ),
            sunset=_SUNSET,
            handle="capability:jobs/bus-reply-watch",
            pre_run=_page_on_arm,
        ),
        JobSpec(
            name="claude-ai-sync",
            description="Upload skills or refresh a code-surface Claude connector.",
            surfaces=frozenset({"code"}),
            args_model=ClaudeSyncArgs,
            argv=_sync_argv,
            executable="scripts/cortex/claude-ai-sync-jupiter",
            result_schema=None,
            idle_seconds=300,
            graduates_to=_target(
                "satellite_route",
                "cdp_ask",
                "CDP lane owner already managed; verbs become cdp_ask routes.",
            ),
            sunset=_SUNSET,
            handle="capability:jobs/claude-ai-sync",
        ),
    )


async def _page_on_arm(args: BaseModel, run_id: str) -> None:
    """Page that a gate-1 watcher armed. Subject text carries the thread id."""
    from pager_notify.client import notify_pager

    if not isinstance(args, BusReplyWatchArgs) or not args.page_on_arm:
        return
    result = await notify_pager(
        subject=f"Gate 1 watcher armed — thread {args.thread}",
        body=f"run {run_id} label {args.label}",
        tag="jobs-watch",
    )
    if not result:
        raise RuntimeError(result.reason or "pager failed")
