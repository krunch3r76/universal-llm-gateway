"""Footprint screen — deployment files, config resolution, and clean slate."""

import asyncio
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from ...controller.footprint import ConfigResolution, FootprintInspector


class FootprintScreen(Screen):
    """Shows files created by ./manage, config resolution, and clean slate reset."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
    ]

    DEFAULT_CSS = """
    FootprintScreen { layout: vertical; }
    #fp-managed { height: 1fr; min-height: 10; padding: 1 2; }
    #fp-resolution {
        height: auto; padding: 1 2;
        border-top: solid $surface-lighten-2;
    }
    #fp-clean-slate {
        height: auto; padding: 1 2;
        border-top: solid $surface-lighten-2;
    }
    #fp-clean-detail { display: none; padding: 1 0; }
    #fp-clean-detail.visible { display: block; }
    #fp-confirm-row { height: auto; padding: 1 0; }
    #fp-confirm-row Input { width: 30; margin: 0 1; }
    #fp-bottom {
        height: 3; padding: 0 2; dock: bottom;
    }
    #fp-bottom Button { margin: 0 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header()

        with VerticalScroll():
            with Vertical(id="fp-managed"):
                yield Static("[b]Managed Files[/b] — created by ./manage", markup=True)
                yield DataTable(id="fp-table")

            with Vertical(id="fp-resolution"):
                yield Static(
                    "[b]Config Resolution[/b] — environment and config chain",
                    markup=True,
                )
                yield Static("", id="fp-resolution-detail", markup=True)

            with Vertical(id="fp-clean-slate"):
                yield Button("Clean Slate", id="btn-clean-slate", variant="warning")
                with Vertical(id="fp-clean-detail"):
                    yield Static("", id="fp-commands-text", markup=True)
                    yield Static(
                        "\nCopy commands above to run manually, "
                        "or type [b]delete[/b] below to execute from this TUI:",
                        markup=True,
                    )
                    with Horizontal(id="fp-confirm-row"):
                        yield Input(
                            id="fp-confirm-input",
                            placeholder="Type 'delete' to confirm",
                        )
                    yield Static("", id="fp-clean-status", markup=True)

        with Horizontal(id="fp-bottom"):
            yield Button("Back [esc]", id="btn-back")
        yield Footer()

    def on_mount(self) -> None:
        self._root: Path = self.app._workspace_root  # type: ignore[attr-defined]
        self._inspector = FootprintInspector(self._root)
        table = self.query_one("#fp-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("", "Category", "Path", "Purpose")
        self._refresh_table()
        self._populate_resolution()

    def _refresh_table(self) -> None:
        table = self.query_one("#fp-table", DataTable)
        table.clear()
        for mf in self._inspector.managed_files():
            icon = "[green]●[/]" if mf.exists else "[dim]○[/]"
            table.add_row(
                icon, mf.category, _display_path(mf.path, self._root), mf.purpose
            )

    def _populate_resolution(self) -> None:
        res = self._inspector.config_resolution()
        text = _format_resolution(res, self._root)
        self.query_one("#fp-resolution-detail", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-clean-slate":
                self._toggle_clean_slate()
            case "btn-back":
                self.app.pop_screen()

    def _toggle_clean_slate(self) -> None:
        panel = self.query_one("#fp-clean-detail")
        if panel.has_class("visible"):
            panel.remove_class("visible")
            return
        commands = self._inspector.clean_slate_commands()
        self.query_one("#fp-commands-text", Static).update(commands)
        self.query_one("#fp-clean-status", Static).update("")
        self.query_one("#fp-confirm-input", Input).value = ""
        panel.add_class("visible")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "fp-confirm-input":
            return
        if event.value.strip().lower() != "delete":
            self.query_one("#fp-clean-status", Static).update(
                "[red]Type exactly 'delete' to confirm, "
                "or copy commands above to run manually.[/]"
            )
            return
        event.input.value = ""
        self.run_worker(self._execute_clean_slate(), exclusive=True)

    async def _execute_clean_slate(self) -> None:
        status = self.query_one("#fp-clean-status", Static)
        status.update("[yellow]Executing clean slate...[/]")
        commands = self._inspector.clean_slate_commands()
        for line in commands.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            proc = await asyncio.create_subprocess_shell(
                line,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await proc.communicate()
        status.update(
            "[green]Clean slate complete. Restart ./manage to rebuild from scratch.[/]"
        )
        self._refresh_table()
        self._populate_resolution()

    def action_go_back(self) -> None:
        self.app.pop_screen()


def _format_resolution(res: ConfigResolution, workspace_root: Path) -> str:
    """Format ConfigResolution as rich markup text."""
    parts: list[str] = ["[b]Environment (layered, last wins):[/b]"]
    accumulated: set[str] = set()

    for layer in res.env_layers:
        if layer.path is None:
            if layer.entries:
                parts.append(f"  [dim]Layer:[/dim] {layer.name}")
                for key, value in layer.entries.items():
                    parts.append(f"    {key} = {_mask_secret(key, value)}")
                    accumulated.add(key)
            else:
                parts.append(f"  [dim]Layer:[/dim] {layer.name} [dim](base)[/dim]")
        else:
            icon = "[green]✓[/]" if layer.path.exists() else "[red]✗[/]"
            parts.append(f"  {icon} {layer.name}")
            for key, value in layer.entries.items():
                display_val = _mask_secret(key, value)
                tag = " [yellow](overrides)[/]" if key in accumulated else ""
                parts.append(f"    {key} = {display_val}{tag}")
                accumulated.add(key)

    parts.append("")
    parts.append("[b]Stargate:[/b]")
    _cfg_line(parts, res.stargate_config, "TUI-generated, editable", workspace_root)
    _cfg_line(
        parts, res.edge_template, "git-tracked, container template", workspace_root
    )

    parts.append("")
    parts.append("[b]Docker Compose:[/b]")
    _cfg_line(parts, res.compose_file, "parameterized", workspace_root)
    _cfg_line(parts, res.engine_env, "engine optimization vars", workspace_root)
    parts.append(f"  Project name: {res.project_name}")

    return "\n".join(parts)


def _cfg_line(parts: list[str], path: Path, note: str, workspace_root: Path) -> None:
    icon = "[green]✓[/]" if path.exists() else "[red]✗[/]"
    parts.append(f"  {icon} {_display_path(path, workspace_root)} [dim]({note})[/dim]")


def _display_path(path: Path, workspace_root: Path | None = None) -> str:
    """Show path relative to ~ or workspace for readability."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        pass
    if workspace_root:
        try:
            return str(path.relative_to(workspace_root))
        except ValueError:
            pass
    return str(path)


def _mask_secret(key: str, value: str) -> str:
    """Mask sensitive values (tokens, keys, secrets)."""
    upper = key.upper()
    if any(s in upper for s in ("TOKEN", "KEY", "SECRET")):
        return value[:4] + "****" if len(value) > 8 else "****"
    return value
