"""Home screen - topology command center with onboarding steps."""

import logging
import os

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Collapsible, Footer, Header, Label, Static

from scripts.model_manager.topology import build_snapshot

from ..widgets.topology_panel import TopologyPanel
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

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
        """Posted when a step indicator is clicked, carrying the ID of the clicked step."""

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
        Binding("t", "toggle_topology", "Topology"),
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
    #steps-compact {
        display: none;
        width: 100%;
        height: auto;
        padding: 0 2;
        text-align: center;
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
    .step-arrow {
        width: 3;
        height: 3;
        content-align: center middle;
        color: $text-muted;
    }
    #topo-collapsible {
        padding: 0 2;
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
        "step-config": r"open Settings \[e] → set model path",
        "step-build": r"press \[s] Services → Build Image",
        "step-start": r"press \[s] Services → Start Gateway + Stargate",
        "step-download": r"press \[d] Download → select a model → Download",
        "step-measure": r"press \[c] Catalog → select a model → Measure",
    }

    def action_nav_catalog(self) -> None:
        """Navigate to the catalog screen."""
        self.app.push_screen("catalog")

    def action_nav_services(self) -> None:
        """Navigate to the services screen."""
        self.app.push_screen("services")

    def action_nav_remotes(self) -> None:
        """Navigate to the remotes screen."""
        self.app.push_screen("remotes")

    def action_nav_footprint(self) -> None:
        """Navigate to the footprint screen."""
        self.app.push_screen("footprint")

    def action_nav_settings(self) -> None:
        """Navigate to the settings screen."""
        self.app.push_screen("settings")

    def action_nav_download_catalog(self) -> None:
        self.app.push_screen("catalog")

    def on_step_indicator_clicked(self, event: StepIndicator.Clicked) -> None:
        """Handle clicks on step indicators to navigate to relevant screens."""
        screen_map = {
            "step-config": "settings",
            "step-build": "services",
            "step-start": "services",
            "step-download": "catalog",
            "step-measure": "catalog",
        }
        if screen_name := screen_map.get(event.step_id):
            self.app.push_screen(screen_name)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(
            "Universal LLM Gateway — Model Manager",
            id="welcome",
        )
        with Horizontal(id="steps-row"):
            steps = list(self.STEP_LABELS.items())
            for i, (step_id, label) in enumerate(steps):
                yield StepIndicator(label, id=step_id, classes="todo")
                if i < len(steps) - 1:
                    yield Static("→", classes="step-arrow")

        yield Static("", id="steps-compact", markup=True)
        yield Static("", id="next-action", markup=True)

        with Vertical(id="info-panel"):
            yield Static("", id="img-status")
            yield Static("", id="model-paths")
            yield Static("", id="model-count")

        with Collapsible(title="Topology", id="topo-collapsible", collapsed=True):
            yield TopologyPanel(id="topology-panel")

        yield Footer()

    _REFRESH_INTERVAL_SECONDS = 30

    def on_mount(self) -> None:
        """Called when the widget is mounted. Sets up refresh interval and initial status."""
        self.set_interval(self._REFRESH_INTERVAL_SECONDS, self.refresh_status)
        self.refresh_status()

    def on_screen_resume(self) -> None:
        """Called when the screen is resumed. Refreshes the status."""
        self.refresh_status()

    def action_toggle_topology(self) -> None:
        """Toggle the collapsed state of the topology panel."""
        collapsible = self.query_one("#topo-collapsible", Collapsible)
        collapsible.collapsed = not collapsible.collapsed

    def on_topology_panel_deploy_state_changed(
        self, event: TopologyPanel.DeployStateChanged
    ) -> None:
        """Handles changes in the topology panel's deployment state."""
        if event.deploying:
            self.query_one("#topo-collapsible", Collapsible).collapsed = False
            return
        self.refresh_status()

    def refresh_status(self) -> None:
        """Refreshes the application status by running a background worker."""
        self.run_worker(self._check_status(), exclusive=True)

    async def _check_status(self) -> None:
        """Asynchronously checks the status of Docker, services, and models, then updates the UI."""
        app = self.app
        # Assuming app is of type 'YourAppClass'
        svc = app.service_controller
        build = svc.check_image()
        services = svc.service_state.check_all()

        gw = services[0]
        sg = services[1]

        workspace_root: Path = app._workspace_root
        try:
            snapshot = build_snapshot(workspace_root, services=services)
            topo_panel = self.query_one("#topology-panel", TopologyPanel)
            topo_panel.set_workspace_root(workspace_root)
            topo_panel.update_from_snapshot(snapshot)
            snapshot.write()
            self._update_topo_title(snapshot)
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

    def _update_topo_title(self, snapshot: object) -> None:
        statuses = [snapshot.master.status]
        if snapshot.local_edge:
            statuses.append(snapshot.local_edge.status)
        statuses.extend(r.status for r in snapshot.remotes)
        total = len(statuses)
        running = sum(1 for s in statuses if s == "running")
        self.query_one(
            "#topo-collapsible", Collapsible
        ).title = f"Topology ({total} nodes: {running} running)"

    def _update_model_paths(self, app: object) -> None:
        search_paths = app.local_env.model_search_paths
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
        catalog = app.catalog
        check = app.onboarding.check_downloaded
        models = list(catalog.models.values())
        total = len(models)
        measured = 0
        missing = 0
        downloaded = 0
        for m in models:
            has_profiles = m.has_gpu_profiles or m.has_cpu_profiles
            is_checked = check(m)
            if has_profiles and is_checked:
                measured += 1
            elif has_profiles and not is_checked:
                missing += 1
            elif not has_profiles and is_checked:
                downloaded += 1
        not_local = total - measured - missing - downloaded
        parts = [f"  Models: {total} in catalog —"]
        parts.append(f"[green]{measured} measured[/]")
        if downloaded:
            parts.append(f"[cyan]{downloaded} downloaded (not measured)[/]")
        if missing:
            parts.append(f"[yellow]{missing} missing[/]")
        parts.append(f"{not_local} not on disk")
        self.query_one("#model-count", Static).update("  ".join(parts))

    def _update_steps(
        self, gw: object, sg: object, build: object, catalog: object
    ) -> None:
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

        all_done = all(step_done.values())
        self.query_one("#steps-row").display = not all_done
        compact = self.query_one("#steps-compact", Static)
        compact.display = all_done
        if all_done:
            compact.update("[green]✓ Configure → Build → Start → Download → Measure[/]")
        else:
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
        """Updates the 'next action' hint based on the completion status of onboarding steps."""
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
        """Visually marks an onboarding step as complete or incomplete."""
        indicator = self.query_one(f"#{step_id}", StepIndicator)
        label = self.STEP_LABELS[step_id]
        if complete:
            indicator.update(f"[green]{label}[/]")
        else:
            indicator.update(f"[red]{label}[/]")
        indicator.set_class(complete, "done")
        indicator.set_class(not complete, "todo")


def _status_icon(status: str) -> str:
    """Returns a rich text icon string based on the provided status."""
    match status:
        case "running" | "built":
            return "[green]●[/]"
        case "building":
            return "[cyan]◎[/]"
        case "stopped" | "not_built":
            return "[red]○[/]"
        case "unhealthy" | "failed":
            return "[yellow]◌[/]"
        case _:
            return "[dim]?[/]"
