"""Home screen - topology command center with onboarding steps."""

import logging
import os
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from scripts.model_manager.topology import build_snapshot

from ..widgets.topology_panel import TopologyPanel

logger = logging.getLogger(__name__)


class StepIndicator(Static):
    """Onboarding step — clickable, navigates to the relevant screen."""

    DEFAULT_CSS = """
    StepIndicator {
        width: 1fr;
        height: 3;
        padding: 0 1;
        content-align: center middle;
        border: solid $surface-lighten-2;
    }
    StepIndicator.done {
        border: solid green;
        color: green;
    }
    StepIndicator.todo {
        border: solid red;
        color: red;
    }
    StepIndicator:hover {
        border: solid $primary;
    }
    """

    class Clicked(Message):
        """Posted when a step indicator is clicked."""

        def __init__(self, step_id: str) -> None:
            self.step_id = step_id
            super().__init__()

    def on_click(self) -> None:
        if self.id:
            self.post_message(self.Clicked(self.id))


class HomeScreen(Screen):
    """Dashboard showing onboarding progress and quick actions."""

    BINDINGS = [
        Binding("c", "nav_catalog", "Catalog"),
        Binding("d", "nav_download", "Download"),
        Binding("s", "nav_services", "Services"),
        Binding("r", "nav_remotes", "Remotes"),
        Binding("f", "nav_footprint", "Footprint"),
        Binding("e", "nav_settings", "Settings"),
        Binding("q", "quit", "Quit"),
    ]

    DEFAULT_CSS = """
    HomeScreen {
        layout: vertical;
    }
    #welcome {
        width: 100%;
        height: auto;
        padding: 1 2;
        text-align: center;
        text-style: bold;
    }
    #steps-row {
        width: 100%;
        height: auto;
        padding: 1 2;
    }
    #next-action {
        width: 100%;
        height: auto;
        padding: 0 2 1 2;
        text-align: center;
    }
    #info-panel {
        width: 100%;
        height: auto;
        padding: 0 2;
    }
    #actions-panel {
        width: 100%;
        height: auto;
        padding: 1 2;
        align-horizontal: center;
    }
    #actions-row {
        width: auto;
        height: auto;
    }
    #actions-row Button {
        margin: 0 1;
    }
    .step-arrow {
        width: 3;
        height: 3;
        content-align: center middle;
        color: $text-muted;
    }
    """

    STEP_LABELS = {
        "step-config": "Configure",
        "step-build": "Build",
        "step-start": "Start",
        "step-download": "Download",
        "step-measure": "Measure",
    }

    STEP_HINTS = {
        "step-config": r"open Settings \[e] → set MODEL_PATH_ROOT",
        "step-build": r"press \[s] Services → Build Image",
        "step-start": r"press \[s] Services → Start Gateway + Stargate",
        "step-download": r"press \[d] Download → select a model → Download",
        "step-measure": r"press \[c] Catalog → select a model → Measure",
    }

    def action_nav_catalog(self) -> None:
        self.app.push_screen("catalog")

    def action_nav_services(self) -> None:
        self.app.push_screen("services")

    def action_nav_remotes(self) -> None:
        self.app.push_screen("remotes")

    def action_nav_footprint(self) -> None:
        self.app.push_screen("footprint")

    def action_nav_settings(self) -> None:
        self.app.push_screen("settings")

    def action_nav_download(self) -> None:
        self.app.push_screen("catalog")

    def on_step_indicator_clicked(self, event: StepIndicator.Clicked) -> None:
        match event.step_id:
            case "step-config":
                self.app.push_screen("settings")
            case "step-build" | "step-start":
                self.app.push_screen("services")
            case "step-download" | "step-measure":
                self.app.push_screen("catalog")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(
            "Universal LLM Gateway — Model Manager",
            id="welcome",
        )
        with Horizontal(id="steps-row"):
            yield StepIndicator("Configure", id="step-config", classes="todo")
            yield Static("→", classes="step-arrow")
            yield StepIndicator("Build", id="step-build", classes="todo")
            yield Static("→", classes="step-arrow")
            yield StepIndicator("Start", id="step-start", classes="todo")
            yield Static("→", classes="step-arrow")
            yield StepIndicator("Download", id="step-download", classes="todo")
            yield Static("→", classes="step-arrow")
            yield StepIndicator("Measure", id="step-measure", classes="todo")

        yield Static("", id="next-action", markup=True)

        with Vertical(id="info-panel"):
            yield Static("", id="img-status")
            yield Static("", id="model-paths")
            yield Static("", id="model-count")

        with Container(id="actions-panel"):
            with Horizontal(id="actions-row"):
                yield Button("Catalog [c]", id="btn-catalog", variant="primary")
                yield Button("Services [s]", id="btn-services")
                yield Button("Remotes [r]", id="btn-remotes")
                yield Button("Footprint [f]", id="btn-footprint")
                yield Button("Settings [e]", id="btn-settings")
                yield Button("Quit [q]", id="btn-quit", variant="error")

        yield TopologyPanel(id="topology-panel")

        yield Footer()

    _REFRESH_INTERVAL_SECONDS = 30

    def on_mount(self) -> None:
        self.set_interval(self._REFRESH_INTERVAL_SECONDS, self.refresh_status)
        self.refresh_status()

    def on_screen_resume(self) -> None:
        self.refresh_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-catalog":
                self.app.push_screen("catalog")
            case "btn-services":
                self.app.push_screen("services")
            case "btn-remotes":
                self.app.push_screen("remotes")
            case "btn-footprint":
                self.app.push_screen("footprint")
            case "btn-settings":
                self.app.push_screen("settings")
            case "btn-quit":
                self.app.exit()

    def refresh_status(self) -> None:
        self.run_worker(self._check_status(), exclusive=True)

    async def _check_status(self) -> None:
        app = self.app
        svc = app.service_controller  # type: ignore[attr-defined]
        build = svc.check_image()
        services = svc.service_state.check_all()

        gw = services[0]
        sg = services[1]

        workspace_root: Path = app._workspace_root  # type: ignore[attr-defined]
        try:
            snapshot = build_snapshot(workspace_root, services=services)
            topo_panel = self.query_one("#topology-panel", TopologyPanel)
            topo_panel.set_workspace_root(workspace_root)
            topo_panel.update_from_snapshot(snapshot)
            snapshot.write()
        except Exception as e:
            logger.error("Topology snapshot failed: %s", e)

        self.query_one("#img-status", Static).update(
            f"  Docker: {_status_icon(build.status)} {build.status}"
            + (f" ({build.size})" if build.size else "")
        )

        self._update_model_paths(app)
        self._update_model_count(app)

        status_bar = app.query_one("StatusBar")  # type: ignore[attr-defined]
        status_bar.gateway_status = gw.status
        status_bar.stargate_status = sg.status
        status_bar.image_status = build.status

        catalog = app.catalog  # type: ignore[attr-defined]
        self._update_steps(gw, sg, build, catalog)

    def _update_model_paths(self, app: object) -> None:
        search_paths = app.local_env.model_search_paths  # type: ignore[attr-defined]
        path_parts: list[str] = []
        for p in search_paths:
            if p.is_dir():
                if p.stat().st_uid == 0 and os.getuid() != 0:
                    uid, gid = os.getuid(), os.getgid()
                    path_parts.append(
                        f"{p} [red](root-owned — fix: sudo chown -R {uid}:{gid} {p})[/]"
                    )
                else:
                    path_parts.append(str(p))
            else:
                try:
                    p.mkdir(parents=True, exist_ok=True)
                    path_parts.append(f"{p} [green](created)[/]")
                except PermissionError:
                    path_parts.append(f"{p} [red](permission denied)[/]")
        self.query_one("#model-paths", Static).update(
            f"  [b]Model search path:[/b] {'  '.join(path_parts)}"
        )

    def _update_model_count(self, app: object) -> None:
        catalog = app.catalog  # type: ignore[attr-defined]
        check = app.onboarding.check_downloaded  # type: ignore[attr-defined]
        models = list(catalog.models.values())
        total = len(models)
        measured = sum(
            1 for m in models if (m.has_gpu_profiles or m.has_cpu_profiles) and check(m)
        )
        missing = sum(
            1
            for m in models
            if (m.has_gpu_profiles or m.has_cpu_profiles) and not check(m)
        )
        downloaded = sum(
            1
            for m in models
            if not (m.has_gpu_profiles or m.has_cpu_profiles) and check(m)
        )
        not_local = total - measured - missing - downloaded
        parts = [f"  Models: {total} in catalog —"]
        parts.append(f"[green]{measured} measured[/]")
        if downloaded:
            parts.append(f"[cyan]{downloaded} downloaded (not measured)[/]")
        if missing:
            parts.append(f"[yellow]{missing} missing[/]")
        parts.append(f"{not_local} not on disk")
        self.query_one("#model-count", Static).update("  ".join(parts))

    def _update_steps(self, gw, sg, build, catalog) -> None:  # type: ignore[no-untyped-def]
        env_local = self.app._workspace_root / ".env.local"  # type: ignore[attr-defined]
        model_path = self.app.local_env.model_path_root  # type: ignore[attr-defined]

        has_downloads = model_path.is_dir() and any(model_path.iterdir())
        has_measured = any(
            m.has_gpu_profiles or m.has_cpu_profiles for m in catalog.models.values()
        )

        gw_up = gw.status.value == "running"
        sg_up = sg.status.value == "running"

        step_done = {
            "step-config": env_local.exists(),
            "step-build": build.status.value == "built",
            "step-start": gw_up and sg_up,
            "step-download": has_downloads,
            "step-measure": has_measured,
        }
        for step_id, done in step_done.items():
            self._mark_step(step_id, done)

        if not gw_up and not sg_up:
            start_hint = r"press \[s] Services → Start Gateway + Stargate"
        elif not gw_up:
            start_hint = r"press \[s] Services → Start Gateway"
        else:
            start_hint = r"press \[s] Services → Start Stargate"

        self._update_next_action(step_done, start_hint)

    def _update_next_action(self, step_done: dict[str, bool], start_hint: str) -> None:
        widget = self.query_one("#next-action", Static)
        first_incomplete = next(
            (step_id for step_id, done in step_done.items() if not done), None
        )
        if first_incomplete is None:
            widget.update(
                "[green]✓ Setup complete[/]  ·  "
                "Send requests to [b]http://localhost:9999/v1/chat/completions[/]  ·  "
                "OpenAI-compatible API"
            )
        else:
            hints = {**self.STEP_HINTS, "step-start": start_hint}
            widget.update(f"[yellow]Next →[/] {hints[first_incomplete]}")

    def _mark_step(self, step_id: str, complete: bool) -> None:
        indicator = self.query_one(f"#{step_id}", StepIndicator)
        label = self.STEP_LABELS[step_id]
        if complete:
            indicator.update(f"[green]{label}[/]")
        else:
            indicator.update(f"[red]{label}[/]")
        indicator.set_class(complete, "done")
        indicator.set_class(not complete, "todo")


def _status_icon(status: str) -> str:
    match status:
        case "running" | "built":
            return "[green]●[/]"
        case "stopped" | "not_built":
            return "[red]○[/]"
        case "unhealthy" | "failed":
            return "[yellow]◌[/]"
        case _:
            return "[dim]?[/]"
