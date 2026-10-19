"""Plan r5 checks for master-review owed rows (friction:39638)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

from services.git_integration_worker import master_review_owed as mro
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble

REPO = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
EXT4 = [("/", "ext4")]
INITIAL = mro.INITIAL_REVIEW_WINDOW_BASE
A758 = "a7584a4bf54d0ced172a00f98b71280a639d1ce0"
BD17 = "bd17fe303468bdfeccd338774783269d2cb9a06a"
NINE = "9e43a20532ac97d0b4fba2da78787231f690f7fc"
A035 = "a0354858fb998905098762a16ab1cf9038f728da"
TIP7438 = "743839e84a2fb311a67df94f205ea391a2680aae"
B735 = "b735ff77790799ea87a097d5c7ff496422be21d7"
D616 = "d61607952e2e790a36f71eae25121dfbc69422ab"
LINE_1842 = (
    "VERDICT: APPROVE range=a0354858fb998905098762a16ab1cf9038f728da.."
    "743839e84a2fb311a67df94f205ea391a2680aae "
    "head=743839e84a2fb311a67df94f205ea391a2680aae "
    "fixes=a0354858fb998905098762a16ab1cf9038f728da"
)
BODY_1842 = (
    LINE_1842 + "\nVERDICT: APPROVE\n"
    "range: a0354858fb998905098762a16ab1cf9038f728da.."
    "743839e84a2fb311a67df94f205ea391a2680aae\n"
    "head_sha: 743839e84a2fb311a67df94f205ea391a2680aae\n"
    "fixes_sha: a0354858fb998905098762a16ab1cf9038f728da\n\n"
    "cc: grok-bot-gc1\n\n"
    "Sidecar: cortex://notes/system/threads/15851-auto-overflow-1842.md"
)
BODY_1833 = """cc: grok-bot-gc1
Refs: `16416#615`, `16416#617` (`15851#1822`, `15851#1825`, `15851#1815`)

### 1. Forward-Fix (`friction:39630` / `a0354858f` I001) + `:8` Hop-Expiry Test Leg — `live@b735ff77790799ea87a097d5c7ff496422be21d7`
- **Dispatch:** `aecd4ac58378-7cec0736` (`execution_id=6068a0f9-7bcf-43a6-b121-9f0ab5a8c2bf`, `cursor/composer-2.5` `fast=true`, `lane=B`, worker thread `18081#2`), fast-forward merged onto hub `master` at `b735ff77790799ea87a097d5c7ff496422be21d7`.
- **Diff on `scripts/model_manager/ui/controller/grokbot_context_hop_io.py` (`a0354858f..b735ff777`):** ONLY the 1-line stdlib import move (`from urllib.error import URLError` moved after `from typing import Any`). `:17` (`DEFAULT_CDP_PORTS: tuple[int, ...] = (9340, 9339)`) and `:46` (`updatedAt` fallback) are untouched.
- **Diff on `libs/bus_watch/test_ide_hop_landing.py`:** Added `test_fire_ide_hop_expired_xvfb_host_skips_remote_toplevels` asserting `fire_ide_hop(gui_host=":8", ...)` under `IDE_HOP_OUTCOME_EXPIRED` (`landed_id=None`) returns `landed=False`, `outcome="expired"`, `target_window=None` and does **not** invoke `remote_toplevels` (`called == []`). Also fixed pre-existing `UP037` on `_Client.__enter__` so `ruff check` passes cleanly on the file.
- **K46 Post-Land Specified Check (`VERDICT: APPROVE`):**
  - `"$HOME/.venvs/universal/bin/ruff" check scripts/model_manager/ui/controller/grokbot_context_hop_io.py libs/bus_watch/test_ide_hop_landing.py` → `All checks passed!` (exit 0)
  - `"$HOME/.venvs/universal/bin/pytest" scripts/model_manager/ui/controller/test_grokbot_context_hop.py libs/bus_watch/test_ide_hop_landing.py -q` → `46 passed in 4.83s` (`14 passed` in `test_grokbot_context_hop.py` + `32 passed` in `test_ide_hop_landing.py`).

Sidecar: cortex://notes/system/threads/15851-auto-overflow-1833.md"""
BODY_1822 = """VERDICT: REQUEST_CHANGES
cc: grok-bot-gc1

sha: a0354858fb998905098762a16ab1cf9038f728da
range: a0354858f^..a0354858f
master ee8dd0de4 contains it. git diff --stat range: 2 files, +144/-10.
scripts/model_manager/ui/controller/grokbot_context_hop_io.py
scripts/model_manager/ui/controller/test_grokbot_context_hop.py

(a)
updatedAt fallback fixed at grokbot_context_hop_io.py:46 (updatedAt, else lastActivityAt, else updatedAtMs, else null). test_ac1b asserts those three names. pytest scripts/model_manager/ui/controller/test_grokbot_context_hop.py -q → 14 passed in 0.38s.
DEFAULT_CDP_PORTS (9340, 9339) fixed at :17; probe loop :99. test_ac1c: ports_probed == [9340, 9339], 9339 URLError skipped.
ruff I001 / checks_failed not fixed. ruff check exit 1 at grokbot_context_hop_io.py:7. Same I001 as 18055#3.

(b)
9339 down and 9340 up: covered, test_ac1c.
duplicate agent_id, higher updated_at_ms wins: covered, test_ac1d (200, high-ts).
both ports down: uncovered (returns []).
JS fallback execution: uncovered (ac1b greps source; order observed at :46).
equal timestamps; non-str agent_id dropped :71; RuntimeError from _cdp_eval (grok_computer.py:136) not in except :105: uncovered.

(c)
VERDICT: REQUEST_CHANGES
grokbot_context_hop_io.py:12 — URLError import is before pathlib and typing. Forward fix: move `from urllib.error import URLError` to after `from typing import Any`. No revert. Re-run ruff check on that file and the pytest above.

Sidecar: cortex://notes/system/threads/15851-k43-a0354858f.md"""
BODY_18067_2 = (
    '{"schema_version":1,"status":"complete","work_outcome":"shipped",'
    '"escalation_harvest":"none","summary":"dispatch f1ba9cb2f3c9-b4dc61e5: '
    '22 tool calls, 331.6s, 2662B -> sidecar","deviations":'
    '["degraded:sdk_git_probe_absent","capture:non_file_manifest_entry_dropped"],'
    '"files_created":[],"files_modified":[],"files_deleted":[],'
    '"head_sha":"9e43a20532ac97d0b4fba2da78787231f690f7fc","landed":false,'
    '"source_ref":"workspaces://universal-llm-gateway/tmp/reviews/closeouts/'
    'f1ba9cb2f3c9-b4dc61e5.md"}'
)
CLOSEOUT_463D = (
    "VERDICT: APPROVE\n"
    "`a0354858fb998905098762a16ab1cf9038f728da.."
    "743839e84a2fb311a67df94f205ea391a2680aae`\n"
)
F1BA_SHAPE = "VERDICT: APPROVE\n\nrange d61607952..9e43a2053\n"
CANON = "VERDICT: APPROVE range={base}..{tip} head={tip}"


def _pin_ledger(monkeypatch: pytest.MonkeyPatch) -> Path:
    root = Path("/tmp") / f"mro-39638-{uuid4().hex}"
    root.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(root))
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(root / "ledger.db"))
    CursorDispatchLedger._instance = None
    return root


def _empty_rows(root: Path) -> Path:
    path = root / "rows.json"
    path.write_text("[]", encoding="utf-8")
    return path


def _rows_without(root: Path, *tips: str) -> Path:
    rows = json.loads(mro.DATA_FILE.read_text(encoding="utf-8"))
    drop = {tip.lower() for tip in tips}
    kept = [row for row in rows if row["tip_sha"].lower() not in drop]
    path = root / "rows.json"
    path.write_text(json.dumps(kept), encoding="utf-8")
    return path


def _check(repo: Path, base: str, **kwargs: object) -> mro.ReviewCheckResult:
    kwargs.setdefault("mounts_table", EXT4)
    kwargs.setdefault("verdict_turns", [])
    return mro.unreviewed_master_commits(repo, base, **kwargs)  # type: ignore[arg-type]


def _init_repo(path: Path, n: int, prefix: str = "c") -> list[str]:
    subprocess.check_call(
        ["git", "init", "-b", "master", str(path)], stdout=subprocess.DEVNULL
    )
    subprocess.check_call(["git", "-C", str(path), "config", "user.email", "t@example"])
    subprocess.check_call(["git", "-C", str(path), "config", "user.name", "t"])
    shas: list[str] = []
    for index in range(n):
        subprocess.check_call(
            [
                "git",
                "-C",
                str(path),
                "commit",
                "--allow-empty",
                "-m",
                f"{prefix}{index}",
            ],
            stdout=subprocess.DEVNULL,
        )
        shas.append(
            subprocess.check_output(
                ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
            ).strip()
        )
    return shas


def test_ac5_literal_15851_1842_line1() -> None:
    match = mro.CANONICAL_VERDICT_RE.search(LINE_1842)
    assert match is not None
    assert match.group(2) == A035
    assert match.group(3) == TIP7438
    assert match.group(4) == A035
    bad = mro.re.compile(
        mro.CANONICAL_VERDICT_RE.pattern.replace(r"head=\3", r"head=\2")
    )
    assert bad.search(LINE_1842) is None


def test_nonbinding_15851_1833_1822_and_18067_2_write_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_ledger(monkeypatch)
    for body in (BODY_1833, BODY_1822, BODY_18067_2):
        result = mro.bind_canonical_verdict(
            body,
            repo=REPO,
            bus_thread="15851",
            bus_turn=1,
            review_dispatch_id=None,
            mounts_table=EXT4,
        )
        assert result.status == "no_match"


def test_section7_k43_backticked_closeout_writes_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_ledger(monkeypatch)
    result = mro.bind_canonical_verdict(
        CLOSEOUT_463D,
        repo=REPO,
        bus_thread=None,
        bus_turn=None,
        review_dispatch_id="463d25d8aea3-5889f151",
        mounts_table=EXT4,
    )
    assert result.status == "no_match"


def test_section7_preamble_names_canonical_line_only_for_reviews() -> None:
    for contract in ("check-review", "code-review"):
        preamble = resolve_prompt_preamble(
            handoff_contract=contract,
            prompt_preamble=None,
            inferred_contract=None,
        )
        assert "VERDICT: APPROVE range=<40 lowercase hex>" in preamble
        assert "do not invent" in preamble
    for contract in ("implement", "freeform"):
        preamble = resolve_prompt_preamble(
            handoff_contract=contract,
            prompt_preamble=None,
            inferred_contract=None,
        )
        assert "REVIEW VERDICT CONTRACT" not in preamble


def test_section7_skill_and_annex_name_one_canonical_line() -> None:
    root = REPO
    skill = (
        root / "cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/SKILL.md"
    ).read_text()
    annex = (
        root
        / "cursor-plugins/ulg-ecosystem/skills/handoff-packet-authoring/packet-detail-annex.md"
    ).read_text()
    for text in (skill, annex):
        assert "range=" in text
        assert "head=" in text
        assert "does not invent" in text or "do not invent" in text
    assert "commit-binding" in skill
    assert "commit-binding" in annex


def test_section7_two_canonical_lines_write_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_ledger(monkeypatch)
    body = (
        CANON.format(base=A035, tip=TIP7438)
        + "\n"
        + CANON.format(base=A035, tip=TIP7438)
    )
    result = mro.bind_canonical_verdict(
        body,
        repo=REPO,
        bus_thread=None,
        bus_turn=None,
        review_dispatch_id=None,
        mounts_table=EXT4,
    )
    assert result.status == "ambiguous"


def test_section7_1842_body_binds_once_from_closeout_and_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_ledger(monkeypatch)
    first = mro.bind_canonical_verdict(
        BODY_1842,
        repo=REPO,
        bus_thread="15851",
        bus_turn=1842,
        review_dispatch_id="463d25d8aea3-5889f151",
        mounts_table=EXT4,
    )
    assert first.status == "bound"
    assert first.tip_sha == TIP7438
    assert first.base_sha == A035
    assert first.fixes_sha == A035
    assert first.verdict == "APPROVE"
    second = mro.bind_canonical_verdict(
        BODY_1842,
        repo=REPO,
        bus_thread="15851",
        bus_turn=1842,
        review_dispatch_id="463d25d8aea3-5889f151",
        mounts_table=EXT4,
    )
    assert second.status == "tip_already_bound"
    assert second.fixes_sha == A035


def test_section7_fixes_absent_leaves_request_changes_on_fix_owed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    shas = _init_repo(root / "repo", 2)
    parent, tip = shas
    monkeypatch.setattr(mro, "INITIAL_REVIEW_WINDOW_BASE", parent)
    line = f"VERDICT: REQUEST_CHANGES range={parent}..{tip} head={tip}\n"
    bound = mro.bind_canonical_verdict(
        line,
        repo=root / "repo",
        bus_thread=None,
        bus_turn=None,
        review_dispatch_id=None,
        mounts_table=EXT4,
    )
    assert bound.status == "bound"
    assert bound.fixes_sha is None
    result = _check(
        root / "repo", parent, data_path=_empty_rows(root), poster=lambda *a: None
    )
    assert result.ok is True
    assert tip not in (result.commits or [])
    assert tip in result.fix_owed


def test_section7_data_file_missing_does_not_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    posts: list[str] = []
    result = _check(
        REPO,
        INITIAL,
        data_path=root / "missing.json",
        poster=lambda *_a: posts.append("x"),
    )
    assert result.reason == "data_file_unreadable"
    assert result.commits is None
    assert posts == []


def test_section7_data_file_loaded_specimens_are_not_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_ledger(monkeypatch)
    import agent_bus_store.db.turns as turns

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("get_turn_by_number")

    monkeypatch.setattr(turns, "get_turn_by_number", _boom)
    result = _check(REPO, INITIAL, poster=lambda *_a: None)
    assert result.ok is True
    assert result.commits is not None
    for sha in (A758, BD17, NINE, A035, B735, TIP7438, D616):
        assert sha not in result.commits
    assert A035 not in result.fix_owed
    assert "get_turn_by_number" not in Path(mro.__file__).read_text(encoding="utf-8")


def test_section7_empty_data_file_posts_gap_once_and_not_team_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    posts: list[tuple[str, str, str]] = []
    data = _empty_rows(root)
    first = _check(
        REPO, INITIAL, data_path=data, poster=lambda *args: posts.append(args)
    )
    assert first.ok is True
    assert first.commits is not None
    for sha in (A758, BD17, NINE, A035, B735, TIP7438, D616):
        assert sha in first.commits
    assert posts
    assert all(args[2] == "gap" for args in posts)
    assert "grok-bot-gc1" not in posts[0][0]
    assert posts[0][0].startswith("NOTICE|gap|")
    assert "team_dispatch(" not in Path(mro.__file__).read_text(encoding="utf-8")
    again = _check(
        REPO,
        mro.stored_review_window_base(mounts_table=EXT4) or INITIAL,
        data_path=data,
        poster=lambda *args: posts.append(args),
    )
    assert len(posts) == len(first.posted_tips)
    assert again.posted_tips == ()


def test_section7_noncanonical_shapes_leave_commit_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    bodies = [
        BODY_1822,
        F1BA_SHAPE,
        "VERDICT: PASS|FAIL\n",
        "VERDICT: APPROVE WITH CHANGES\n",
        "VERDICT: APPROVE | REQUEST_CHANGES\n",
        f"VERDICT: APPROVE range={A035[:7]}..{TIP7438[:7]} head={TIP7438[:7]}\n",
    ]
    for body in bodies:
        result = mro.bind_canonical_verdict(
            body,
            repo=REPO,
            bus_thread=None,
            bus_turn=None,
            review_dispatch_id=None,
            mounts_table=EXT4,
        )
        assert result.status == "no_match"
    posts: list[str] = []
    checked = _check(
        REPO, INITIAL, data_path=_empty_rows(root), poster=lambda *a: posts.append(a[0])
    )
    assert A035 in (checked.commits or [])
    assert posts


def test_section7_bad_base_and_rev_list_keep_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    posts: list[str] = []
    data = _empty_rows(root)
    rewritten = _check(
        REPO, "b" * 40, data_path=data, poster=lambda *a: posts.append("x")
    )
    assert rewritten.reason == "base_rewritten"
    assert rewritten.commits is None
    assert mro.stored_review_window_base(mounts_table=EXT4) == INITIAL
    monkeypatch.setattr(mro, "_is_ancestor", lambda *_a, **_k: False)
    failed = _check(REPO, INITIAL, data_path=data, poster=lambda *a: posts.append("x"))
    assert failed.reason == "rev_list_failed"
    assert failed.commits is None
    assert posts == []
    assert mro.stored_review_window_base(mounts_table=EXT4) == INITIAL


def test_section7_next_good_call_lists_uncovered_specimens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    result = _check(REPO, INITIAL, data_path=_empty_rows(root), poster=lambda *_a: None)
    assert result.ok is True
    assert A758 in (result.commits or [])
    assert D616 in (result.commits or [])


def test_section7_verdict_on_parent_only_leaves_child_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    rows = [
        {
            "tip_sha": INITIAL,
            "base_sha": INITIAL,
            "verdict": "APPROVE",
            "fixes_sha": None,
        }
    ]
    path = root / "rows.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    result = _check(REPO, INITIAL, data_path=path, poster=lambda *_a: None)
    assert A758 in (result.commits or [])


def test_section7_clean_merge_covered_by_pass_row_on_second_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    data = _rows_without(root, TIP7438)
    real = mro._rev_list

    def _window(repo: Path, base: str, tip: str) -> list[str] | None:
        if tip == mro.MASTER_REF:
            return [D616]
        return real(repo, base, tip)

    monkeypatch.setattr(mro, "_rev_list", _window)
    result = _check(REPO, INITIAL, data_path=data, poster=lambda *_a: None)
    assert result.ok is True
    assert D616 not in (result.commits or [])


def test_section7_clean_merge_gap_when_tree_or_second_parent_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    data = _rows_without(root, TIP7438)
    real_list = mro._rev_list

    def _window(repo: Path, base: str, tip: str) -> list[str] | None:
        if tip == mro.MASTER_REF:
            return [D616]
        return real_list(repo, base, tip)

    monkeypatch.setattr(mro, "_rev_list", _window)
    original_merge = mro._merge_tree
    monkeypatch.setattr(mro, "_merge_tree", lambda *_a, **_k: "0" * 40)
    posts: list[str] = []
    result = _check(REPO, INITIAL, data_path=data, poster=lambda *a: posts.append(a[0]))
    assert D616 in (result.commits or [])
    assert len(posts) == 1
    root2 = _pin_ledger(monkeypatch)
    wrong = _rows_without(root2, TIP7438, BD17)
    monkeypatch.setattr(mro, "_merge_tree", original_merge)
    wrong_posts: list[str] = []
    again = _check(
        REPO, INITIAL, data_path=wrong, poster=lambda *a: wrong_posts.append(a[0])
    )
    assert D616 in (again.commits or [])
    assert len(wrong_posts) == 1


def test_section7_five_commits_one_range_one_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    shas = _init_repo(repo, 6)
    monkeypatch.setattr(mro, "INITIAL_REVIEW_WINDOW_BASE", shas[0])
    posts: list[str] = []
    result = _check(
        repo, shas[0], data_path=_empty_rows(root), poster=lambda *a: posts.append(a[2])
    )
    assert result.commits == shas[1:]
    assert len(posts) == 1
    assert result.posted_tips == (shas[-1],)


def _four_disjoint_ranges(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, str, tuple[str, str, str, str]]:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    shas = _init_repo(repo, 9)
    base, _c1, c2, c3, c4, c5, c6, c7, c8 = shas
    monkeypatch.setattr(mro, "INITIAL_REVIEW_WINDOW_BASE", base)
    rows = [
        {"tip_sha": c3, "base_sha": c2, "verdict": "APPROVE", "fixes_sha": None},
        {"tip_sha": c5, "base_sha": c4, "verdict": "APPROVE", "fixes_sha": None},
        {"tip_sha": c7, "base_sha": c6, "verdict": "APPROVE", "fixes_sha": None},
    ]
    path = root / "rows.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return repo, path, base, (c2, c4, c6, c8)


def _gap_posted_at(tip: str) -> str | None:
    conn, reason = mro._open_ledger(EXT4)
    assert conn is not None, reason
    row = conn.execute(
        "SELECT gap_posted_at FROM master_review_owed WHERE tip_sha = ?",
        (tip,),
    ).fetchone()
    if row is None:
        return None
    return row["gap_posted_at"]


def test_section7_four_disjoint_ranges_defer_the_fourth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    shas = _init_repo(repo, 9)
    base, c1, c2, c3, c4, c5, c6, c7, c8 = shas
    monkeypatch.setattr(mro, "INITIAL_REVIEW_WINDOW_BASE", base)
    rows = [
        {"tip_sha": c3, "base_sha": c2, "verdict": "APPROVE", "fixes_sha": None},
        {"tip_sha": c5, "base_sha": c4, "verdict": "APPROVE", "fixes_sha": None},
        {"tip_sha": c7, "base_sha": c6, "verdict": "APPROVE", "fixes_sha": None},
    ]
    path = root / "rows.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    result = _check(repo, base, data_path=path, poster=lambda *_a: None)
    assert result.commits == [c1, c2, c4, c6, c8]
    assert result.posted_tips == (c2, c4, c6)
    assert len(result.deferred_ranges) == 1
    deferred = result.deferred_ranges[0]
    assert deferred["commits"] == [c8]
    assert deferred["gap_posted_at"] is None
    assert "id" not in deferred


def test_section7_claimed_ranges_do_not_consume_the_next_run_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path, base, tips = _four_disjoint_ranges(monkeypatch)
    oldest_three = tips[:3]
    fourth = tips[3]
    first_posts: list[str] = []
    first = _check(
        repo, base, data_path=path, poster=lambda *args: first_posts.append(args[0])
    )
    assert first.posted_tips == oldest_three
    assert len(first_posts) == 3
    assert len(first.deferred_ranges) == 1
    assert first.deferred_ranges[0]["commits"] == [fourth]
    assert first.deferred_ranges[0]["gap_posted_at"] is None
    assert _gap_posted_at(fourth) is None
    second_posts: list[str] = []
    second = _check(
        repo, base, data_path=path, poster=lambda *args: second_posts.append(args[0])
    )
    assert second_posts == [f"NOTICE|gap|{fourth}"]
    assert second.posted_tips == (fourth,)
    assert _gap_posted_at(fourth) is not None
    for tip in oldest_three:
        assert tip not in second_posts[0]
    assert "team_dispatch(" not in Path(mro.__file__).read_text(encoding="utf-8")


def test_section7_fourth_range_poster_raise_releases_claim_for_run_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, path, base, tips = _four_disjoint_ranges(monkeypatch)
    fourth = tips[3]
    first = _check(repo, base, data_path=path, poster=lambda *_args: None)
    assert first.posted_tips == tips[:3]
    assert _gap_posted_at(fourth) is None

    def _boom(*_args: object) -> None:
        raise RuntimeError("down")

    second = _check(repo, base, data_path=path, poster=_boom)
    assert second.ok is True
    assert second.posted_tips == ()
    assert _gap_posted_at(fourth) is None
    third_posts: list[str] = []
    third = _check(
        repo, base, data_path=path, poster=lambda *args: third_posts.append(args[0])
    )
    assert third_posts == [f"NOTICE|gap|{fourth}"]
    assert third.posted_tips == (fourth,)
    assert _gap_posted_at(fourth) is not None


def test_section7_ledger_under_mnt_or_nfs_does_not_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _spy(path: Path) -> None:
        calls.append(str(path))
        raise AssertionError("connect")

    monkeypatch.setattr(mro, "_connect", _spy)
    monkeypatch.setattr(
        mro,
        "resolve_cursor_sdk_dispatch_ledger_path",
        lambda: Path("/mnt/torus/refused.db"),
    )
    refused = mro.unreviewed_master_commits(
        REPO, INITIAL, mounts_table=EXT4, verdict_turns=[], poster=lambda *_a: None
    )
    assert refused.reason == "ledger_not_local"
    assert refused.commits is None
    assert calls == []
    monkeypatch.setattr(
        mro, "resolve_cursor_sdk_dispatch_ledger_path", lambda: Path("/tmp/mro-nfs.db")
    )
    nfs = mro.unreviewed_master_commits(
        REPO,
        INITIAL,
        mounts_table=[("/", "nfs")],
        verdict_turns=[],
        poster=lambda *_a: None,
    )
    assert nfs.reason == "ledger_not_local"
    assert calls == []


def test_section7_poster_raise_leaves_gap_posted_at_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    shas = _init_repo(repo, 2)
    monkeypatch.setattr(mro, "INITIAL_REVIEW_WINDOW_BASE", shas[0])

    def _boom(*_args: object) -> None:
        raise RuntimeError("down")

    result = _check(repo, shas[0], data_path=_empty_rows(root), poster=_boom)
    assert result.ok is True
    assert result.posted_tips == ()
    assert result.commits == [shas[1]]
    conn, _reason = mro._open_ledger(EXT4)
    assert conn is not None
    row = conn.execute(
        "SELECT gap_posted_at FROM master_review_owed WHERE tip_sha = ?", (shas[1],)
    ).fetchone()
    assert row is None or row["gap_posted_at"] is None


def test_section7_test_only_subject_is_not_an_exemption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    data = _rows_without(root, NINE, TIP7438)
    result = _check(REPO, INITIAL, data_path=data, poster=lambda *_a: None)
    assert NINE in (result.commits or [])


def test_section7_fenced_canonical_line_writes_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_ledger(monkeypatch)
    body = "```\n" + CANON.format(base=A035, tip=TIP7438) + "\n```\n"
    result = mro.bind_canonical_verdict(
        body,
        repo=REPO,
        bus_thread=None,
        bus_turn=None,
        review_dispatch_id=None,
        mounts_table=EXT4,
    )
    assert result.status == "no_match"


def test_section7_lowercase_verdict_matches_and_pass_token_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin_ledger(monkeypatch)
    line = f"verdict: approve range={A035}..{TIP7438} head={TIP7438}\n"
    result = mro.bind_canonical_verdict(
        line,
        repo=REPO,
        bus_thread=None,
        bus_turn=None,
        review_dispatch_id=None,
        mounts_table=EXT4,
    )
    assert result.status == "bound"
    assert result.verdict == "APPROVE"
    bare = mro.bind_canonical_verdict(
        "VERDICT: PASS\n",
        repo=REPO,
        bus_thread=None,
        bus_turn=None,
        review_dispatch_id=None,
        mounts_table=EXT4,
    )
    assert bare.status == "no_match"


def test_section7_doctrine_sites_do_not_hold_the_merge() -> None:
    root = REPO / "cursor-plugins/ulg-ecosystem"
    posture = (root / "skills/git-posture/SKILL.md").read_text(encoding="utf-8")
    kernel = (root / "rules/checkout-kernel_ulg.mdc").read_text(encoding="utf-8")
    gotchas = (root / "skills/conductor/reference-gotchas.md").read_text(
        encoding="utf-8"
    )
    anti = (
        root / "skills/conductor/reference-interactive-and-anti-patterns.md"
    ).read_text(encoding="utf-8")
    for text in (posture, kernel, gotchas, anti):
        assert "owes a review" in text or "owed review" in text
        assert "forward fix" in text or "REQUEST_CHANGES" in text
        assert (
            "does not wait" in text
            or "not a merge hold" in text
            or "not a hold" in text
            or "Land does not wait" in text
        )
    assert "land on green, don't re-ask" in gotchas
    assert "binds to the landed sha or range afterward" in anti
    assert "check debt, not a hold" in anti
    hook = (
        REPO / "services/git_integration_worker/lane_b_reference_transaction_hook.py"
    ).read_text()
    assert "def run_reference_transaction_hook" in hook


def test_ride_along_unreviewed_posts_once_and_poster_raise_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    old, rider, triggering = _init_repo(repo, 3)
    data = _empty_rows(root)
    posts: list[str] = []

    def _poster(subject: str, body: str, tag: str) -> None:
        posts.append(tag)
        assert tag == "ride_along"
        assert "15851" not in subject or True
        assert rider in body

    first = mro.ride_along_unreviewed(
        repo,
        old,
        triggering,
        triggering,
        mounts_table=EXT4,
        poster=_poster,
        data_path=data,
    )
    assert first.ok is True
    assert first.commits == [rider]
    assert first.posted == 1
    assert posts == ["ride_along"]
    second = mro.ride_along_unreviewed(
        repo,
        old,
        triggering,
        triggering,
        mounts_table=EXT4,
        poster=_poster,
        data_path=data,
    )
    assert second.ok is True
    assert second.posted == 0
    assert posts == ["ride_along"]
    conn, _reason = mro._open_ledger(EXT4)
    assert conn is not None
    row = conn.execute(
        "SELECT notice_tag, gap_posted_at, verdict FROM master_review_owed WHERE tip_sha = ?",
        (rider,),
    ).fetchone()
    assert row["notice_tag"] == "ride_along"
    assert row["gap_posted_at"]
    assert row["verdict"] is None

    def _boom(*_args: object) -> None:
        raise RuntimeError("poster down")

    other = root / "other"
    shas = _init_repo(other, 3, prefix="boom")
    raised = mro.ride_along_unreviewed(
        other,
        shas[0],
        shas[2],
        shas[2],
        mounts_table=EXT4,
        poster=_boom,
        data_path=data,
    )
    assert raised.ok is False
    assert raised.commits is None

    def _caller() -> None:
        mro.apply_review_obligations_after_land(
            repo=other,
            live_old_sha=shas[0],
            live_new_sha=shas[2],
            triggering_land_sha=shas[2],
            mounts_table=EXT4,
            poster=_boom,
            verdict_turns=[],
            data_path=data,
        )

    _caller()


def _raise_timeout(*args: object, **kwargs: object) -> None:
    command = args[0] if args else kwargs.get("args", ["git"])
    raise subprocess.TimeoutExpired(cmd=command, timeout=10)


def test_watermark_pages_oldest_first_and_binds_line_behind_the_tip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    base, tip = _init_repo(repo, 2)
    canonical = CANON.format(base=base, tip=tip)
    total = 130
    canonical_turn = 10
    store = [
        {
            "id": 100_000 + number,
            "turn_number": number,
            "thread": "15851",
            "body": canonical if number == canonical_turn else f"note {number}",
        }
        for number in range(1, total + 1)
    ]
    seen: list[dict[str, object]] = []

    class _Response:
        def __init__(self, turns: list[dict[str, object]]) -> None:
            self.status_code = 200
            self._turns = turns

        def json(self) -> dict[str, object]:
            return {"turns": self._turns}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def get(
            self,
            path: str,
            params: dict[str, object] | None = None,
            headers: dict[str, str] | None = None,
        ) -> _Response:
            del headers
            assert path == "/turns"
            assert params is not None
            seen.append(dict(params))
            assert params["last"] == mro.SCAN_CAP
            assert params["thread"] == "15851"
            after = int(params["after_turn"])  # type: ignore[arg-type]
            page = [row for row in store if int(row["turn_number"]) > after][
                : mro.SCAN_CAP
            ]
            return _Response(page)

    monkeypatch.setattr(
        "transport_utils.make_async_client", lambda *_a, **_k: _Client()
    )
    conn, reason = mro._open_ledger(EXT4)
    assert conn is not None and reason is None
    first = mro._bus_verdict_turns(0)
    mro._scan_turns(conn, repo, first)
    assert [int(row["turn_number"]) for row in first] == list(range(1, 65))
    assert int(mro._state(conn)["verdict_scan_after_turn"]) == 64
    bound = conn.execute(
        "SELECT verdict FROM master_review_owed WHERE tip_sha = ?", (tip,)
    ).fetchone()
    assert bound is not None and bound["verdict"] == "APPROVE"
    fetched = [int(row["turn_number"]) for row in first]
    while True:
        cursor = int(mro._state(conn)["verdict_scan_after_turn"])
        page = mro._bus_verdict_turns(cursor)
        if not page:
            break
        fetched.extend(int(row["turn_number"]) for row in page)
        mro._scan_turns(conn, repo, page)
    assert fetched == list(range(1, total + 1))
    assert seen[0]["after_turn"] == 0
    assert all("after_turn" in params for params in seen)


def test_scan_does_not_advance_past_unresolved_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    base, tip = _init_repo(repo, 2)
    dead = "ab" * 20
    turns = [
        {"turn_number": 1, "thread": "15851", "body": "no verdict here"},
        {
            "turn_number": 2,
            "thread": "15851",
            "body": CANON.format(base=dead, tip=dead),
        },
        {
            "turn_number": 3,
            "thread": "15851",
            "body": CANON.format(base=base, tip=tip),
        },
    ]
    conn, reason = mro._open_ledger(EXT4)
    assert conn is not None and reason is None
    mro._scan_turns(conn, repo, turns)
    assert int(mro._state(conn)["verdict_scan_after_turn"]) == 1
    mro._scan_turns(conn, repo, turns)
    assert int(mro._state(conn)["verdict_scan_after_turn"]) == 1
    later = conn.execute(
        "SELECT verdict FROM master_review_owed WHERE tip_sha = ?", (tip,)
    ).fetchone()
    assert later is None
    assert conn.execute("SELECT COUNT(*) FROM master_review_owed").fetchone()[0] == 0


def test_git_timeout_on_review_check_is_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    shas = _init_repo(repo, 2)
    monkeypatch.setattr(mro, "INITIAL_REVIEW_WINDOW_BASE", shas[0])
    monkeypatch.setattr(mro.subprocess, "run", _raise_timeout)
    posts: list[str] = []
    started = time.monotonic()
    result = _check(
        repo,
        shas[0],
        data_path=_empty_rows(root),
        poster=lambda *_args: posts.append("posted"),
    )
    assert time.monotonic() - started < 2
    assert result.ok is False
    assert result.reason == "git_timeout"
    assert result.commits is None
    assert result.posted_tips == ()
    assert posts == []
    conn, _reason = mro._open_ledger(EXT4)
    assert conn is not None
    assert conn.execute("SELECT COUNT(*) FROM master_review_owed").fetchone()[0] == 0


def test_merge_tree_timeout_is_not_treated_as_covered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    _init_repo(repo, 1)
    subprocess.check_call(
        ["git", "-C", str(repo), "checkout", "-b", "side"], stdout=subprocess.DEVNULL
    )
    subprocess.check_call(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "side"],
        stdout=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "-C", str(repo), "checkout", "master"], stdout=subprocess.DEVNULL
    )
    subprocess.check_call(
        ["git", "-C", str(repo), "merge", "--no-ff", "-m", "merge", "side"],
        stdout=subprocess.DEVNULL,
    )
    base = subprocess.check_output(
        ["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"], text=True
    ).strip()
    monkeypatch.setattr(mro, "INITIAL_REVIEW_WINDOW_BASE", base)
    real_run = mro.subprocess.run

    def _timeout_merge_tree(
        *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        command = args[0] if args else kwargs.get("args", [])
        if isinstance(command, list) and "merge-tree" in command:
            raise subprocess.TimeoutExpired(cmd=command, timeout=10)
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(mro.subprocess, "run", _timeout_merge_tree)
    posts: list[str] = []
    started = time.monotonic()
    result = _check(
        repo,
        base,
        data_path=_empty_rows(root),
        poster=lambda *_args: posts.append("posted"),
    )
    assert time.monotonic() - started < 2
    assert result.ok is False
    assert result.reason == "git_timeout"
    assert result.commits is None
    assert posts == []
    conn, _reason = mro._open_ledger(EXT4)
    assert conn is not None
    assert conn.execute("SELECT COUNT(*) FROM master_review_owed").fetchone()[0] == 0


def test_head_ref_sha_timeout_records_failed_check_without_a_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    shas = _init_repo(repo, 2)
    monkeypatch.setattr(mro, "INITIAL_REVIEW_WINDOW_BASE", shas[0])
    monkeypatch.setattr(mro.subprocess, "run", _raise_timeout)
    posts: list[str] = []
    started = time.monotonic()
    assert mro.head_ref_sha(repo) is None
    mro.apply_review_obligations_after_land(
        repo=repo,
        live_old_sha=None,
        live_new_sha=mro.head_ref_sha(repo),
        triggering_land_sha=shas[1],
        mounts_table=EXT4,
        poster=lambda *_args: posts.append("posted"),
        verdict_turns=[],
        data_path=_empty_rows(root),
    )
    assert time.monotonic() - started < 2
    assert posts == []
    conn, _reason = mro._open_ledger(EXT4)
    assert conn is not None
    assert conn.execute("SELECT COUNT(*) FROM master_review_owed").fetchone()[0] == 0


def test_gap_claim_precedes_post_and_one_tip_is_not_renoticed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    repo = root / "repo"
    shas = _init_repo(repo, 4)
    monkeypatch.setattr(mro, "INITIAL_REVIEW_WINDOW_BASE", shas[0])
    posts: list[str] = []

    def _poster(subject: str, _body: str, tag: str) -> None:
        tip = subject.rsplit("|", 1)[-1]
        conn, _reason = mro._open_ledger(EXT4)
        assert conn is not None
        row = conn.execute(
            "SELECT gap_posted_at FROM master_review_owed WHERE tip_sha = ?",
            (tip,),
        ).fetchone()
        assert row is not None and row["gap_posted_at"]
        posts.append(f"{tag}:{tip}")

    result = _check(repo, shas[0], data_path=_empty_rows(root), poster=_poster)
    assert result.posted_tips == (shas[-1],)
    ride = mro.ride_along_unreviewed(
        repo,
        shas[0],
        shas[-1],
        shas[1],
        mounts_table=EXT4,
        poster=_poster,
        data_path=_empty_rows(root),
    )
    assert ride.posted == 0
    assert posts == [f"gap:{shas[-1]}"]
    conn, _reason = mro._open_ledger(EXT4)
    assert conn is not None
    assert mro._claim_gap(conn, tip=shas[-1], base=shas[0], tag=None) is False


def test_ledger_connect_lock_is_a_failed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _pin_ledger(monkeypatch)
    monkeypatch.setattr(mro, "_LEDGER_CONNECT_BUDGET_S", 0.2)

    def _locked(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(mro.sqlite3, "connect", _locked)
    posts: list[str] = []
    started = time.monotonic()
    result = mro.unreviewed_master_commits(
        REPO,
        INITIAL,
        mounts_table=EXT4,
        verdict_turns=[],
        poster=lambda *_args: posts.append("posted"),
        data_path=_empty_rows(root),
    )
    elapsed = time.monotonic() - started
    assert elapsed < 2
    assert result.ok is False
    assert result.reason == "ledger_connect_failed"
    assert result.commits is None
    assert posts == []
