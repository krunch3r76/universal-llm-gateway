"""Pipeline Viewer — standalone FastAPI app for consensus pipeline demos.

Reads pipeline execution events from events.jsonl files and serves them
via REST API alongside a single-page web frontend.  Supports live
streaming via SSE (Server-Sent Events) by tailing the JSONL file.

Usage:
    python server.py [--port 8080] [--summaries-dir /path/to/summaries]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import uvicorn
from aggregator import aggregate_execution
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from list_cache import ExecutionListCache

logger = logging.getLogger(__name__)

DEFAULT_SUMMARIES_DIR = Path("/tmp/logs/universal-stargate/pipeline_summaries")
DEFAULT_SNAPSHOTS_DIR = Path("/tmp/stargate-request-snapshots")
STATIC_DIR = Path(__file__).parent / "static"

SNAPSHOT_STAGES = ("before", "after", "response-from-gateway", "response-to-client")

_TERMINAL_EVENT_TYPES = frozenset(
    {"pipeline_completed", "pipeline_failed", "pipeline_cancelled"}
)


def create_app(
    summaries_dir: Path,
    snapshots_dir: Path = DEFAULT_SNAPSHOTS_DIR,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Consensus Pipeline Viewer")
    execution_list_cache = ExecutionListCache()

    # -- API routes ----------------------------------------------------------

    @app.get("/api/executions")
    def get_executions(request: Request) -> Response:
        """List all executions with live/complete status."""
        executions = execution_list_cache.refresh(summaries_dir)
        etag = f'W/"{execution_list_cache.version}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(content=executions, headers={"ETag": etag})

    @app.get("/api/executions/{pipeline_id}/{exec_id}")
    def get_execution(pipeline_id: str, exec_id: str) -> JSONResponse:
        """Get full execution data aggregated from events.jsonl."""
        exec_dir = _resolve_exec_dir(summaries_dir, pipeline_id, exec_id)
        return JSONResponse(content=aggregate_execution(exec_dir))

    @app.get("/api/executions/{pipeline_id}/{exec_id}/stream")
    async def stream_execution(pipeline_id: str, exec_id: str) -> StreamingResponse:
        """SSE endpoint: tail events.jsonl and push events to the browser."""
        exec_dir = _resolve_exec_dir(summaries_dir, pipeline_id, exec_id)
        events_file = exec_dir / "events.jsonl"
        return StreamingResponse(
            _tail_events(events_file),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/snapshots/{request_id}")
    def get_snapshots(request_id: str) -> JSONResponse:
        """Look up request/response snapshots by snapshot_request_id.

        Scans {snapshots_dir}/{stage}/ for files matching *_{request_id}.json.
        Returns whatever stages exist on disk.
        """
        result: dict[str, Any] = {}
        if not snapshots_dir.exists():
            raise HTTPException(status_code=404, detail="Snapshots directory not found")

        # Apply same sanitization as the writer (request_snapshots.py):
        # replace "/" with "-" and truncate to 32 chars
        safe_request_id = request_id.replace("/", "-")[:32]

        for stage in SNAPSHOT_STAGES:
            stage_dir = snapshots_dir / stage
            if not stage_dir.is_dir():
                continue
            for f in stage_dir.iterdir():
                if f.suffix == ".json" and safe_request_id in f.name:
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        key = stage.replace("-", "_")
                        result[key] = data
                    except (json.JSONDecodeError, OSError) as e:
                        logger.warning("Could not read snapshot %s: %s", f, e)
                    break

        if not result:
            raise HTTPException(status_code=404, detail="No snapshots found")
        return JSONResponse(content=result)

    # -- Static files --------------------------------------------------------

    @app.get("/")
    def index() -> FileResponse:
        """Serve the frontend."""
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-cache"},
        )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


async def _tail_events(events_file: Path) -> AsyncGenerator[str, None]:
    """Tail a JSONL file, yielding SSE-formatted lines.

    1. Replay all existing lines.
    2. Poll for new lines every 500ms.
    3. Stop when a terminal event is seen or after 5 min timeout.
    """
    max_wait_s = 300
    elapsed = 0.0
    poll_interval = 0.5

    # Wait for file to appear (pipeline may still be starting)
    while not events_file.exists():
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        if elapsed >= max_wait_s:
            yield 'event: error\ndata: {"error":"timeout waiting for events"}\n\n'
            return

    with events_file.open("r", encoding="utf-8") as fh:
        while elapsed < max_wait_s:
            line = fh.readline()
            if line:
                line = line.strip()
                if not line:
                    continue
                yield f"data: {line}\n\n"
                # Check for terminal event
                try:
                    ev = json.loads(line)
                    if ev.get("event_type") in _TERMINAL_EVENT_TYPES:
                        yield "event: done\ndata: {}\n\n"
                        return
                except json.JSONDecodeError:
                    pass
            else:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

    yield 'event: done\ndata: {"reason":"timeout"}\n\n'


def _resolve_exec_dir(summaries_dir: Path, pipeline_id: str, exec_id: str) -> Path:
    """Locate the execution directory or raise 404."""
    pipeline_dir = summaries_dir / pipeline_id
    if not pipeline_dir.exists():
        raise HTTPException(status_code=404, detail="Pipeline not found")
    exec_dir = _find_exec_dir(pipeline_dir, exec_id)
    if not exec_dir:
        raise HTTPException(status_code=404, detail="Execution not found")
    events_file = exec_dir / "events.jsonl"
    if not events_file.exists():
        raise HTTPException(status_code=404, detail="No events.jsonl found")
    return exec_dir


def _find_exec_dir(pipeline_dir: Path, exec_id: str) -> Path | None:
    """Find execution directory by exec_id match.

    Directory names are ``{date}_{time}_{short_hash}`` while the execution_id
    from events.jsonl is the full UUID.  Match when the directory's trailing
    segment is a prefix of the requested exec_id (or an exact suffix).
    """
    for d in pipeline_dir.iterdir():
        if not d.is_dir():
            continue
        # Exact suffix match (short hash passed directly)
        if d.name.endswith(exec_id):
            return d
        # Directory short hash is a prefix of the full UUID
        dir_suffix = d.name.rsplit("_", 1)[-1]
        if exec_id.startswith(dir_suffix):
            return d
    return None


def main() -> None:
    """Entry point for the pipeline viewer server."""
    arg_parser = argparse.ArgumentParser(description="Consensus Pipeline Viewer")
    arg_parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    arg_parser.add_argument(
        "--summaries-dir",
        type=Path,
        default=DEFAULT_SUMMARIES_DIR,
        help="Path to pipeline summaries directory",
    )
    arg_parser.add_argument(
        "--snapshots-dir",
        type=Path,
        default=DEFAULT_SNAPSHOTS_DIR,
        help="Path to stargate request snapshots directory",
    )
    args = arg_parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    app = create_app(args.summaries_dir, args.snapshots_dir)
    logger.info(
        "Starting Pipeline Viewer on http://localhost:%d (summaries: %s)",
        args.port,
        args.summaries_dir,
    )
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
