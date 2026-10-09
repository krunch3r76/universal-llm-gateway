"""Hermetic tests for inspect-window dump waits (friction 39027)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.container_skills_dump import (
    PlaywrightTimeoutError as DumpTimeout,
)
from claude_bundles.container_skills_dump import (
    _click_chat_zip_card,
    _fresh_chat,
    _ZipHarvest,
    download_skills_zip,
)
from claude_bundles.liveness_wait import (
    LivenessError,
    LivenessStalledError,
    liveness_wait,
)

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


@pytest.mark.asyncio
async def test_consecutive_expiries_with_progress_then_complete() -> None:
    """Several window expiries that each see progress, then completion, succeed."""
    clock = _Clock()
    values = [0, 1, 2, 3]

    async def progress() -> int:
        return values.pop(0)

    async def complete() -> bool:
        return not values

    async def error():
        return None

    await liveness_wait(
        progress=progress,
        complete=complete,
        error=error,
        window_s=10,
        poll_s=10,
        phase="probe",
        on_progress=lambda _sig: None,
        clock=clock,
        sleep=clock.sleep,
    )
    assert clock.slept == [10, 10, 10]
    assert values == []


@pytest.mark.asyncio
async def test_expiry_with_no_progress_is_stalled() -> None:
    clock = _Clock()

    async def progress() -> int:
        return 1

    async def complete() -> bool:
        return False

    with pytest.raises(LivenessStalledError) as raised:
        await liveness_wait(
            progress=progress,
            complete=complete,
            window_s=10,
            poll_s=10,
            phase="navigate",
            on_progress=lambda _sig: None,
            clock=clock,
            sleep=clock.sleep,
        )
    assert raised.value.phase == "navigate"
    assert clock.slept == [10]


@pytest.mark.asyncio
async def test_explicit_error_fails_before_expiry() -> None:
    clock = _Clock()

    async def progress() -> int:
        return 1

    async def complete() -> bool:
        return False

    async def error():
        return "banner: overloaded"

    async def boom(_seconds: float) -> None:
        raise AssertionError("slept before error")

    with pytest.raises(LivenessError) as raised:
        await liveness_wait(
            progress=progress,
            complete=complete,
            error=error,
            window_s=10,
            poll_s=10,
            phase="download",
            clock=clock,
            sleep=boom,
        )
    assert raised.value.phase == "download"
    assert "overloaded" in str(raised.value)
    assert clock.t == 0.0


def _page_ready() -> AsyncMock:
    page = AsyncMock()
    page.url = "https://claude.ai/new"
    page.goto = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(
        return_value={
            "ready": "complete",
            "dom": 20,
            "resources": 1,
            "url": "https://claude.ai/new",
        }
    )
    page.bring_to_front = AsyncMock()
    locator = AsyncMock()
    locator.count = AsyncMock(return_value=1)
    page.locator = MagicMock(return_value=locator)
    return page


@pytest.mark.asyncio
async def test_fresh_chat_navigates_under_liveness_loop() -> None:
    """Dump navigation must not pass Playwright's 30s goto deadline."""
    page = _page_ready()
    ctx = AsyncMock()
    ctx.new_page = AsyncMock(return_value=page)
    await _fresh_chat(ctx)
    assert page.goto.call_count == 1
    assert page.goto.call_args.args[0] == "https://claude.ai/new"
    assert page.goto.call_args.kwargs["wait_until"] == "commit"
    assert page.goto.call_args.kwargs["timeout"] == 0


@pytest.mark.asyncio
async def test_download_path_uses_liveness_loop(tmp_path: Path) -> None:
    page = AsyncMock()
    out = tmp_path / "claude-skills.zip"
    seen: dict[str, str] = {}

    async def fake_wait(**kwargs):
        seen["phase"] = kwargs["phase"]
        partial = out.with_name(out.name + ".partial")
        with zipfile.ZipFile(partial, "w") as zf:
            zf.writestr("skills/user/fs/SKILL.md", b"# fs\n")

    with patch(
        "claude_bundles.container_skills_dump.liveness_wait",
        new=fake_wait,
    ):
        result = await download_skills_zip(page, out)
    assert result == out
    assert seen["phase"] == "download"
    page.wait_for_timeout.assert_not_called()


@pytest.mark.asyncio
async def test_zip_card_expect_download_has_no_fixed_deadline(tmp_path: Path) -> None:
    page = AsyncMock()
    page.url = "https://claude.ai/chat/1"
    timeouts: list[int] = []

    class _Info:
        def __init__(self) -> None:
            download = AsyncMock()
            download.save_as = AsyncMock()

            async def _value():
                return download

            self.value = _value()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

    def _expect_download(*, timeout: int = 30_000):
        timeouts.append(timeout)
        return _Info()

    element = AsyncMock()
    element.inner_text = AsyncMock(return_value="Download Claude skills")
    element.get_attribute = AsyncMock(return_value=None)
    element.evaluate = AsyncMock()
    element.first = element

    buttons = AsyncMock()
    buttons.count = AsyncMock(return_value=1)
    buttons.nth = MagicMock(return_value=element)

    root = AsyncMock()
    root.count = AsyncMock(return_value=1)
    root.get_by_role = MagicMock(return_value=buttons)

    # get_by_role("button", name=...) returns the clickable control.
    def _role(*_args, **kwargs):
        if "name" in kwargs:
            named = AsyncMock()
            named.count = AsyncMock(return_value=1)
            named.first = element
            return named
        return buttons

    root.get_by_role = MagicMock(side_effect=_role)
    page.locator = MagicMock(return_value=root)
    page.expect_download = MagicMock(side_effect=_expect_download)

    out = tmp_path / "claude-skills.zip"
    await _click_chat_zip_card(page, out)
    assert timeouts == [0]


def _load_dump_script():
    path = _REPO / "scripts" / "cortex" / "dump_claude_container_skills.py"
    spec = importlib.util.spec_from_file_location("dump_claude_container_skills", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dump_cli_terminal_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    module = _load_dump_script()
    zpath = tmp_path / "claude-skills.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("skills/user/fs/SKILL.md", b"# fs\n")

    async def fake_dump(**_kwargs):
        return zpath

    monkeypatch.setattr(module, "dump_container_skills", fake_dump)
    monkeypatch.setattr(sys, "argv", ["dump", "--out", str(zpath)])
    monkeypatch.delenv("CLAUDE_SYNC_NESTED", raising=False)
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "PHASE dump start" in out
    assert "PHASE dump done" in out
    assert "MIRROR_DONE rc=0" in out

    async def stalled(**_kwargs):
        raise LivenessStalledError("quiet", phase="navigate")

    monkeypatch.setattr(module, "dump_container_skills", stalled)
    assert module.main() == 3
    stalled_out = capsys.readouterr().out
    assert "STALLED navigate" in stalled_out
    assert "MIRROR_DONE" not in stalled_out

    async def errored(**_kwargs):
        raise LivenessError("net::ERR_FAILED", phase="navigate")

    monkeypatch.setattr(module, "dump_container_skills", errored)
    assert module.main() == 1
    err_out = capsys.readouterr().out
    assert "MIRROR_ERROR" in err_out
    assert "net::ERR_FAILED" in err_out

    monkeypatch.setenv("CLAUDE_SYNC_NESTED", "1")
    monkeypatch.setattr(module, "dump_container_skills", fake_dump)
    assert module.main() == 0
    nested = capsys.readouterr().out
    assert "PHASE dump done" in nested
    assert "MIRROR_DONE" not in nested


def test_wrapper_heartbeat_is_not_a_failure() -> None:
    """Shell phase_exec prints a heartbeat and still returns the child rc."""
    script = _REPO / "scripts" / "cortex" / "claude-ai-sync-jupiter"
    text = script.read_text(encoding="utf-8")
    start = text.index("emit() {")
    end = text.index("# 24911/24976:")
    functions = text[start:end]
    driver = (
        functions
        + "\n"
        + "CLAUDE_DUMP_LIVENESS_WINDOW_S=1\n"
        + "phase_exec nap sleep 2\n"
        + "printf 'RC=%s\\n' \"$?\"\n"
    )
    import subprocess

    proc = subprocess.run(
        ["bash", "-c", driver],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PHASE nap start" in proc.stdout
    assert "PHASE nap heartbeat" in proc.stdout
    assert "PHASE nap done" in proc.stdout
    assert "RC=0" in proc.stdout


@pytest.mark.asyncio
async def test_stale_zip_is_not_a_finished_download(tmp_path: Path) -> None:
    """A previous mirror zip at the destination is not this harvest."""
    out = tmp_path / "claude-skills.zip"
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("old.md", b"old")
    harvest = _ZipHarvest(AsyncMock(), out)
    assert await harvest.complete() is False

    async def hang(*_args, **_kwargs):
        await asyncio.Event().wait()

    with (
        patch(
            "claude_bundles.container_skills_dump._click_chat_zip_card",
            new=hang,
        ),
        patch(
            "claude_bundles.container_skills_dump._save_cowork_zip",
            new=hang,
        ),
        pytest.raises(asyncio.TimeoutError),
    ):
        await asyncio.wait_for(download_skills_zip(AsyncMock(), out), timeout=0.6)
    with zipfile.ZipFile(out) as zf:
        assert zf.read("old.md") == b"old"


@pytest.mark.asyncio
async def test_click_timeout_falls_through_to_cowork(tmp_path: Path) -> None:
    """A quiet chat click ends its slice and Cowork can still return the zip."""
    out = tmp_path / "skills.zip"
    seen: dict[str, int] = {}

    async def click(_page, _out, *, timeout_ms: int = 0):
        seen["click"] = timeout_ms
        raise DumpTimeout("attempt slice")

    async def save(_page, dest: Path, *, timeout_ms: int = 0):
        seen["save"] = timeout_ms
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("skills/user/x/SKILL.md", b"x")
        return dest

    with (
        patch(
            "claude_bundles.container_skills_dump._click_chat_zip_card",
            new=click,
        ),
        patch(
            "claude_bundles.container_skills_dump._save_cowork_zip",
            new=save,
        ),
    ):
        result = await download_skills_zip(AsyncMock(), out)
    assert result == out
    assert seen["click"] > 0
    assert seen["save"] > 0
    assert zipfile.is_zipfile(out)


def test_remote_py_forwards_nested_flag() -> None:
    """CLAUDE_SYNC_NESTED is inside the remote command, not only the local shell."""
    script = _REPO / "scripts" / "cortex" / "claude-ai-sync-jupiter"
    text = script.read_text(encoding="utf-8")
    start = text.index("remote_py() {")
    end = text.index("# Line-buffered")
    driver = (
        "_cdp_display() { echo :1; }\n"
        "_cdp_xauthority() { echo /tmp/xa; }\n"
        "ssh() { printf '%s\\n' \"$2\"; }\n"
        "SSH=box\nREMOTE_REPO=/repo\nPY=/usr/bin/python\nCDP_PORT=9\n"
        "CLAUDE_SYNC_NESTED=1\n"
        + text[start:end]
        + "\nremote_py scripts/cortex/dump_claude_container_skills.py\n"
    )
    import subprocess

    proc = subprocess.run(
        ["bash", "-c", driver],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert "CLAUDE_SYNC_NESTED=1" in proc.stdout


def test_dump_cli_early_failure_emits_terminal_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Bad flags and a missing prompt file still print MIRROR_ERROR."""
    script = _REPO / "scripts" / "cortex" / "dump_claude_container_skills.py"
    spec = importlib.util.spec_from_file_location("dump_cli_early", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["dump_claude_container_skills.py", "--nope"])
    assert module.main() == 2
    assert "MIRROR_ERROR" in capsys.readouterr().out
    missing = tmp_path / "missing.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["dump_claude_container_skills.py", "--prompt-file", str(missing)],
    )
    assert module.main() == 1
    missing_out = capsys.readouterr().out
    assert "MIRROR_ERROR" in missing_out
    assert "FileNotFoundError" in missing_out
