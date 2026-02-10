"""
Auto-flushing FileHandler for immediate log writes.

Python's standard FileHandler uses buffered I/O, which means log messages
may not be written to disk immediately. This handler flushes after each
emit() call, ensuring logs are written immediately.

This is critical for:
- Real-time log monitoring (tail -f)
- Debugging crashes (logs visible before process exits)
- Production systems where log visibility is important
"""

import logging


class AutoFlushFileHandler(logging.FileHandler):
    """
    FileHandler that flushes after each log message.

    Extends logging.FileHandler to automatically flush the stream after
    each emit() call, ensuring log messages are written to disk immediately
    rather than being buffered.

    This is a drop-in replacement for logging.FileHandler - all constructor
    arguments and behavior are identical, except for the automatic flushing.

    Example:
        >>> handler = AutoFlushFileHandler('/var/log/app.log')
        >>> logger.addHandler(handler)
        >>> logger.info('This message is immediately written to disk')
    """

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record and flush immediately.

        Args:
            record: Log record to emit
        """
        # Call parent emit (writes to file)
        super().emit(record)
        # Flush immediately to ensure write to disk
        self.flush()
