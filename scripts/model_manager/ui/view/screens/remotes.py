"""Remotes screen - add/remove remote GPU nodes in the federation."""

from pathlib import Path
from urllib.parse import urlparse

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from ...controller.topology import (
    add_remote,
    deploy_remote,
    list_remotes,
    remove_remote,
)
from ..widgets.log_stream import LogStream


class RemotesScreen(Screen):
    """Manage remote GPU nodes in the federation topology."""

    BINDINGS = [
        Binding("escape", "pop_screen", "Back", priority=True),
    ]

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    DEFAULT_CSS = """
    RemotesScreen {
        layout: vertical;
    }
    #remotes-list {
        height: auto;
        max-height: 12;
        padding: 1 2;
    }
    #add-form {
        height: auto;
        padding: 1 2;
        border-top: solid $surface-lighten-2;
    }
    #add-form Input {
        width: 50;
        margin: 0 1;
    }
    #add-form Label {
        width: 18;
    }
    #add-form Checkbox {
        width: auto;
        margin: 0 1;
    }
    #add-form Horizontal {
        height: 3;
    }
    #remote-log {
        height: 1fr;
    }
    #remotes-bottom {
        height: 3;
        padding: 0 2;
        dock: bottom;
    }
    #remotes-bottom Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()

        with Vertical(id="remotes-list"):
            yield Static("[b]Federation Remotes[/b]", markup=True)
            yield DataTable(id="remotes-table")

        with Vertical(id="add-form"):
            yield Static("[b]Add Remote Node[/b]", markup=True)
            with Horizontal():
                yield Label("Hostname:")
                yield Input(id="inp-hostname", placeholder="jupiter")
            with Horizontal():
                yield Label("Network address:")
                yield Input(id="inp-address", placeholder="jupiter (or 192.168.1.50)")
            with Horizontal():
                yield Label("Model path:")
                yield Input(id="inp-model-path", placeholder="/mnt/models")
            with Horizontal():
                yield Button("Add Remote", id="btn-add", variant="success")
                yield Checkbox("Build", id="chk-build")
                yield Button("Deploy", id="btn-deploy", variant="primary")
                yield Button("Redeploy", id="btn-redeploy", variant="warning")
                yield Button("Remove", id="btn-remove", variant="error")

        yield LogStream(id="remote-log")

        with Horizontal(id="remotes-bottom"):
            yield Button("Refresh", id="btn-refresh")
            yield Button("Back [esc]", id="btn-back")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#remotes-table", DataTable)
        table.add_columns("Stargate ID", "URL", "Status")
        self._refresh_table()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-add":
                self._handle_add()
            case "btn-deploy":
                self._handle_deploy()
            case "btn-redeploy":
                self._handle_redeploy()
            case "btn-remove":
                self._handle_remove()
            case "btn-refresh":
                self._refresh_table()
            case "btn-back":
                self.app.pop_screen()

    def _refresh_table(self) -> None:
        table = self.query_one("#remotes-table", DataTable)
        table.clear()
        remotes = list_remotes()
        if not remotes:
            table.add_row("(none)", "", "")
            return
        for remote in remotes:
            table.add_row(
                remote.get("stargate_id", "?"),
                remote.get("url", "?"),
                "configured",
            )

    def _handle_add(self) -> None:
        hostname = self.query_one("#inp-hostname", Input).value.strip()
        address = self.query_one("#inp-address", Input).value.strip()
        model_path = self.query_one("#inp-model-path", Input).value.strip()
        log = self.query_one("#remote-log", LogStream)

        if not hostname:
            log.write_line("[red]Hostname is required.[/]")
            return
        if not model_path:
            log.write_line("[red]Model path is required.[/]")
            return
        if not address:
            address = hostname

        try:
            result = add_remote(
                hostname=hostname,
                address=address,
                model_path=model_path,
            )
        except (FileNotFoundError, ValueError) as e:
            log.write_line(f"[red]{e}[/]")
            return

        log.write_line(f"[green]Added relay-{hostname} → {address}[/]")
        log.write_line(f"  Node env: {result['node_env_path']}")
        log.write_line("")
        log.write_line(
            f"[b]Next:[/b] Click [b]Deploy[/b] to push repo and node env to {hostname}, then start relay there."
        )
        log.write_line("")

        self.query_one("#inp-hostname", Input).value = ""
        self.query_one("#inp-address", Input).value = ""
        self.query_one("#inp-model-path", Input).value = ""
        self._refresh_table()

    def _handle_remove(self) -> None:
        table = self.query_one("#remotes-table", DataTable)
        log = self.query_one("#remote-log", LogStream)

        cursor_row = table.cursor_row
        if cursor_row is None or table.row_count == 0:
            log.write_line("[yellow]Select a remote to remove.[/]")
            return

        row_data = table.get_row_at(cursor_row)
        stargate_id = str(row_data[0])
        if stargate_id == "(none)":
            return

        hostname = stargate_id.removeprefix("relay-")
        if remove_remote(hostname):
            log.write_line(f"[green]Removed {stargate_id}[/]")
        else:
            log.write_line(f"[yellow]{stargate_id} not found in config.[/]")
        self._refresh_table()

    def _selected_remote(self) -> tuple[str, str] | None:
        """Get (hostname, address) for the selected table row, or None."""
        table = self.query_one("#remotes-table", DataTable)
        log = self.query_one("#remote-log", LogStream)
        cursor_row = table.cursor_row
        if cursor_row is None or table.row_count == 0:
            log.write_line("[yellow]Select a remote first.[/]")
            return None
        row_data = table.get_row_at(cursor_row)
        stargate_id = str(row_data[0])
        url_str = str(row_data[1])
        if stargate_id == "(none)" or not url_str:
            log.write_line("[yellow]Select a remote first.[/]")
            return None
        hostname = stargate_id.removeprefix("relay-")
        parsed = urlparse(url_str)
        address = (
            parsed.hostname or parsed.netloc.split(":")[0] if parsed.netloc else ""
        )
        if not address:
            log.write_line(f"[red]Could not parse address from URL: {url_str}[/]")
            return None
        return hostname, address

    def _handle_deploy(self) -> None:
        selected = self._selected_remote()
        if not selected:
            return
        hostname, address = selected
        build = self.query_one("#chk-build", Checkbox).value
        workspace_root: Path = self.app._workspace_root  # type: ignore[attr-defined]
        self.run_worker(
            self._deploy(hostname, address, workspace_root, build=build),
            exclusive=True,
        )

    def _handle_redeploy(self) -> None:
        selected = self._selected_remote()
        if not selected:
            return
        hostname, address = selected
        build = self.query_one("#chk-build", Checkbox).value
        workspace_root: Path = self.app._workspace_root  # type: ignore[attr-defined]
        self.run_worker(
            self._deploy(hostname, address, workspace_root, build=build, restart=True),
            exclusive=True,
        )

    async def _deploy(
        self,
        hostname: str,
        address: str,
        workspace_root: Path,
        *,
        build: bool = False,
        restart: bool = False,
    ) -> None:
        log = self.query_one("#remote-log", LogStream)
        log.clear()
        async for line in deploy_remote(
            hostname=hostname,
            address=address,
            workspace_root=workspace_root,
            build=build,
            restart=restart,
        ):
            log.write_line(line)
