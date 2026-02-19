"""Remotes screen - configure remote GPU nodes in the federation."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from scripts.model_manager.topology import probe_federation_sources

from ...controller.topology import (
    add_remote,
    get_master_port,
    list_remotes,
    remove_remote,
)
from ..widgets.log_stream import LogStream


class RemotesScreen(Screen):
    """Configure remote GPU nodes in the federation topology.

    Operational concerns (deploy, build, restart) live on the Home topology panel.
    """

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
                yield Label("SSH user:")
                yield Input(id="inp-ssh-user", placeholder="username on remote")
            with Horizontal():
                yield Label("Network address:")
                yield Input(id="inp-address", placeholder="jupiter (or 192.168.1.50)")
            with Horizontal():
                yield Label("Model path:")
                yield Input(id="inp-model-path", placeholder="~/.models")
            with Horizontal():
                yield Button("Add Remote", id="btn-add", variant="success")
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
        sources = probe_federation_sources(get_master_port())
        for remote in remotes:
            url = remote.get("url", "")
            sid = str(remote.get("stargate_id", "?"))
            model_count = sources.get(sid)
            status = "running" if model_count is not None else "configured"
            detail = _status_display(status, model_count)
            table.add_row(sid, url or "?", detail)

    def _handle_add(self) -> None:
        hostname = self.query_one("#inp-hostname", Input).value.strip()
        ssh_user = self.query_one("#inp-ssh-user", Input).value.strip()
        address = self.query_one("#inp-address", Input).value.strip()
        model_path = self.query_one("#inp-model-path", Input).value.strip()
        log = self.query_one("#remote-log", LogStream)

        if not hostname:
            log.write_line("Hostname is required.")
            return
        if not ssh_user:
            log.write_line("SSH user is required.")
            return
        if not model_path:
            log.write_line("Model path is required.")
            return
        if not address:
            address = hostname

        try:
            result = add_remote(
                hostname=hostname,
                address=address,
                model_path=model_path,
                ssh_user=ssh_user,
            )
        except (FileNotFoundError, ValueError) as e:
            log.write_line(f"Error: {e}")
            return

        log.write_line(f"Added relay-{hostname} -> {ssh_user}@{address}")
        log.write_line(f"  Node env: {result['node_env_path']}")
        log.write_line("")
        log.write_line(f"Go to Home -> Rebuild + Deploy All to deploy to {hostname}.")
        log.write_line("")

        self.query_one("#inp-hostname", Input).value = ""
        self.query_one("#inp-ssh-user", Input).value = ""
        self.query_one("#inp-address", Input).value = ""
        self.query_one("#inp-model-path", Input).value = ""
        self._refresh_table()

    def _handle_remove(self) -> None:
        table = self.query_one("#remotes-table", DataTable)
        log = self.query_one("#remote-log", LogStream)

        cursor_row = table.cursor_row
        if cursor_row is None or table.row_count == 0:
            log.write_line("Select a remote to remove.")
            return

        row_data = table.get_row_at(cursor_row)
        stargate_id = str(row_data[0])
        if stargate_id == "(none)":
            return

        hostname = stargate_id.removeprefix("relay-")
        if remove_remote(hostname):
            log.write_line(f"Removed {stargate_id}")
        else:
            log.write_line(f"{stargate_id} not found in config.")
        self._refresh_table()


def _status_display(status: str, model_count: int | None) -> str:
    match status:
        case "running":
            models = f" ({model_count} models)" if model_count is not None else ""
            return f"● running{models}"
        case "unreachable":
            return "◌ unreachable"
        case _:
            return f"○ {status}"
