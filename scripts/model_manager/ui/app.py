"""Model Manager TUI application."""

from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header
from universal_event_bus import EventBus, MinimalEventDebugBroadcaster

from scripts.model_manager.ensure_venv import find_workspace_root

from .api_server import ManageAPIServer, ManageSocketBusyError
from .controller.onboarding import OnboardingController
from .controller.service_config import ensure_event_service_config, ensure_socket_dir
from .controller.service_ctl import ServiceController
from .model.catalog_state import CatalogState
from .model.local_env import LocalEnv
from .tui_events import TuiExited, TuiStarted
from .view.screens.catalog import CatalogScreen
from .view.screens.download import DownloadScreen
from .view.screens.footprint import FootprintScreen
from .view.screens.home import HomeScreen
from .view.screens.measure import MeasureScreen
from .view.screens.remotes import RemotesScreen
from .view.screens.services import ServicesScreen
from .view.screens.settings import SettingsScreen
from .view.widgets.status_bar import StatusBar

logger = logging.getLogger(__name__)

_TUI_LOG_PATH = Path("/tmp/logs/tui/tui.log")
_MANAGE_API_LOG_PATH = Path("/tmp/logs/tui/manage-api.log")
_MANAGE_API_LOG_BYTES = 1_048_576  # 1 MiB per file
_MANAGE_API_LOG_BACKUPS = 3


def _configure_manage_api_logging() -> None:
    """Persist manage-api logger output to disk.

    Textual captures stdout/stderr and routes it to its own devlog, so
    `logger.exception` calls from the manage API connection handler vanish
    by default. Without persisted logs, a sync_restart wedge leaves no
    forensic trail. A bounded RotatingFileHandler attached to the
    api_server module's logger ensures every traceback lands in a file
    agents can read after the fact.
    """
    api_logger = logging.getLogger("scripts.model_manager.ui.api_server")
    if any(getattr(h, "_manage_api_handler", False) for h in api_logger.handlers):
        return
    _MANAGE_API_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        _MANAGE_API_LOG_PATH,
        maxBytes=_MANAGE_API_LOG_BYTES,
        backupCount=_MANAGE_API_LOG_BACKUPS,
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    handler._manage_api_handler = True  # idempotency marker
    api_logger.addHandler(handler)
    api_logger.setLevel(logging.INFO)
    api_logger.propagate = False


class ModelManagerApp(App):
    """Interactive TUI for onboarding and managing the Universal LLM Gateway."""

    TITLE = "Model Manager"
    SUB_TITLE = "Universal LLM Gateway"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    # Screen id → screen class for push_screen("home"), etc.
    SCREENS = {
        "home": HomeScreen,
        "catalog": CatalogScreen,
        "services": ServicesScreen,
        "settings": SettingsScreen,
        "remotes": RemotesScreen,
        "footprint": FootprintScreen,
    }

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(self, workspace_root: Path | None = None) -> None:
        super().__init__()
        self._workspace_root = workspace_root or find_workspace_root()
        self._local_env = LocalEnv(self._workspace_root)
        self._catalog = CatalogState(
            static_catalog_dir=self._workspace_root / "config" / "models",
            local_catalog_dir=self._local_env.local_catalog_dir,
        )
        self._catalog.refresh()
        self._service_controller = ServiceController(self._workspace_root)
        self._onboarding = OnboardingController(
            catalog=self._catalog,
            local_env=self._local_env,
            workspace_root=self._workspace_root,
        )
        self._event_bus: EventBus | None = None
        self._broadcaster: MinimalEventDebugBroadcaster | None = None
        self._api_server: ManageAPIServer | None = None

    @property
    def catalog(self) -> CatalogState:
        return self._catalog

    @property
    def local_env(self) -> LocalEnv:
        return self._local_env

    @property
    def service_controller(self) -> ServiceController:
        return self._service_controller

    @property
    def event_bus(self) -> EventBus | None:
        """Shared manage EventBus (None until on_mount wires it)."""
        return self._event_bus

    @property
    def onboarding(self) -> OnboardingController:
        return self._onboarding

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield StatusBar()

    async def on_mount(self) -> None:
        ensure_event_service_config()
        warning = self._service_controller.check_model_path_ownership()
        if warning:
            self.notify(warning, severity="error", timeout=15)
        self.push_screen("home")

        self._broadcaster = MinimalEventDebugBroadcaster()
        self._event_bus = EventBus(debug_broadcaster=self._broadcaster)
        await self._broadcaster.start_debug_server()
        await self._event_bus.publish(TuiStarted(pid=os.getpid()))

        self._api_server = ManageAPIServer(self._service_controller, self._event_bus)
        try:
            await self._api_server.start()
        except ManageSocketBusyError as e:
            # Another live ./manage owns the socket. Retrying would only
            # silently rebind and orphan that controller — refuse instead and
            # let the user resolve the conflict.
            logger.error("Manage API server refused to bind: %s", e)
            self._api_server = None
            self.notify(
                f"manage.sock conflict: {e}",
                severity="error",
                timeout=60,
            )
        except Exception as e:
            logger.exception("Failed to start Manage API server: %s", e)
            self._api_server = None
            self.notify(
                f"manage.sock unavailable: {e} — restart ./manage to recover",
                severity="error",
                timeout=30,
            )
            self.set_timer(10, self._retry_api_server)

    async def _retry_api_server(self) -> None:
        """Retry binding manage.sock after a startup failure.

        Called 10s after on_mount if the initial bind failed (typically because
        /tmp/universal-protocol was root-owned at launch and has since been
        fixed). Backs off to 30s intervals until it succeeds.

        Stops retrying on ManageSocketBusyError — user must resolve the
        dual-instance conflict explicitly; auto-rebinding would resurrect
        Failure 1 (silent orphaning).
        """
        if self._api_server is not None:
            return  # already running
        if self._event_bus is None:
            return
        server = ManageAPIServer(self._service_controller, self._event_bus)
        try:
            await server.start()
            self._api_server = server
            self.notify("manage.sock recovered — agent tools now available", timeout=10)
            logger.info("Manage API server recovered on retry")
        except ManageSocketBusyError as e:
            logger.error("Manage API server retry refused: %s", e)
            return
        except Exception as e:
            logger.warning("Manage API server retry failed: %s", e)
            self.set_timer(30, self._retry_api_server)

    async def on_unmount(self) -> None:
        # Kill build process group directly — the worker's finally block may
        # have already cleared _build_process, so we can't rely on cancel_build().
        proc = self._service_controller._build_process
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        if self._api_server is not None:
            try:
                await self._api_server.stop()
            except Exception as e:
                logger.exception("Error stopping Manage API server: %s", e)
        if self._event_bus is not None:
            await self._event_bus.publish(TuiExited(reason="quit"))
        if self._broadcaster is not None:
            await self._broadcaster.stop_debug_server()

    def push_screen(self, screen: str, kwargs: dict | None = None) -> None:
        kwargs = kwargs or {}
        match screen:
            case "download":
                model_id = kwargs.get("model_id", "")
                super().push_screen(DownloadScreen(model_id=model_id))
            case "measure":
                model_id = kwargs.get("model_id", "")
                training_ctx = 0
                model_info = self._catalog.get(model_id)
                if model_info:
                    training_ctx = model_info.training_context_length
                super().push_screen(
                    MeasureScreen(
                        model_id=model_id,
                        training_context_length=training_ctx,
                    )
                )
            case _:
                if screen in self.SCREENS:
                    super().push_screen(self.SCREENS[screen]())
                else:
                    logger.warning("Unknown screen id: %s", screen)


def run() -> None:
    """Entry point for the TUI."""
    import logging

    # Avoid polluting the TUI with DEBUG/INFO from probes and third-party libs.
    for _logger in (
        "httpcore",
        "httpcore.connection",
        "httpx",
        "urllib3",
        "scripts.model_manager.ui.controller.service_ctl",
        "scripts.model_manager.ui.model.service_state",
    ):
        logging.getLogger(_logger).setLevel(logging.WARNING)

    _TUI_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _configure_manage_api_logging()

    # Claim /tmp/universal-protocol as the current user before any container
    # starts. /tmp is 1777 so mkdir succeeds unconditionally when the path is
    # absent. This prevents the Docker daemon from racing ahead and creating
    # the bind-mount source dir as root (which blocks all host-side UDS writes).
    _sock_err = ensure_socket_dir()
    if _sock_err:
        logging.getLogger(__name__).warning(
            "ensure_socket_dir at startup: %s", _sock_err
        )

    app = ModelManagerApp()
    try:
        app.run()
    except Exception:
        tb = traceback.format_exc()
        ts = datetime.now(UTC).isoformat()
        with _TUI_LOG_PATH.open("a") as fh:
            fh.write(f"\n[{ts}] TUI crashed:\n{tb}\n")
        print(
            f"\nManage TUI crashed. Traceback written to {_TUI_LOG_PATH}",
            file=sys.stderr,
        )
        app.bell()
        # Optionally, display an error screen: app.push_screen(ErrorScreen(...))
        # app.push_screen(ErrorScreen(message="An unexpected error occurred. See logs for details."))
        # Or, if the intention is to just log and let the process terminate naturally:
        # sys.exit(1) # Or similar controlled exit
