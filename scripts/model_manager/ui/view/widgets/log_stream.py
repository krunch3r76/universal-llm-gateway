"""Reusable buffered log widget — thread-safe producer, timer-driven consumer.

Producers push lines via write_line() from any thread.  A periodic timer
flushes a capped batch into a Textual Log widget whose rendering is lazy
(only visible lines are rendered), so high-throughput output never starves
the event loop.

Utilities:
    strip_ansi       — remove ANSI escape sequences
    strip_formatting — remove ANSI escapes + Rich markup tags
"""

import asyncio
import re
from collections import deque
from collections.abc import AsyncIterator, Callable

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Log

# ── text utilities (importable) ──────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_MARKUP_RE = re.compile(r"\[/?\w[\w ]*\]|\[/\]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences."""
    return _ANSI_RE.sub("", text) if "\x1b" in text else text


def strip_formatting(text: str) -> str:
    """Remove ANSI escape sequences and Rich markup tags."""
    if "\x1b" in text:
        text = _ANSI_RE.sub("", text)
    if "[" in text:
        text = _MARKUP_RE.sub("", text)
    return text


# ── widget ───────────────────────────────────────────────────────────────────


class LogStream(Widget):
    """Buffered log display fed via a thread-safe deque.

    Args:
        max_lines:      Ring-buffer cap for both the deque and the Log widget.
        flush_interval: Seconds between timer-driven flushes (default 100 ms).
        flush_batch:    Max lines drained per flush tick.
        formatter:      Text transform applied on input (default strips ANSI +
                        Rich markup).  Pass ``None`` for raw pass-through.
    """

    DEFAULT_CSS = """
    LogStream {
        height: 1fr;
        border: solid $primary;
    }
    LogStream Log {
        height: 1fr;
    }
    """

    def __init__(
        self,
        *,
        max_lines: int = 5_000,
        flush_interval: float = 0.1,
        flush_batch: int = 200,
        formatter: Callable[[str], str] | None = strip_formatting,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._max_lines = max_lines
        self._flush_interval = flush_interval
        self._flush_batch = flush_batch
        self._formatter = formatter
        self._queue: deque[str] = deque(maxlen=max_lines)
        self._stopped = False

    def compose(self) -> ComposeResult:
        yield Log(id="log-output", max_lines=self._max_lines, auto_scroll=True)

    def on_mount(self) -> None:
        self.set_interval(self._flush_interval, self._flush)

    def on_unmount(self) -> None:
        self.stop()

    # ── public API ───────────────────────────────────────────────────────────

    def write_line(self, text: str) -> None:
        """Push a line to the display queue.  Thread-safe, non-blocking."""
        if self._stopped:
            return
        if self._formatter is not None:
            text = self._formatter(text)
        self._queue.append(text)

    async def stream_from(
        self, source: AsyncIterator[str], *, yield_every: int = 50
    ) -> None:
        """Drain *source* into the queue, yielding to the event loop periodically."""
        i = 0
        async for line in source:
            if self._stopped:
                break
            self.write_line(line)
            i += 1
            if i % yield_every == 0:
                await asyncio.sleep(0.001)

    def stop(self) -> None:
        """Stop accepting lines and discard queued output."""
        self._stopped = True
        self._queue.clear()

    def clear(self) -> None:
        """Clear display and queue, re-enable writes."""
        self._queue.clear()
        self._stopped = False
        self.query_one("#log-output", Log).clear()

    # ── internals ────────────────────────────────────────────────────────────

    def _flush(self) -> None:
        if not self._queue:
            return
        batch: list[str] = []
        while self._queue and len(batch) < self._flush_batch:
            try:
                batch.append(self._queue.popleft())
            except IndexError:
                break
        if batch:
            self.query_one("#log-output", Log).write_lines(batch)
