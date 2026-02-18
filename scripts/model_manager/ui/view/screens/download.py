"""Download screen - model download with log streaming."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from ..widgets.log_stream import LogStream


class DownloadScreen(Screen):
    """Download a model from HuggingFace with progress output."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
        Binding("s", "start_download", "Start"),
    ]

    DEFAULT_CSS = """
    DownloadScreen {
        layout: vertical;
    }
    #dl-info {
        height: auto;
        padding: 1 2;
    }
    #dl-actions {
        height: 3;
        padding: 0 2;
        dock: bottom;
    }
    #dl-actions Button {
        margin: 0 1;
    }
    """

    def __init__(self, model_id: str = "") -> None:
        super().__init__()
        self._model_id = model_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"Download: {self._model_id}", id="dl-info")
        yield LogStream(id="dl-log")
        with Horizontal(id="dl-actions"):
            yield Button("Start Download [s]", id="btn-start", variant="primary")
            yield Button("Back [esc]", id="btn-back")
        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_start_download(self) -> None:
        btn = self.query_one("#btn-start", Button)
        if not btn.disabled:
            btn.disabled = True
            self.run_worker(self._run_download(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-start":
                self.action_start_download()
            case "btn-back":
                self.app.pop_screen()

    async def _run_download(self) -> None:
        catalog = self.app.catalog  # type: ignore[attr-defined]
        model = catalog.get(self._model_id)
        if not model:
            self._write("Model not found in catalog.")
            return

        onboarding = self.app.onboarding  # type: ignore[attr-defined]
        async for line in onboarding.download_model(model):
            self._write(line)
        self._write("[green]Done.[/]")

    def _write(self, text: str) -> None:
        self.query_one("#dl-log", LogStream).write_line(text)
