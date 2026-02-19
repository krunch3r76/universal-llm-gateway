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
    #vram-warning {
        height: auto;
        padding: 1 2;
        background: $warning-darken-3;
        color: $text;
        display: none;
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
        yield Static("", id="vram-warning")
        yield LogStream(id="measure-log")
        with Horizontal(id="measure-actions"):
            yield Button("Start Measurement", id="btn-start", variant="primary")
            yield Button(
                "Unload All", id="btn-unload", variant="warning", disabled=True
            )
            yield Button("Back", id="btn-back")
        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_start_measure(self) -> None:
        btn = self.query_one("#btn-start", Button)
        if not btn.disabled:
            btn.disabled = True
            self.run_worker(self._preflight_and_measure(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-start":
                self.action_start_measure()
            case "btn-unload":
                self.run_worker(self._unload_and_retry(), exclusive=True)
            case "btn-back":
                self.app.pop_screen()

    async def _preflight_and_measure(self) -> None:
        """Check for loaded models before starting measurement."""
        contexts = self.query_one("#contexts-input", Input).value.strip()
        do_gpu = self.query_one("#gpu-check", Checkbox).value
        do_cpu = self.query_one("#cpu-check", Checkbox).value

        if not do_gpu and not do_cpu:
            self._write("Select at least one of GPU or CPU.")
            self.query_one("#btn-start", Button).disabled = False
            return

        onboarding = self.app.onboarding  # type: ignore[attr-defined]
        loaded = await onboarding.check_loaded_models()

        if loaded:
            resource = "VRAM" if do_gpu else "RAM"
            model_list = ", ".join(loaded)
            self._show_vram_warning(
                f"⚠ {resource} OCCUPIED: {len(loaded)} model(s) loaded: {model_list}\n"
                f"  Measurement requires clean {resource} for accurate capacity detection."
            )
            self._write(f"⚠ {len(loaded)} model(s) loaded — unload before measuring.")
            self.query_one("#btn-unload", Button).disabled = False
            self.query_one("#btn-start", Button).disabled = False
            return

        await self._run_measure(contexts, do_gpu, do_cpu)

    async def _unload_and_retry(self) -> None:
        """Unload all models then start measurement."""
        onboarding = self.app.onboarding  # type: ignore[attr-defined]
        self.query_one("#btn-unload", Button).disabled = True

        loaded = await onboarding.check_loaded_models()
        if not loaded:
            self._write("✓ No models loaded — ready to measure.")
            self._hide_vram_warning()
            self.query_one("#btn-start", Button).disabled = False
            return

        self._write(f"Unloading {len(loaded)} model(s)...")
        results = await onboarding.unload_models(loaded)

        all_ok = True
        for model_id, success, message in results:
            if success:
                self._write(f"  ✓ {model_id}: {message}")
            else:
                self._write(f"  ✗ {model_id}: {message}")
                all_ok = False

        if all_ok:
            self._write("✓ All models unloaded.")
            self._hide_vram_warning()
            # Auto-start measurement
            contexts = self.query_one("#contexts-input", Input).value.strip()
            do_gpu = self.query_one("#gpu-check", Checkbox).value
            do_cpu = self.query_one("#cpu-check", Checkbox).value
            await self._run_measure(contexts, do_gpu, do_cpu)
        else:
            self._write(
                "✗ Some models failed to unload. Fix manually or restart Gateway."
            )
            self.query_one("#btn-unload", Button).disabled = False
            self.query_one("#btn-start", Button).disabled = False

    async def _run_measure(self, contexts: str, do_gpu: bool, do_cpu: bool) -> None:
        onboarding = self.app.onboarding  # type: ignore[attr-defined]
        self.query_one("#btn-start", Button).disabled = True

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

    def _show_vram_warning(self, text: str) -> None:
        warning = self.query_one("#vram-warning", Static)
        warning.update(text)
        warning.display = True

    def _hide_vram_warning(self) -> None:
        warning = self.query_one("#vram-warning", Static)
        warning.display = False

    def _write(self, text: str) -> None:
        self.query_one("#measure-log", LogStream).write_line(text)
