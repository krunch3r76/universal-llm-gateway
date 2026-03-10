"""Catalog screen - browse and manage models."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Select, Static

from ..widgets.model_table import ModelTable


class CatalogScreen(Screen):
    """Browse static + local catalog models."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
        Binding("d", "download_selected", "Download"),
        Binding("m", "measure_selected", "Measure"),
        Binding("r", "refresh_catalog", "Refresh"),
    ]

    DEFAULT_CSS = """
    CatalogScreen {
        layout: vertical;
    }
    #filter-row {
        height: 3;
        padding: 0 2;
    }
    #filter-row Select {
        width: 30;
        margin: 0 1;
    }
    #detail-panel {
        height: auto;
        max-height: 12;
        padding: 1 2;
        border-top: solid $surface-lighten-2;
    }
    #action-row {
        height: 3;
        padding: 0 2;
        dock: bottom;
    }
    #action-row Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="filter-row"):
            yield Label("Filter: ")
            yield Select(
                [("All Domains", "all")],
                id="domain-filter",
                value="all",
            )
            yield Select(
                [("All Engines", "all")],
                id="engine-filter",
                value="all",
            )
        yield ModelTable(id="model-table")
        with Vertical(id="detail-panel"):
            yield Static("Select a model for details", id="model-detail")
        with Horizontal(id="action-row"):
            yield Button("Download [d]", id="btn-download", variant="primary")
            yield Button("Measure [m]", id="btn-measure")
            yield Button("Back [esc]", id="btn-back")
        yield Footer()

    def on_mount(self) -> None:
        self._load_filters()
        self._load_models()

    def on_screen_resume(self) -> None:
        """Re-scan catalog from disk and re-check when returning to Catalog."""
        catalog = self.app.catalog  # type: ignore[attr-defined]
        catalog.refresh()
        self._load_filters()
        domain = self.query_one("#domain-filter", Select).value
        engine = self.query_one("#engine-filter", Select).value
        self._load_models(
            domain=str(domain) if domain != Select.BLANK else "all",
            engine=str(engine) if engine != Select.BLANK else "all",
        )
        self._refresh_detail()

    def _load_filters(self) -> None:
        catalog = self.app.catalog  # type: ignore[attr-defined]
        domains = catalog.get_domains()
        engines = catalog.get_engines()

        domain_select = self.query_one("#domain-filter", Select)
        domain_select._options = [("All Domains", "all")] + [  # type: ignore[assignment]
            (d, d) for d in domains
        ]

        engine_select = self.query_one("#engine-filter", Select)
        engine_select._options = [("All Engines", "all")] + [  # type: ignore[assignment]
            (e, e) for e in engines
        ]

    def _load_models(self, domain: str = "all", engine: str = "all") -> None:
        catalog = self.app.catalog  # type: ignore[attr-defined]
        models = list(catalog.models.values())
        if domain != "all":
            models = [m for m in models if m.domain == domain]
        if engine != "all":
            models = [m for m in models if m.engine == engine]
        models.sort(key=lambda m: (m.domain, m.engine, m.model_id))
        checker = self.app.onboarding.check_downloaded  # type: ignore[attr-defined]
        self.query_one("#model-table", ModelTable).load_models(
            models, is_downloaded=checker
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        domain = self.query_one("#domain-filter", Select).value
        engine = self.query_one("#engine-filter", Select).value
        self._load_models(
            domain=str(domain) if domain != Select.BLANK else "all",
            engine=str(engine) if engine != Select.BLANK else "all",
        )

    def on_model_table_model_selected(self, event: ModelTable.ModelSelected) -> None:
        self._selected_model_id = event.model_id
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        model_id = getattr(self, "_selected_model_id", None)
        if not model_id:
            return
        catalog = self.app.catalog  # type: ignore[attr-defined]
        model = catalog.get(model_id)
        if not model:
            return
        downloaded = self.app.onboarding.check_downloaded(model)  # type: ignore[attr-defined]
        dl_status = "[green]downloaded[/]" if downloaded else "[red]not downloaded[/]"
        detail = (
            f"[b]{model.display_name}[/b]  ({model.model_id})\n"
            f"  Schema: {model.schema}  Format: {model.format}  "
            f"Quant: {model.quant or '—'}  Params: {model.parameters_m}M\n"
            f"  HF Repo: {model.hf_repo or '—'}  "
            f"Size: {model.size_display}  Status: {dl_status}\n"
            f"  GPU profiles: {'Yes' if model.has_gpu_profiles else 'No'}  "
            f"CPU profiles: {'Yes' if model.has_cpu_profiles else 'No'}"
        )
        self.query_one("#model-detail", Static).update(detail)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-download":
                self.action_download_selected()
            case "btn-measure":
                self.action_measure_selected()
            case "btn-back":
                self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_download_selected(self) -> None:
        model_id = getattr(self, "_selected_model_id", None)
        if model_id:
            self.app.push_screen("download", {"model_id": model_id})  # type: ignore[arg-type]

    def action_measure_selected(self) -> None:
        model_id = getattr(self, "_selected_model_id", None)
        if model_id:
            self.app.push_screen("measure", {"model_id": model_id})  # type: ignore[arg-type]

    def action_refresh_catalog(self) -> None:
        catalog = self.app.catalog  # type: ignore[attr-defined]
        catalog.refresh()
        self._load_models()
