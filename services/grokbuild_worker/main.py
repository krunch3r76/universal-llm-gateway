"""Uvicorn entrypoint shared by bare-metal systemd and the container fallback.

Both deploy shapes invoke ``python -m services.grokbuild_worker.main``;
host/port come from env vars (see ``config.WorkerConfig``).
"""

from __future__ import annotations

import uvicorn

from services.grokbuild_worker.config import load_config


def main() -> None:
    cfg = load_config()
    uvicorn.run(
        "services.grokbuild_worker.app:app",
        host=cfg.host,
        port=cfg.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
