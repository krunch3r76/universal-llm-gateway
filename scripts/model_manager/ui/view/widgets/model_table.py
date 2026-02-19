"""Model table widget for browsing the catalog."""

from collections.abc import Callable

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable

from ...model.catalog_state import ModelInfo


class ModelTable(Widget):
    """DataTable displaying catalog models with status indicators."""

    DEFAULT_CSS = """
    ModelTable {
        height: 1fr;
    }
    """

    class ModelSelected(Message):
        """Fired when user selects a model row."""

        def __init__(self, model_id: str) -> None:
            super().__init__()
            self.model_id = model_id

    def compose(self) -> ComposeResult:
        table = DataTable(id="model-dt")
        table.cursor_type = "row"
        table.zebra_stripes = True
        yield table

    def on_mount(self) -> None:
        table = self.query_one("#model-dt", DataTable)
        table.add_columns("Model ID", "Engine", "Quant", "Size", "Status", "GPU", "CPU")

    def load_models(
        self,
        models: list[ModelInfo],
        *,
        is_downloaded: Callable[[ModelInfo], bool] | None = None,
    ) -> None:
        table = self.query_one("#model-dt", DataTable)
        table.clear()
        for m in models:
            table.add_row(
                m.model_id,
                m.engine,
                m.quant or "—",
                m.size_display,
                _model_status(m, is_downloaded),
                "Y" if m.has_gpu_profiles else "—",
                "Y" if m.has_cpu_profiles else "—",
                key=m.model_id,
            )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self.post_message(self.ModelSelected(event.row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key and event.row_key.value:
            self.post_message(self.ModelSelected(event.row_key.value))


def _model_status(
    m: ModelInfo, is_downloaded: Callable[[ModelInfo], bool] | None
) -> str:
    """
    ∀ m: has_profiles(m) ⟹ was measured ⟹ file was present at some point.
    is_local alone is insufficient: migration creates local entries for all models.
    """
    on_disk = is_downloaded(m) if is_downloaded else False
    has_profiles = m.has_gpu_profiles or m.has_cpu_profiles or m.has_hybrid_profiles
    if has_profiles and on_disk:
        return "[green]measured[/]"
    if has_profiles and not on_disk:
        return "[yellow]missing[/]"
    if on_disk:
        return "[cyan]downloaded[/]"
    return "—"
