"""Keep-set trim for 12286 house-rules (#649 items 1–7) and ordered cortex writes.

Cortex note writes go through ``durable_io.atomic.durable_write_text`` (flock,
temp, fsync, replace, CAS). The house-rules pre-image is checked before any
write. A second append is skipped when the seven verbatim blocks are already
in the archive.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from durable_io.atomic import durable_write_text

K36_VERBATIM_MARKERS = (
    "Argon five-plan trial done and reported 2026-10-15 04:17Z",
    "Argon never both authors and reviews one item",
)

PRE_IMAGE_SHA = "e1c5284538aa5cae6caac4769f276b154de55a3e5a7c193f93ba65161ffc484c"
POST_TRIM_SHA = "3e492abe936faf09c25655cbcb952794124d52a2da1125545ff963398b5df760"

_K40_OLD = (
    '- K40 a:39361 (Kaywan ~07:34Z 2026-10-15, this session, verbatim: "By the way as for argon reviewing plans that was a surprising result. Perhaps argon should be deployed for the planning itself especially around frictions + zoom out"; "Including an alternative to cdp/fable for architecture sketches"): direction, not yet a rule change. Trial gemini-4-argon (via cursor) as the planning seat for friction plans and zoom-out passes, and as an alternative to cdp/fable for architecture sketches. K36 holds: Argon never authors and reviews the same item, so an Argon-authored plan is reviewed by cdp/opus-5.5 or a grok-bot. R4, E11 and the lean-dispatch dramaturg wire change only through a keep-set PLAN ACKed by maestro (15851 REQUEST plan:argon-planning-seat). Trial ACKed 15851#645; FAILED at row 1 (friction:39359, 15851#646: MAJOR zoom-out miss, predicate 3). Fail action applied 2026-10-15 by maestro 25320f63: R4 trial sentence deleted; Argon is K36 pre-flight review only; E11, dramaturg unchanged. Reopen: Kaywan\'s call.'
)
_K40_NEW = (
    "- K40 a:39361: trial FAILED row 1 (friction:39359, 15851#646); Argon = K36 pre-flight review only; Argon never authors and reviews one item; R4, E11, dramaturg unchanged; reopen is Kaywan's call."
)
_K36_LINE = (
    "- K36 a:39306: Argon five-plan trial done and reported 2026-10-15 04:17Z (scoreboard 12286-argon-trial-scoreboard.md). Argon never both authors and reviews one item. R4/E11 unchanged until Kaywan rules on the numbers.\n"
)


def archive_append_blocks() -> str:
    """Return the section-4 archive append (headings + verbatim bodies)."""
    blocks: list[str] = [
        "#### K40 a:39361 — #649 item 1 — verbatim house-rules line",
        _K40_OLD,
        "#### K36 a:39306 — #649 item 2 — verbatim house-rules line folded into K40",
        _K36_LINE.strip(),
        "#### K35 a:39297 — #649 item 3 — done clause (2)",
        "(2) done: 10479 closed 2026-10-14T23:20:54Z (10479#49687). ",
        "#### K25 a:39116 — #649 item 4 — hop desired_model sentence",
        " Hop successor desired_model=cdp/opus-5.5-extra (K26 a:39151 as amended by K30 a:39234, both later than K25).",
        "#### K22 a:39008 — #649 item 5 — Modifies K21 / SR7 fold",
        "Modifies K21. SR7 fold: step 9 and 9a hop call stay unchanged; ",
        "#### K37 a:39310 — #649 item 6 — verbatim quotes",
        ', this session, verbatim: "we shall leverage grok-bot more than grok-4.7 for judgment now that you have a lane to the stage manager"; "this applies to your house as well - we can lean less on grok-4.7 (which we have seen missed what argon caught) and more on grok-bot"',
        "#### K38 a:39314 — #649 item 6 — verbatim quotes",
        ', this session, verbatim: "If claudeburst is accessing a db that is on NFS (not local) it should be via an endpoint on stargate or on the satellite"; "making db access on NFS more reliable ... is the wrong approach"',
        "#### K39 a:39360 — #649 item 7 — warm intro and coding harnesses quotes",
        '; "Get a warm intro through code might be up our alley!"; "Few coding harnesses are as modern as opus-5.5 grok-bot AND argon"',
    ]
    return "\n\n".join(blocks) + "\n"


def trim_house_rules_body(text: str) -> str:
    """Apply #649 items 1–7 to the pre-trim house-rules body."""
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != PRE_IMAGE_SHA:
        msg = "pre-image sha mismatch; refusing trim on drifted house-rules"
        raise ValueError(msg)
    out = text.replace(_K40_OLD, _K40_NEW)
    out = out.replace(_K36_LINE, "")
    out = out.replace("(2) done: 10479 closed 2026-10-14T23:20:54Z (10479#49687). ", "")
    out = out.replace(
        " Hop successor desired_model=cdp/opus-5.5-extra (K26 a:39151 as amended by K30 a:39234, both later than K25).",
        "",
    )
    out = out.replace("Modifies K21. SR7 fold: step 9 and 9a hop call stay unchanged; ", "")
    out = out.replace(
        ', this session, verbatim: "we shall leverage grok-bot more than grok-4.7 for judgment now that you have a lane to the stage manager"; "this applies to your house as well - we can lean less on grok-4.7 (which we have seen missed what argon caught) and more on grok-bot"',
        "",
    )
    out = out.replace(
        ', this session, verbatim: "If claudeburst is accessing a db that is on NFS (not local) it should be via an endpoint on stargate or on the satellite"; "making db access on NFS more reliable ... is the wrong approach"',
        "",
    )
    out = out.replace(
        '; "Get a warm intro through code might be up our alley!"; "Few coding harnesses are as modern as opus-5.5 grok-bot AND argon"',
        "",
    )
    digest = hashlib.sha256(out.encode("utf-8")).hexdigest()
    if digest != POST_TRIM_SHA:
        raise ValueError(f"post-trim sha mismatch: got {digest}")
    return out


def archive_has_k36_verbatim(archive_text: str) -> bool:
    """True when the archive body contains the verbatim K36 block."""
    return all(marker in archive_text for marker in K36_VERBATIM_MARKERS)


@dataclass(frozen=True)
class KeepSetTrimReceipts:
    """Receipt order: archive, house-rules, fixture."""

    archive_written: bool
    house_rules_written: bool
    fixture_written: bool


def _house_rules_size_rejection(path: Path, trimmed: str, before_len: int) -> dict | None:
    """Call the MCP house-rules gate without importing the filesystem package.

    ``tools.*`` resolves only when ``services/mcp-server`` is on ``sys.path``.
    The libs test runner does not put it there, so the insert is required for
    this import. It is removed before return so it does not shadow ``tools``
    for the rest of the process.
    """
    mcp_root = Path(__file__).resolve().parents[2] / "services" / "mcp-server"
    root_s = str(mcp_root)
    inserted = root_s not in sys.path
    if inserted:
        sys.path.insert(0, root_s)
    try:
        from tools.filesystem._write_authority import evaluate_house_rules_post_edit_size
    finally:
        if inserted:
            sys.path.remove(root_s)

    return evaluate_house_rules_post_edit_size(
        path=path.name,
        post_edit_text=trimmed,
        before_len=before_len,
        dest_exists=True,
    )


def apply_keep_set_trim(
    archive: Path,
    house_rules: Path,
    fixture: Path,
    *,
    expected_archive_sha: str,
    expected_house_rules_sha: str,
) -> KeepSetTrimReceipts:
    """Check both pre-images, then append, trim, and copy the fixture.

    House-rules sha drift raises before any write. Archive append is skipped
    when ``archive_append_blocks`` is already present, so a retry does not
    duplicate the seven verbatim blocks.
    """
    archive_bytes = archive.read_bytes()
    if hashlib.sha256(archive_bytes).hexdigest() != expected_archive_sha:
        raise ValueError("archive sha drift at step 1")
    hr_bytes = house_rules.read_bytes()
    if hashlib.sha256(hr_bytes).hexdigest() != expected_house_rules_sha:
        raise ValueError("house-rules sha drift before trim write")
    append = archive_append_blocks()
    archive_text = archive_bytes.decode("utf-8")
    archive_written = False
    if append.strip() not in archive_text:
        durable_write_text(
            archive,
            archive_text + "\n" + append,
            expected_pre_image=expected_archive_sha,
        )
        archive_written = True
    if not archive_has_k36_verbatim(archive.read_text(encoding="utf-8")):
        raise ValueError("archive missing K36 verbatim block after append")
    hr_sha = hashlib.sha256(hr_bytes).hexdigest()
    if hr_sha == POST_TRIM_SHA:
        trimmed = hr_bytes.decode("utf-8")
    else:
        trimmed = trim_house_rules_body(hr_bytes.decode("utf-8"))
    rejection = _house_rules_size_rejection(house_rules, trimmed, len(hr_bytes))
    if rejection is not None:
        raise ValueError(str(rejection["reason"]))
    durable_write_text(
        house_rules,
        trimmed,
        expected_pre_image=expected_house_rules_sha,
    )
    fixture_pre = (
        hashlib.sha256(fixture.read_bytes()).hexdigest() if fixture.is_file() else None
    )
    durable_write_text(fixture, trimmed, expected_pre_image=fixture_pre)
    return KeepSetTrimReceipts(
        archive_written=archive_written,
        house_rules_written=True,
        fixture_written=True,
    )
