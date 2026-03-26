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
from ...controller.service_config import (
    is_agent_bus_configured,
    is_cloud_proxy_configured,
    is_cortex_configured,
    is_mcp_configured,
    is_rag_configured,
)
from ..widgets.log_stream import LogStream

_SCOPE_FLAGS: dict[str, list[str]] = {
    "all": ["--cpu-native", "--gpu-native"],
    "llama": ["--cpu-native", "--gpu-native"],
}

_CACHE_TARGET_LABELS: dict[str, str] = {
    "gateway": "Gateway",
    "mcp": "MCP",
    "cortex-api": "Cortex API",
    "agent-bus": "Agent Bus",
    "event-service": "Event Service",
}


class ServicesScreen(Screen):
    """Manage Docker builds and service lifecycle."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back", priority=True),
    ]

    _poll_timer: Timer | None = None
    _POLL_INTERVAL = 2.0

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
    #svc-buttons .svc-button-row {
        height: auto;
        margin-bottom: 1;
    }
    #svc-buttons .svc-button-row:last-of-type {
        margin-bottom: 0;
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
            yield Static("  Build cache (Gateway): —", id="img-cache")
            yield Static("")
            yield Static("[b]Services[/b]", markup=True)
            yield Static("  Gateway:  checking...", id="svc-gw")
            yield Static("  Stargate: checking...", id="svc-sg")
            yield Static("  RAG:      checking...", id="svc-rag")
            yield Static("  RAG failed chunks: —", id="svc-rag-failed")
            yield Static("  Cloud Px: checking...", id="svc-cp")
            yield Static("  Sidecar:  checking...", id="svc-sidecar")
            yield Static("  MCP:      —", id="svc-mcp")
            yield Static("  Cortex:   —", id="svc-cortex")
            yield Static("  AgentBus: —", id="svc-agentbus")
            yield Static("  Events:   checking...", id="svc-events")

        with Vertical(id="build-options"):
            yield Select(
                [("Full build (vLLM + llama)", "all"), ("llama only", "llama")],
                id="build-scope",
                value="all",
            )
            yield Select(
                [
                    ("Gateway cache", "gateway"),
                    ("MCP cache", "mcp"),
                    ("Cortex cache", "cortex-api"),
                    ("Agent Bus cache", "agent-bus"),
                    ("Event Service cache", "event-service"),
                ],
                id="cache-target",
                value="gateway",
            )
            yield Static("", id="build-flags")

        with Vertical(id="svc-buttons"):
            with Horizontal(classes="svc-button-row"):
                yield Button("Build Image", id="btn-build", variant="primary")
                yield Button("Prune Builder Cache", id="btn-prune-cache")
                yield Button("Start Gateway", id="btn-start-gw", variant="success")
                yield Button(
                    "Stop Gateway", id="btn-stop-gw", variant="error", disabled=True
                )
                yield Button("Start Stargate", id="btn-start-sg", variant="success")
                yield Button(
                    "Stop Stargate", id="btn-stop-sg", variant="error", disabled=True
                )
            with Horizontal(classes="svc-button-row"):
                yield Button("Start RAG", id="btn-start-rag", variant="success")
                yield Button(
                    "Stop RAG", id="btn-stop-rag", variant="error", disabled=True
                )
                yield Button("Start Cloud Proxy", id="btn-start-cp", variant="success")
                yield Button(
                    "Stop Cloud Proxy", id="btn-stop-cp", variant="error", disabled=True
                )
                yield Button("Start Sidecar", id="btn-start-sidecar", variant="success")
                yield Button(
                    "Stop Sidecar",
                    id="btn-stop-sidecar",
                    variant="error",
                    disabled=True,
                )
            with Horizontal(classes="svc-button-row"):
                yield Button("Start Events", id="btn-start-events", variant="success")
                yield Button(
                    "Stop Events", id="btn-stop-events", variant="error", disabled=True
                )
                yield Button("Start MCP", id="btn-start-mcp", variant="success")
                yield Button(
                    "Stop MCP", id="btn-stop-mcp", variant="error", disabled=True
                )
                yield Button(
                    "Start Cortex",
                    id="btn-start-cortex",
                    variant="success",
                    disabled=True,
                )
                yield Button(
                    "Stop Cortex", id="btn-stop-cortex", variant="error", disabled=True
                )
                yield Button(
                    "Start AgentBus",
                    id="btn-start-agentbus",
                    variant="success",
                    disabled=True,
                )
                yield Button(
                    "Stop AgentBus",
                    id="btn-stop-agentbus",
                    variant="error",
                    disabled=True,
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
        self.run_worker(self._poll_rag_failed(), exclusive=False)

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
        elif event.select.id == "cache-target":
            self._refresh_cache_size()

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
            case "btn-prune-cache":
                event.button.disabled = True
                self.run_worker(self._prune_cache(), exclusive=True)
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
            case "btn-start-cp" | "btn-stop-cp":
                self.query_one("#btn-start-cp", Button).disabled = True
                self.query_one("#btn-stop-cp", Button).disabled = True
                if event.button.id == "btn-start-cp":
                    self.run_worker(self._start_cloud_proxy(), exclusive=True)
                else:
                    self.run_worker(self._stop_cloud_proxy(), exclusive=True)
            case "btn-start-sidecar" | "btn-stop-sidecar":
                self.query_one("#btn-start-sidecar", Button).disabled = True
                self.query_one("#btn-stop-sidecar", Button).disabled = True
                if event.button.id == "btn-start-sidecar":
                    self.run_worker(self._start_sidecar(), exclusive=True)
                else:
                    self.run_worker(self._stop_sidecar(), exclusive=True)
            case "btn-start-events" | "btn-stop-events":
                self.query_one("#btn-start-events", Button).disabled = True
                self.query_one("#btn-stop-events", Button).disabled = True
                if event.button.id == "btn-start-events":
                    self.run_worker(self._start_event_service(), exclusive=True)
                else:
                    self.run_worker(self._stop_event_service(), exclusive=True)
            case "btn-start-mcp" | "btn-stop-mcp":
                self.query_one("#btn-start-mcp", Button).disabled = True
                self.query_one("#btn-stop-mcp", Button).disabled = True
                if event.button.id == "btn-start-mcp":
                    self.run_worker(self._start_mcp(), exclusive=True)
                else:
                    self.run_worker(self._stop_mcp(), exclusive=True)
            case "btn-start-cortex" | "btn-stop-cortex":
                self.query_one("#btn-start-cortex", Button).disabled = True
                self.query_one("#btn-stop-cortex", Button).disabled = True
                if event.button.id == "btn-start-cortex":
                    self.run_worker(self._start_cortex_api(), exclusive=True)
                else:
                    self.run_worker(self._stop_cortex_api(), exclusive=True)
            case "btn-start-agentbus" | "btn-stop-agentbus":
                self.query_one("#btn-start-agentbus", Button).disabled = True
                self.query_one("#btn-stop-agentbus", Button).disabled = True
                if event.button.id == "btn-start-agentbus":
                    self.run_worker(self._start_agent_bus(), exclusive=True)
                else:
                    self.run_worker(self._stop_agent_bus(), exclusive=True)
            case "btn-restart-local":
                self.run_worker(self._restart_local(), exclusive=True)
            case "btn-refresh":
                self._refresh_status()
            case "btn-back":
                self.app.pop_screen()

    async def _poll_rag_failed(self) -> None:
        """Poll the RAG service for failed extraction chunk count and update display."""
        from transport_utils import make_async_client

        from ...controller.service_config import (
            read_rag_socket_path,
            read_rag_tcp_config,
        )

        while self.is_attached:
            try:
                try:
                    tcp_config = read_rag_tcp_config()
                except ValueError:
                    tcp_config = None

                if tcp_config:
                    host, port = tcp_config
                    base_url = f"http://{host}:{port}"
                else:
                    socket_path = read_rag_socket_path()
                    base_url = f"unix://{socket_path}"

                async with make_async_client(base_url, timeout=3.0) as client:
                    resp = await client.get("/extraction/failed")
                    if resp.status_code == 200:
                        total = resp.json().get("total", 0)
                        label = (
                            f"  RAG failed chunks: [bold red]{total}[/bold red]"
                            if total > 0
                            else "  RAG failed chunks: 0"
                        )
                        self.query_one("#svc-rag-failed", Static).update(label)
            except Exception as e:
                self.log(f"Error polling RAG service: {e}")
            await asyncio.sleep(self._POLL_INTERVAL)

    def _refresh_cache_size(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        target = self._selected_cache_target()
        label = _CACHE_TARGET_LABELS.get(target, target)
        cache_size = svc.check_build_cache(target=target)
        if cache_size:
            self.query_one("#img-cache", Static).update(
                f"  Build cache ({label}): {cache_size}"
            )
        else:
            self.query_one("#img-cache", Static).update(f"  Build cache ({label}): —")

    def _refresh_status(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        build = svc.check_image()
        services = svc.service_state.check_all()
        gw, sg, rag, cp = services[0], services[1], services[2], services[3]
        sidecar = svc.service_state.check_sidecar()

        config_text = f"  ({build.config.summary()})" if build.config.cpu else ""
        self.query_one("#img-detail", Static).update(
            f"  Status: {build.status}"
            + (f"  Size: {build.size}" if build.size else "")
            + (f"  ID: {build.image_id}" if build.image_id else "")
            + config_text
        )
        self._refresh_cache_size()
        self.query_one("#svc-gw", Static).update(
            f"  Gateway:  {gw.detail or gw.status}"
        )
        self.query_one("#svc-sg", Static).update(
            f"  Stargate: {sg.detail or sg.status}"
        )
        self.query_one("#svc-sidecar", Static).update(
            f"  Sidecar:  {sidecar.detail or sidecar.status}"
        )

        workspace_root = svc._root  # type: ignore[attr-defined]
        rag_cfg = is_rag_configured()
        cp_cfg = is_cloud_proxy_configured()
        mcp_cfg = is_mcp_configured(workspace_root)
        cortex_cfg = is_cortex_configured()
        bus_cfg = is_agent_bus_configured()

        if rag_cfg:
            self.query_one("#svc-rag", Static).update(
                f"  RAG:      {rag.detail or rag.status}"
            )
        else:
            self.query_one("#svc-rag", Static).update("  RAG:      (not configured)")

        if cp_cfg:
            self.query_one("#svc-cp", Static).update(
                f"  Cloud Px: {cp.detail or cp.status}"
            )
        else:
            self.query_one("#svc-cp", Static).update("  Cloud Px: (not configured)")

        mcp = svc.service_state.check_mcp() if mcp_cfg else None
        cortex = svc.service_state.check_cortex_api() if cortex_cfg else None
        agent_bus = svc.service_state.check_agent_bus() if bus_cfg else None

        if mcp is not None:
            self.query_one("#svc-mcp", Static).update(
                f"  MCP:      {mcp.detail or mcp.status}"
            )
            mcp_up = mcp.status.value == "running"
            self.query_one("#btn-start-mcp", Button).disabled = mcp_up
            self.query_one("#btn-stop-mcp", Button).disabled = not mcp_up
        else:
            self.query_one("#svc-mcp", Static).update("  MCP:      (not configured)")
            self.query_one("#btn-start-mcp", Button).disabled = True
            self.query_one("#btn-stop-mcp", Button).disabled = True

        if cortex is not None:
            self.query_one("#svc-cortex", Static).update(
                f"  Cortex:   {cortex.detail or cortex.status}"
            )
            cortex_up = cortex.status.value == "running"
            self.query_one("#btn-start-cortex", Button).disabled = cortex_up
            self.query_one("#btn-stop-cortex", Button).disabled = not cortex_up
        else:
            self.query_one("#svc-cortex", Static).update("  Cortex:   (not configured)")
            self.query_one("#btn-start-cortex", Button).disabled = True
            self.query_one("#btn-stop-cortex", Button).disabled = True

        if agent_bus is not None:
            self.query_one("#svc-agentbus", Static).update(
                f"  AgentBus: {agent_bus.detail or agent_bus.status}"
            )
            bus_up = agent_bus.status.value == "running"
            self.query_one("#btn-start-agentbus", Button).disabled = bus_up
            self.query_one("#btn-stop-agentbus", Button).disabled = not bus_up
        else:
            self.query_one("#svc-agentbus", Static).update(
                "  AgentBus: (not configured)"
            )
            self.query_one("#btn-start-agentbus", Button).disabled = True
            self.query_one("#btn-stop-agentbus", Button).disabled = True

        events = svc.service_state.check_event_service()
        self.query_one("#svc-events", Static).update(
            f"  Events:   {events.detail or events.status}"
        )

        gw_exists = gw.status.value != "stopped"
        sg_up = sg.status.value == "running"
        # If service is unhealthy but not stopped (e.g., stale PID/socket), show Stop.
        rag_up = rag_cfg and rag.status.value != "stopped"
        cp_up = cp_cfg and cp.status.value != "stopped"
        sidecar_up = sidecar.status.value == "running"
        events_up = events.status.value == "running"
        self.query_one("#btn-start-gw", Button).disabled = gw_exists
        self.query_one("#btn-stop-gw", Button).disabled = not gw_exists
        self.query_one("#btn-start-sg", Button).disabled = sg_up
        self.query_one("#btn-stop-sg", Button).disabled = not sg_up
        self.query_one("#btn-start-rag", Button).disabled = rag_up
        self.query_one("#btn-stop-rag", Button).disabled = not rag_up
        self.query_one("#btn-start-cp", Button).disabled = cp_up
        self.query_one("#btn-stop-cp", Button).disabled = not cp_up
        self.query_one("#btn-start-sidecar", Button).disabled = sidecar_up
        self.query_one("#btn-stop-sidecar", Button).disabled = not sidecar_up
        self.query_one("#btn-start-events", Button).disabled = events_up
        self.query_one("#btn-stop-events", Button).disabled = not events_up

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

    def _selected_cache_target(self) -> str:
        val = self.query_one("#cache-target", Select).value
        return str(val) if val != Select.BLANK else "gateway"

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

    async def _start_cloud_proxy(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.start_cloud_proxy()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _stop_cloud_proxy(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.stop_cloud_proxy()
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

    async def _start_event_service(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.start_event_service()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _stop_event_service(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.stop_event_service()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _start_mcp(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.start_mcp()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _stop_mcp(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.stop_mcp()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _start_cortex_api(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.start_cortex_api()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _stop_cortex_api(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.stop_cortex_api()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _start_agent_bus(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.start_agent_bus()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _stop_agent_bus(self) -> None:
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.stop_agent_bus()
        self.query_one("#svc-log", LogStream).write_line(result)
        await asyncio.sleep(2)
        self._refresh_status()

    async def _prune_cache(self) -> None:
        log = self.query_one("#svc-log", LogStream)
        log.clear()
        target = self._selected_cache_target()
        label = _CACHE_TARGET_LABELS.get(target, target)
        log.write_line(f"Pruning builder-scoped cache for {label}...")
        svc = self.app.service_controller  # type: ignore[attr-defined]
        result = await svc.prune_build_cache(target=target)
        log.write_line(result)
        self.query_one("#btn-prune-cache", Button).disabled = False
        self._refresh_cache_size()
