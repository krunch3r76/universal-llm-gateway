"""Pydantic v2 request models for the synchronous REST endpoints.

Response shapes are the raw ``dict[str, Any]`` envelopes returned by
``libs/grokbuild`` functions — re-wrapping is explicitly forbidden so
callers get the canonical envelope without schema duplication.

Every request model carries ``model_config = ConfigDict(extra="forbid")``
to surface typos at deserialization rather than silently ignoring them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorktreeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ..., description="Short name for the worktree (no slashes or '..')."
    )
    branch: str = Field(
        ..., description="Branch to check out (must exist unless create_branch=True)."
    )
    source_repo: str = Field(
        ..., description="Absolute path to the source git repository."
    )
    create_branch: bool = Field(
        False,
        description="When True, create the branch from start_point (mirrors git worktree add -b).",
    )
    start_point: str = Field(
        "",
        description="Start-point ref for create_branch=True (defaults to HEAD when empty).",
    )


class PushRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote: str = Field("origin", description="Git remote name.")
    branch: str = Field(
        "", description="Branch to push (defaults to current branch when empty)."
    )
    set_upstream: bool = Field(
        True, description="Pass -u to set the upstream tracking reference."
    )


class PRCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr_title: str = Field(..., description="Title for the pull request.")
    pr_body: str = Field("", description="Body/description for the pull request.")
    pr_base: str = Field(
        "", description="Base branch for the PR (gh default when empty)."
    )
    pr_head: str = Field(
        "", description="Head branch for the PR (current branch when empty)."
    )
    draft: bool = Field(False, description="Create as a draft PR.")


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_repo: str = Field(
        ..., description="Absolute path to the source git repository."
    )
    slug: str = Field(..., description="Arc slug; branch becomes arc/<slug>.")
    name: str = Field(
        "", description="Worktree short name (defaults to slug when empty)."
    )
    reset_main: bool = Field(
        False,
        description="Opt-in: reset the main tree clean after the snapshot commit is durable.",
    )


ResultFormat = Literal["json", "text", "summary", "signals"]
