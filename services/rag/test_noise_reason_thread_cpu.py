"""noise_reason_timed reports this thread's CPU, not the process (friction:39420)."""

from __future__ import annotations

import threading
import time

from services.rag.chunk_filters import noise_reason_timed


def test_cpu_ms_ignores_concurrent_burner_threads() -> None:
    """Other threads burning CPU must not inflate the reported cpu_ms."""
    stop = threading.Event()

    def _burn() -> None:
        while not stop.is_set():
            pass

    burners = [threading.Thread(target=_burn, daemon=True) for _ in range(3)]
    for thread in burners:
        thread.start()
    try:
        text = "a, " * 200_000
        proc0 = time.process_time()
        _reason, cpu_ms = noise_reason_timed(text)
        proc_ms = (time.process_time() - proc0) * 1000.0
    finally:
        stop.set()
        for thread in burners:
            thread.join(timeout=1.0)
    assert proc_ms > cpu_ms + 5.0, (
        f"burners did not show up in process time proc_ms={proc_ms:.1f} cpu_ms={cpu_ms:.1f}"
    )
