"""Uvicorn entrypoint for ``git-integration-worker``."""

from __future__ import annotations

import uvicorn

from services.git_integration_worker.config import load_config


def main() -> None:
    cfg = load_config()
    uvicorn.run(
        "services.git_integration_worker.app:app",
        host=cfg.host,
        port=cfg.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
