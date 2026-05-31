"""Allow running as `python -m scripts.model_manager.ui`."""

from scripts.model_manager.ensure_venv import ensure_venv, find_workspace_root

ensure_venv(find_workspace_root())

from scripts.model_manager.ui.controller.service_config import (  # noqa: E402
    bootstrap_manage_process_logging_env,
)

bootstrap_manage_process_logging_env()

from scripts.model_manager.ui.app import run  # noqa: E402, I001 — bootstrap before loading app

run()
