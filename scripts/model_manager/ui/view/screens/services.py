"""Services screen - local build, start, stop Gateway and Stargate.

Remote operations (deploy, restart remotes) live on the Home topology panel.
"""

import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Button, Footer, Header, Select, Static

from ...controller.operation_log import tee_with_summary
from ..widgets.log_stream import LogStream

_SCOPE_FLAGS: dict[str, list[str]] = {
    "all": ["--cpu-native", "--gpu-native"],
    "llama": ["--cpu-native", "--gpu-native", "--no-vllm"],
}


class ServicesScreen(Screen):
    """Manage Docker builds and service lifecycle."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back", priority=True),
    ]

    _poll_timer: Timer | None = None
    _POLL_INTERVAL = 5.0

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    DEFAULT_CSS = """
    ServicesScreen {
        layout: vertical;
    }
    #svc-status {
        height: auto;
        padding: 1 2;
    }
    #svc-buttons {
        height: auto;
        padding: 1 2;
    }
    #svc-buttons Button {
        margin: 0 1;
        opacity: 100%;
    }
    #build-options {
        height: auto;
        padding: 0 2;
    }
    #build-options Select {
        width: 30;
    }
    #build-flags {
        margin: 0 0 0 2;
        color: $text-muted;
    }
    #svc-bottom {
        height: 3;
        padding: 0 2;
        dock: bottom;
    }
    #svc-buttons Button:disabled {
        background: $surface-darken-1;
        color: $text-muted;
        opacity: 60%;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="svc-status"):
            yield Static("[b]Docker Image[/b]", markup=True)
            yield Static("  Checking...", id="img-detail")
            yield Static("")
            yield Static("[b]Services[/b]", markup=True)
            yield Static("  Gateway:  checking...", id="svc-gw")
            yield Static("  Stargate: checking...", id="svc-sg")
            yield Static("  RAG:      checking...", id="svc-rag")
            yield Static("  Sidecar:  checking...", id="svc-sidecar")

        with Vertical(id="build-options"):
            yield Select(
                [("Full build (vLLM + llama)", "all"), ("llama only", "llama")],
                id="build-scope",
                value="all",
            )
            yield Static("", id="build-flags")

        with Horizontal(id="svc-buttons"):
            yield Button("Build Image", id="btn-build", variant="primary")
            yield Button("Start Gateway", id="btn-start-gw", variant="success")
            yield Button(
                "Stop Gateway", id="btn-stop-gw", variant="error", disabled=True
            )
            yield Button("Start Stargate", id="btn-start-sg", variant="success")
            yield Button(
                "Stop Stargate", id="btn-stop-sg", variant="error", disabled=True
            )
            yield Button("Start RAG", id="btn-start-rag", variant="success")
            yield Button("Stop RAG", id="btn-stop-rag", variant="error", disabled=True)
            yield Button("Start Sidecar", id="btn-start-sidecar", variant="success")
            yield Button(
                "Stop Sidecar", id="btn-stop-sidecar", variant="error", disabled=True
            )
            yield Button("Restart Local", id="btn-restart-local", variant="warning")

        yield LogStream(id="svc-log")

        with Horizontal(id="svc-bottom"):
            yield Button("Refresh", id="btn-refresh")
            yield Button("Back [esc]", id="btn-back")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_status()
        self._update_build_flags()
        self._poll_timer = self.set_interval(self._POLL_INTERVAL, self._refresh_status)

    def on_screen_suspend(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.pause()

    def on_screen_resume(self) -> None:
        self._refresh_status()
        if self._poll_timer is not None:
            self._poll_timer.resume()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "build-scope":
            self._update_build_flags()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-build":
                svc = self.app.service_controller  # type: ignore[attr-defined]
                if svc.build_running:
                    event.button.disabled = True
                    self.run_worker(self._cancel_build(), exclusive=True)
                else:
                    self._set_build_ui(building=True)
                    self.run_worker(
                        self._build(self._selected_scope()),
                        exclusive=True,
                    )
            case "btn-start-gw" | "btn-stop-gw":
                self.query_one("#btn-start-gw", Button).disabled = True
                self.query_one("#btn-stop-gw", Button).disabled = True
                if event.button.id == "btn-start-gw":
                    self.run_worker(self._start_gateway(), exclusive=True)
                else:
                    self.run_worker(self._stop_gateway(), exclusive=True)
            case "btn-start-sg" | "btn-stop-sg":
                self.query_one("#btn-start-sg", Button).disabled = True
                self.query_one("#btn-stop-sg", Button).disabled = True
                if event.button.id == "btn-start-sg":
                    self.run_worker(self._start_stargate(), exclusive=True)
                else:
                    self.run_worker(self._stop_stargate(), exclusive=True)
            case "btn-start-rag" | "btn-stop-rag":
                self.query_one("#btn-start-rag", Button).disabled = True
                self.query_one("#btn-stop-rag", Button).disabled = True
                if event.button.id == "btn-start-rag":
                    self.run_worker(self._start_rag(), exclusive=True)
                else:
                    self.run_worker(self._stop_rag(), exclusive=True)
            case "btn-start-sidecar" | "btn-stop-sidecar":
                self.query_one("#btn-start-sidecar", Button).disabled = True
                self.query_one("#btn-stop-sidecar", Button).disabled = True
                if event.button.id == "btn-start-sidecar":
                    self.run_worker(self._start_sidecar(), exclusive=True)
                else:
                    self.run_worker(self._stop_sidecar(), exclusive=True)
            case "btn-restart-local":
                self.run_worker(self._restart_local(), exclusive=True)
            case "btn-refresh":
                self._refresh_status()
            case "btn-back":
                self.app.pop_screen()

    def _refresh_status(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        build = svc.check_image()
        services = svc.service_state.check_all()
        gw, sg, rag = services[0], services[1], services[2]
        sidecar = svc.service_state.check_sidecar()

        config_text = f"  ({build.config.summary()})" if build.config.cpu else ""
        self.query_one("#img-detail", Static).update(
            f"  Status: {build.status}"
            + (f"  Size: {build.size}" if build.size else "")
            + (f"  ID: {build.image_id}" if build.image_id else "")
            + config_text
        )
        self.query_one("#svc-gw", Static).update(f"  Gateway:  {gw.status} {gw.detail}")
        self.query_one("#svc-sg", Static).update(f"  Stargate: {sg.status} {sg.detail}")
        self.query_one("#svc-rag", Static).update(
            f"  RAG:      {rag.status} {rag.detail}"
        )
        self.query_one("#svc-sidecar", Static).update(
            f"  Sidecar:  {sidecar.status} {sidecar.detail}"
        )

        gw_exists = gw.status.value != "stopped"
        sg_up = sg.status.value == "running"
        rag_up = rag.status.value == "running"
        sidecar_up = sidecar.status.value == "running"
        self.query_one("#btn-start-gw", Button).disabled = gw_exists
        self.query_one("#btn-stop-gw", Button).disabled = not gw_exists
        self.query_one("#btn-start-sg", Button).disabled = sg_up
        self.query_one("#btn-stop-sg", Button).disabled = not sg_up
        self.query_one("#btn-start-rag", Button).disabled = rag_up
        self.query_one("#btn-stop-rag", Button).disabled = not rag_up
        self.query_one("#btn-start-sidecar", Button).disabled = sidecar_up
        self.query_one("#btn-stop-sidecar", Button).disabled = not sidecar_up

    def _update_build_flags(self) -> None:
        scope_sel = self.query_one("#build-scope", Select)
        scope = str(scope_sel.value) if scope_sel.value != Select.BLANK else "all"
        flags = _SCOPE_FLAGS.get(scope, _SCOPE_FLAGS["all"])
        self.query_one("#build-flags", Static).update(f"  flags: {' '.join(flags)}")

    def _set_build_ui(self, *, building: bool) -> None:
        btn = self.query_one("#btn-build", Button)
        if building:
            btn.label = "Cancel Build"
            btn.variant = "error"
        else:
            btn.label = "Build Image"
            btn.variant = "primary"
        btn.disabled = False

    def _selected_scope(self) -> str:
        val = self.query_one("#build-scope", Select).value
        return str(val) if val != Select.BLANK else "all"

    async def _restart_local(self) -> None:
        """Stop and restart local gateway + stargate only."""
        log = self.query_one("#svc-log", LogStream)
        log.clear()
        svc = self.app.service_controller  # type: ignore[attr-defined]

        log.write_line("[localhost] Stopping services...")
        log.write_line(await svc.stop_stargate())
        log.write_line(await svc.stop_gateway())
        await asyncio.sleep(1)
        log.write_line("[localhost] Starting gateway...")
        log.write_line(await svc.start_gateway())
        await asyncio.sleep(2)
        log.write_line("[localhost] Starting stargate...")
        log.write_line(await svc.start_stargate())
        self._refresh_status()

    async def _build(self, scope: str) -> None:
        log = self.query_one("#svc-log", LogStream)
        log.clear()
        svc = self.app.service_controller  # type: ignore[attr-defined]
        summary = tee_with_summary(
            svc.build_image(scope=scope),
            operation="build",
            host="localhost",
        )
        await log.stream_from(summary)
        self._set_build_ui(building=False)
        self._refresh_status()

    async def _cancel_build(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.cancel_build()
        self.query_one("#svc-log", LogStream).write_line(result)
        self._set_build_ui(building=False)

    async def _start_gateway(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.start_gateway()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _stop_gateway(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.stop_gateway()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _start_stargate(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.start_stargate()
        self.query_one("#svc-log", LogStream).write_line(result)
        sidecar_result = await svc.sidecar.start()
        self.query_one("#svc-log", LogStream).write_line(sidecar_result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _stop_stargate(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        sidecar_result = await svc.sidecar.stop()
        self.query_one("#svc-log", LogStream).write_line(sidecar_result)
        result = await svc.stop_stargate()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _start_rag(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.start_rag()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _stop_rag(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.stop_rag()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _start_sidecar(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.sidecar.start()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(1)
        self._refresh_status()

    async def _stop_sidecar(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.sidecar.stop()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(1)
        self._refresh_status()
