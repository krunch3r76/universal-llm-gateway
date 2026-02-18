"""Settings screen - environment config and config file browser."""

import os
import subprocess

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from ...controller.config_browser import ConfigBrowser, ConfigFile


class SettingsScreen(Screen):
    """Configure model paths, environment, and browse config files."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
    ]

    DEFAULT_CSS = """
    SettingsScreen {
        layout: vertical;
    }
    #env-section {
        height: auto;
        padding: 1 2;
        border-bottom: solid $surface-lighten-2;
    }
    #env-section Input {
        width: 60;
        margin: 0 1;
    }
    #config-section {
        height: 1fr;
        padding: 1 2;
    }
    #settings-bottom {
        height: 3;
        padding: 0 2;
        dock: bottom;
    }
    #settings-bottom Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()

        with Vertical(id="env-section"):
            yield Static("[b]Environment (.env.local)[/b]", markup=True)
            with Horizontal():
                yield Label("Model Path: ")
                yield Input(id="model-path-input", placeholder="~/.models")
            with Horizontal():
                yield Label("HF Token:   ")
                yield Input(
                    id="hf-token-input", placeholder="(optional)", password=True
                )
            yield Button("Save", id="btn-save-env", variant="primary")
            yield Static("", id="save-status")

        with Vertical(id="config-section"):
            yield Static("[b]Configuration Files[/b]", markup=True)
            yield Static(
                "Select a file and press Enter to open in $EDITOR",
                id="config-hint",
            )
            yield DataTable(id="config-table")

        with Horizontal(id="settings-bottom"):
            yield Button("Back [esc]", id="btn-back")
        yield Footer()

    def on_mount(self) -> None:
        self._load_env()
        self._load_config_files()

    def _load_env(self) -> None:
        local_env = self.app.local_env  # type: ignore[attr-defined]
        self.query_one("#model-path-input", Input).value = str(
            local_env.model_path_root
        )
        token = local_env.hf_token or ""
        self.query_one("#hf-token-input", Input).value = token

    def _load_config_files(self) -> None:
        table = self.query_one("#config-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("Category", "File", "Description", "Exists")

        browser = ConfigBrowser(self.app._workspace_root)  # type: ignore[attr-defined]
        self._config_files: list[ConfigFile] = browser.list_all()

        for cf in self._config_files:
            rel = _relative_path(cf, self.app._workspace_root)  # type: ignore[attr-defined]
            exists = "[green]Yes[/]" if cf.exists else "[red]No[/]"
            table.add_row(cf.category, str(rel), cf.description, exists)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-save-env":
                self._save_env()
            case "btn-back":
                self.app.pop_screen()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.cursor_row is not None and event.cursor_row < len(self._config_files):
            cf = self._config_files[event.cursor_row]
            if cf.exists and cf.path.is_file():
                self._open_in_editor(cf.path)

    def _save_env(self) -> None:
        local_env = self.app.local_env  # type: ignore[attr-defined]
        model_path = self.query_one("#model-path-input", Input).value.strip()
        hf_token = self.query_one("#hf-token-input", Input).value.strip()

        if model_path:
            local_env.model_path_root = model_path  # type: ignore[assignment]
        if hf_token:
            local_env.set("HF_TOKEN", hf_token)

        local_env.save()
        self.query_one("#save-status", Static).update(
            f"[green]Saved to {local_env.path}[/]"
        )
        self.app.pop_screen()

    def _open_in_editor(self, path: object) -> None:
        editor = os.environ.get("EDITOR", "vi")
        with self.app.suspend():
            subprocess.run([editor, str(path)])


def _relative_path(cf: ConfigFile, workspace_root: object) -> object:
    try:
        return cf.path.relative_to(workspace_root)
    except ValueError:
        return cf.path
