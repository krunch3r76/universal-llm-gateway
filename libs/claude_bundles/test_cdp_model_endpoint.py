"""Unit tests for CDP model-endpoint adapter + staging."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from claude_bundles.cdp_model_endpoint import (
    CDP_SUBSTRATE,
    SUBMIT_RETRY_BACKOFF_S,
    UPSTREAM_OVERLOADED,
    _has_proof,
    _is_overload_only_harvest,
    picker_from_model_id,
    run_cdp_generate,
)
from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    stage_cdp_prompt_with_skills,
    stage_prompt_uri,
    sweep_ephemeral,
)


def test_picker_from_model_id_passthrough() -> None:
    assert picker_from_model_id("cdp/opus-4.8") == "opus-4.8"
    assert picker_from_model_id("cdp/fable-5") == "fable-5"
    assert picker_from_model_id("cdp/fable") == "fable-5"


def test_stage_prompt_text_writes_ephemeral(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    staged = stage_prompt_uri(execution_id="exec-1", prompt_text="hello CDP")
    assert staged.staged is True
    assert (
        staged.prompt_uri
        == "cortex://notes/system/ephemeral/cdp-endpoint/exec-1/prompt.md"
    )
    on_disk = tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-1/prompt.md"
    assert on_disk.read_text(encoding="utf-8") == "hello CDP"
    assert sweep_ephemeral("exec-1") is True
    assert not on_disk.exists()


def test_stage_cdp_prompt_with_skills_prepends_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-skills",
        prompt_text="## Task\n",
        skills=["reasoning-posture", "consult-posture"],
    )
    on_disk = tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills/prompt.md"
    text = on_disk.read_text(encoding="utf-8")
    assert text.startswith("/reasoning-posture\n/consult-posture\n")
    assert "## Task" in text
    assert staged.prompt_uri.endswith("exec-skills/prompt.md")


def test_stage_cdp_prompt_with_skills_inlines_code_mcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-skills-mixed",
        prompt_text="## Task\n",
        skills=["path-sim", "reasoning-posture"],
    )
    on_disk = tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills-mixed/prompt.md"
    text = on_disk.read_text(encoding="utf-8")
    assert text.startswith("/reasoning-posture\n")
    assert '<skill slug="path-sim"' in text
    assert "## Task" in text
    assert staged.prompt_uri.endswith("exec-skills-mixed/prompt.md")


def test_stage_cortex_passthrough(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    uri = "cortex://notes/system/threads/already.md"
    staged = stage_prompt_uri(execution_id="exec-2", prompt_uri=uri)
    assert staged.staged is False
    assert staged.prompt_uri == uri
    assert staged.ephemeral_root is None


def test_stage_workspaces_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cortex = tmp_path / "cortex"
    ws = tmp_path / "ws" / "universal-llm-gateway"
    cortex.mkdir()
    ws.mkdir(parents=True)
    src = ws / "prompt.md"
    src.write_text("from workspaces", encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex))
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    staged = stage_prompt_uri(
        execution_id="exec-3",
        prompt_uri="workspaces://universal-llm-gateway/prompt.md",
    )
    assert staged.staged is True
    dest = cortex / "notes/system/ephemeral/cdp-endpoint/exec-3/prompt.md"
    assert dest.read_text(encoding="utf-8") == "from workspaces"


def test_stage_missing_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    with pytest.raises(CdpStagingError) as exc:
        stage_prompt_uri(
            execution_id="exec-4",
            prompt_uri="workspaces://universal-llm-gateway/missing.md",
        )
    assert exc.value.code == "cdp_prompt_unstageable"


class _FakeClient:
    """Minimal httpx-shaped client for adapter unit tests."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def request(
        self, method: str, url: str, json: dict[str, Any] | None = None
    ) -> _FakeResp:
        self.calls.append((method, url))
        if method == "POST" and "/abort" in url:
            return _FakeResp({"ok": True, "status": "aborted"})
        if not self._responses:
            raise AssertionError(f"unexpected request {method} {url}")
        payload = self._responses.pop(0)
        return _FakeResp(payload)

    def close(self) -> None:
        return None


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.content = b"{}" if payload else b""
        self.status_code = int(payload.get("_status_code", 200))
        self.text = ""

    def raise_for_status(self) -> None:
        if self._payload.get("_http_error"):
            raise RuntimeError("http error")

    def json(self) -> dict[str, Any]:
        return {k: v for k, v in self._payload.items() if not k.startswith("_")}


def test_run_cdp_generate_proof_before_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient(
        [
            {"execution_id": "sat-1", "status": "running"},
            {
                "execution_id": "sat-1",
                "status": "running",
                "completion_phase": "running",
                "body_len": 0,
            },
            {
                "execution_id": "sat-1",
                "status": "complete",
                "completion_phase": "content_proof",
                "content_proof_uri": "cortex://notes/system/threads/proof.md",
                "content_proof_sha256": "abc",
                "body": "harvested",
                "body_len": 9,
            },
        ]
    )
    sleeps: list[float] = []
    submit_bodies: list[dict[str, Any]] = []

    _orig_request = client.request

    def _capture(method: str, url: str, json: dict[str, Any] | None = None) -> Any:
        if method == "POST" and json is not None:
            submit_bodies.append(json)
        return _orig_request(method, url, json=json)

    client.request = _capture  # type: ignore[method-assign]
    result = run_cdp_generate(
        execution_id="dispatch-1",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        harvest_source="output-file",
        expected_size="large",
        download_output=True,
        client=client,  # type: ignore[arg-type]
        sleep=sleeps.append,
    )
    assert result.ok is True
    assert result.body == "harvested"
    assert result.substrate == CDP_SUBSTRATE
    assert result.cost_source == "unavailable"
    assert result.content_proof_uri is not None
    assert result.picker_model == "opus-4.8"
    # submit + 2 polls
    assert len(client.calls) == 3
    assert submit_bodies[0]["harvest_source"] == "output-file"
    assert submit_bodies[0]["expected_size"] == "large"
    assert submit_bodies[0]["download_output"] is True


def test_run_cdp_generate_stall_wall_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient(
        [
            {"execution_id": "sat-2", "status": "running"},
            {
                "execution_id": "sat-2",
                "status": "running",
                "completion_phase": "running",
                "body_len": 0,
                "liveness_observed_at": "t1",
            },
        ]
    )
    clock = {"t": 0.0}

    def _now() -> float:
        return clock["t"]

    def _sleep(_s: float) -> None:
        # Jump past max_wall_s after first poll interval.
        clock["t"] = 50.0

    result = run_cdp_generate(
        execution_id="dispatch-2",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        max_wall_s=10,
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is False
    assert result.stall_stage == "wall_clock_exceeded"


def test_run_cdp_generate_wall_clock_preserves_archive_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-ok wall_clock_exceeded must retain archive_uri from last status snapshot (B3).

    ``archive_uri`` on a polled snapshot triggers ``ok=True`` via ``_has_proof``,
    so this case carries ``archive_uri`` on the submit ack, polls a running
    snapshot without re-emitting it, then fires wall_clock on the next loop head.
    """
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    archive = "cortex://notes/system/threads/archive-wc.md"
    client = _FakeClient(
        [
            {
                "execution_id": "sat-wc",
                "status": "running",
                "archive_uri": archive,
            },
            {
                "execution_id": "sat-wc",
                "status": "running",
                "completion_phase": "running",
                "body_len": 0,
                "liveness_observed_at": "t1",
            },
        ]
    )
    clock = {"t": 0.0}

    def _now() -> float:
        return clock["t"]

    def _sleep(_s: float) -> None:
        clock["t"] = 50.0

    result = run_cdp_generate(
        execution_id="dispatch-wc",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        max_wall_s=10,
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is False
    assert result.stall_stage == "wall_clock_exceeded"
    assert result.archive_uri == archive


def test_has_proof_archiving_phase_not_failed() -> None:
    """Archiving + content_proof_uri counts; failed phase must not (B4)."""
    proof_uri = "cortex://notes/system/threads/proof-arch.md"
    assert _has_proof(
        {"completion_phase": "archiving", "content_proof_uri": proof_uri}
    )
    assert not _has_proof(
        {"completion_phase": "failed", "content_proof_uri": proof_uri}
    )


def test_run_cdp_generate_stall_no_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    perpetual = {
        "execution_id": "sat-3",
        "status": "running",
        "completion_phase": "running",
        "body_len": 0,
    }
    client = _FakeClient(
        [
            {"execution_id": "sat-3", "status": "running"},
            perpetual,
            perpetual,
            perpetual,
        ]
    )
    # no_progress_s=5; clock advances 6s with identical fingerprints.
    clock = {"t": 0.0}

    def _now() -> float:
        return clock["t"]

    def _sleep(s: float) -> None:
        clock["t"] += max(s, 6.0)

    result = run_cdp_generate(
        execution_id="dispatch-3",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        max_wall_s=1800,
        no_progress_s=5,
        poll_interval_s=1,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is False
    assert result.stall_stage == "no_progress"


def test_run_cdp_generate_no_progress_exempt_post_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A frozen fingerprint at ``turn_idle`` must not trip the no-progress abort.

    Post-idle the satellite is resolving harvest and emits no per-sample signal,
    so the watchdog cannot distinguish that from a hang — ``max_wall_s`` is the
    only honest bound there (friction a:26175).
    """
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    idle = {
        "execution_id": "sat-4",
        "status": "running",
        "completion_phase": "turn_idle",
        "body_len": 0,
    }
    proof = {
        "execution_id": "sat-4",
        "status": "running",
        "completion_phase": "content_proof",
        "content_proof_uri": "cortex://notes/system/threads/proof.md",
        "body": "late harvest",
    }
    client = _FakeClient(
        [{"execution_id": "sat-4", "status": "running"}, idle, idle, idle, proof]
    )
    clock = {"t": 0.0}

    def _now() -> float:
        return clock["t"]

    def _sleep(s: float) -> None:
        clock["t"] += max(s, 6.0)

    result = run_cdp_generate(
        execution_id="dispatch-4",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        max_wall_s=1800,
        no_progress_s=5,
        poll_interval_s=1,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is True
    assert result.body == "late harvest"
    assert result.stall_stage is None


def test_run_cdp_generate_reports_satellite_id_at_submit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``on_submitted`` fires before polling so the id is discoverable in flight."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    seen: list[str] = []
    client = _FakeClient(
        [
            {"execution_id": "sat-5", "status": "running"},
            {
                "execution_id": "sat-5",
                "status": "running",
                "archive_uri": "cortex://notes/system/threads/archive.md",
                "body": "done",
            },
        ]
    )

    def _on_submitted(satellite_execution_id: str) -> None:
        # Recorded against the poll count to prove ordering, not just arrival.
        seen.append(f"{satellite_execution_id}@{len(client.calls)}")

    result = run_cdp_generate(
        execution_id="dispatch-5",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
        on_submitted=_on_submitted,
    )
    assert seen == ["sat-5@1"]
    assert result.satellite_execution_id == "sat-5"


_OVERLOAD_ONLY_BODY = (
    "Claude responded: API Error: 529 Overloaded.\n\n"
    "API Error: 529 Overloaded. This is a server-side issue, usually temporary "
    "— try again in a moment. If it persists, check https://status.claude.com."
)


def test_is_overload_only_harvest_matches_archive_fixture() -> None:
    assert _is_overload_only_harvest(_OVERLOAD_ONLY_BODY) is True
    assert _is_overload_only_harvest("legitimate short answer") is False
    assert (
        _is_overload_only_harvest(
            "Done. API Error: 529 was transient during the run."
        )
        is False
    )


def test_run_cdp_generate_proof_short_answer_with_529_quote_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC4 negative: harvest quoting 529 in prose must not trip overload gate."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    short_answer = "Summary complete. API Error: 529 was mentioned once."
    client = _FakeClient(
        [
            {"execution_id": "sat-quote", "status": "running"},
            {
                "execution_id": "sat-quote",
                "status": "running",
                "archive_uri": "cortex://notes/system/threads/archive.md",
                "body": short_answer,
            },
        ]
    )

    result = run_cdp_generate(
        execution_id="dispatch-quote",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
    )
    assert result.ok is True
    assert result.body == short_answer


def test_run_cdp_generate_submit_529_then_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC1: one retry after backoff; on_submitted fires once; proof completes."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient(
        [
            {"_status_code": 529},
            {"execution_id": "sat-retry", "status": "running"},
            {
                "execution_id": "sat-retry",
                "status": "running",
                "archive_uri": "cortex://notes/system/threads/archive.md",
                "body": "harvested after retry",
            },
        ]
    )
    sleeps: list[float] = []
    seen: list[str] = []

    result = run_cdp_generate(
        execution_id="dispatch-retry",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=sleeps.append,
        on_submitted=seen.append,
    )
    assert result.ok is True
    assert result.body == "harvested after retry"
    assert seen == ["sat-retry"]
    assert sleeps[0] == SUBMIT_RETRY_BACKOFF_S
    assert sleeps.count(SUBMIT_RETRY_BACKOFF_S) == 1


def test_run_cdp_generate_submit_529_twice_upstream_overloaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC2: double 529 exhausts retry; no satellite id; overload carrier."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient([{"_status_code": 529}, {"_status_code": 529}])
    sleeps: list[float] = []
    seen: list[str] = []

    result = run_cdp_generate(
        execution_id="dispatch-exhaust",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=sleeps.append,
        on_submitted=seen.append,
    )
    assert result.ok is False
    assert result.satellite_execution_id is None
    assert result.stall_stage == UPSTREAM_OVERLOADED
    assert result.extras.get("reason") == UPSTREAM_OVERLOADED
    assert seen == []
    assert sleeps == [SUBMIT_RETRY_BACKOFF_S]


def test_run_cdp_generate_submit_transport_ambiguous_no_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC3: status_code None does not retry or stamp upstream_overloaded."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")

    class _UnreachableClient(_FakeClient):
        def request(
            self, method: str, url: str, json: dict[str, Any] | None = None
        ) -> _FakeResp:
            if method == "POST" and "/abort" not in url:
                self.calls.append((method, url))
                raise httpx.ConnectError("connection refused")
            return super().request(method, url, json=json)

    client = _UnreachableClient([])
    sleeps: list[float] = []

    result = run_cdp_generate(
        execution_id="dispatch-transport",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=sleeps.append,
    )
    assert result.ok is False
    assert result.stall_stage is None
    assert result.extras.get("reason") != UPSTREAM_OVERLOADED
    assert sleeps == []
    assert len(client.calls) == 1


def test_run_cdp_generate_proof_overload_only_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC4: overload-only harvest body is not ok=True."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient(
        [
            {"execution_id": "sat-ol", "status": "running"},
            {
                "execution_id": "sat-ol",
                "status": "running",
                "archive_uri": "cortex://notes/system/threads/cdp-ask-archive-new.md",
                "body": _OVERLOAD_ONLY_BODY,
            },
        ]
    )

    result = run_cdp_generate(
        execution_id="dispatch-ol-body",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
    )
    assert result.ok is False
    assert result.stall_stage == UPSTREAM_OVERLOADED
    assert result.extras.get("reason") == UPSTREAM_OVERLOADED


def test_run_cdp_generate_proof_empty_body_with_archive_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC4/F7: empty snapshot body with archive_uri must not bypass overload gate."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient(
        [
            {"execution_id": "sat-empty", "status": "running"},
            {
                "execution_id": "sat-empty",
                "status": "running",
                "archive_uri": "cortex://notes/system/threads/cdp-ask-archive-new.md",
                "body": "",
            },
        ]
    )

    result = run_cdp_generate(
        execution_id="dispatch-ol-empty",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
    )
    assert result.ok is False
    assert result.stall_stage == UPSTREAM_OVERLOADED
    assert result.extras.get("reason") == UPSTREAM_OVERLOADED
