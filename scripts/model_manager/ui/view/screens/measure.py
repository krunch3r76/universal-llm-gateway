"""Measure screen - run measurement jobs with SSE log streaming."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Static

from ..widgets.log_stream import LogStream


class MeasureScreen(Screen):
    """Measure a model's VRAM/RAM profile via Gateway Job API."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
        Binding("s", "start_measure", "Start"),
    ]

    DEFAULT_CSS = """
    MeasureScreen {
        layout: vertical;
    }
    #measure-info {
        height: auto;
        padding: 1 2;
    }
    #measure-options {
        height: 3;
        padding: 0 2;
    }
    #measure-options Input {
        width: 30;
        margin: 0 1;
    }
    #measure-options Checkbox {
        margin: 0 1;
        background: $surface;
        color: $text;
    }
    #measure-actions {
        height: 3;
        padding: 0 2;
        dock: bottom;
    }
    #measure-actions Button {
        margin: 0 1;
    }
    """

    def __init__(self, model_id: str = "", training_context_length: int = 0) -> None:
        super().__init__()
        self._model_id = model_id
        self._training_ctx = training_context_length

    def compose(self) -> ComposeResult:
        yield Header()
        info_parts = [f"Measure: {self._model_id}"]
        if self._training_ctx:
            info_parts.append(f"  Training context: {self._training_ctx:,}")
        else:
            info_parts.append("  Training context: unknown")
        yield Static("\n".join(info_parts), id="measure-info")
        with Horizontal(id="measure-options"):
            placeholder = "Contexts (e.g. 4096,8192)"
            if self._training_ctx:
                placeholder = (
                    f"Contexts — empty = auto from {self._training_ctx:,} down"
                )
            yield Input(placeholder=placeholder, id="contexts-input")
            yield Checkbox("GPU", id="gpu-check", value=True)
            yield Checkbox("CPU", id="cpu-check")
        yield LogStream(id="measure-log")
        with Horizontal(id="measure-actions"):
            yield Button("Start Measurement [s]", id="btn-start", variant="primary")
            yield Button("Back [esc]", id="btn-back")
        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_start_measure(self) -> None:
        btn = self.query_one("#btn-start", Button)
        if not btn.disabled:
            btn.disabled = True
            self.run_worker(self._run_measure(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-start":
                self.action_start_measure()
            case "btn-back":
                self.app.pop_screen()

    async def _run_measure(self) -> None:
        contexts = self.query_one("#contexts-input", Input).value.strip()
        do_gpu = self.query_one("#gpu-check", Checkbox).value
        do_cpu = self.query_one("#cpu-check", Checkbox).value

        if not do_gpu and not do_cpu:
            self._write("Select at least one of GPU or CPU.")
            self.query_one("#btn-start", Button).disabled = False
            return

        onboarding = self.app.onboarding  # type: ignore[attr-defined]

        if do_gpu:
            self._write("━━━ GPU measurement ━━━")
            async for line in onboarding.measure_model(
                self._model_id, contexts=contexts, cpu=False
            ):
                self._write(line)

        if do_cpu:
            self._write("━━━ CPU measurement ━━━")
            async for line in onboarding.measure_model(
                self._model_id, contexts=contexts, cpu=True
            ):
                self._write(line)

    def _write(self, text: str) -> None:
        self.query_one("#measure-log", LogStream).write_line(text)
