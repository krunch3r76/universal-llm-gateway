"""Status bar widget showing service health and image status."""

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from ...model.build_state import BuildStatus
from ...model.service_state import ServiceStatus


def _status_icon(status: ServiceStatus | BuildStatus) -> str:
    match status:
        case ServiceStatus.RUNNING | BuildStatus.BUILT:
            return "[green]●[/]"
        case ServiceStatus.STOPPED | BuildStatus.NOT_BUILT:
            return "[red]○[/]"
        case ServiceStatus.UNHEALTHY | BuildStatus.FAILED:
            return "[yellow]◌[/]"
        case ServiceStatus.NOT_ENABLED:
            return "[dim]—[/]"
        case ServiceStatus.DISABLED:
            return "[blue]⊘[/]"
        case BuildStatus.BUILDING:
            return "[cyan]◎[/]"
        case _:
            return "[dim]?[/]"


class StatusBar(Widget):
    """Horizontal bar showing Gateway, Stargate, and Docker image status."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text;
    }
    StatusBar .status-item {
        width: auto;
        padding: 0 2;
    }
    """

    gateway_status: reactive[str] = reactive("unknown")
    stargate_status: reactive[str] = reactive("unknown")
    image_status: reactive[str] = reactive("unknown")

    def compose(self) -> ComposeResult:
        yield Static(self._render_text(), id="status-text")

    def _render_text(self) -> str:
        gw = _status_icon(ServiceStatus(self.gateway_status))
        sg = _status_icon(ServiceStatus(self.stargate_status))
        img = _status_icon(BuildStatus(self.image_status))
        return f" {gw} Gateway  {sg} Stargate  {img} Docker Image"

    def watch_gateway_status(self) -> None:
        self._update_display()

    def watch_stargate_status(self) -> None:
        self._update_display()

    def watch_image_status(self) -> None:
        self._update_display()

    def _update_display(self) -> None:
        try:
            self.query_one("#status-text", Static).update(self._render_text())
        except Exception:
            pass
