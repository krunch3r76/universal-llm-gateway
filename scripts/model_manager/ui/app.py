"""Model Manager TUI application."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from scripts.model_manager.ensure_venv import find_workspace_root

from .controller.onboarding import OnboardingController
from .controller.service_ctl import ServiceController
from .model.catalog_state import CatalogState
from .model.local_env import LocalEnv
from .view.screens.catalog import CatalogScreen
from .view.screens.download import DownloadScreen
from .view.screens.footprint import FootprintScreen
from .view.screens.home import HomeScreen
from .view.screens.measure import MeasureScreen
from .view.screens.remotes import RemotesScreen
from .view.screens.services import ServicesScreen
from .view.screens.settings import SettingsScreen
from .view.widgets.status_bar import StatusBar


class ModelManagerApp(App):
    """Interactive TUI for onboarding and managing the Universal LLM Gateway."""

    TITLE = "Model Manager"
    SUB_TITLE = "Universal LLM Gateway"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

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
    def onboarding(self) -> OnboardingController:
        return self._onboarding

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        yield StatusBar()

    def on_mount(self) -> None:
        warning = self._service_controller.check_model_path_ownership()
        if warning:
            self.notify(warning, severity="error", timeout=15)
        self.push_screen("home")

    def push_screen(  # type: ignore[override]
        self, screen: str, kwargs: dict | None = None
    ) -> None:
        match screen:
            case "download":
                model_id = (kwargs or {}).get("model_id", "")
                super().push_screen(DownloadScreen(model_id=model_id))
            case "measure":
                model_id = (kwargs or {}).get("model_id", "")
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
                super().push_screen(screen)


def run() -> None:
    """Entry point for the TUI."""
    app = ModelManagerApp()
    app.run()
