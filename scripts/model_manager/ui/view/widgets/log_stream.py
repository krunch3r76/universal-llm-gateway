"""Log streaming widget for build/measure output."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog


class LogStream(Widget):
    """RichLog wrapper for streaming subprocess output."""

    DEFAULT_CSS = """
    LogStream {
        height: 1fr;
        border: solid $primary;
    }
    LogStream RichLog {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield RichLog(id="log-output", highlight=True, markup=True, wrap=True)

    def write_line(self, text: str) -> None:
        self.query_one("#log-output", RichLog).write(text)

    def clear(self) -> None:
        self.query_one("#log-output", RichLog).clear()
