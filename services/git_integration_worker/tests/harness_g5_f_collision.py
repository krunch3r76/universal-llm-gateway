"""G5 F-collision harness — non-collected (not test_*.py).

Run explicitly:
  python services/git_integration_worker/tests/harness_g5_f_collision.py

Arc agent-bus:6792. Criteria:
  cortex://notes/system/threads/6792-g5-f-collision-falsification-frame.md
  read_sha256=93c7bdefa47c64221745305b8c1a18a1164f167588132a1177aa783dd66644bb

Case bodies live in harness_g5_f_collision_cases.py. Encodes live failure modes
as non-collected provocations (AC5). Passing F-5 guard also collected in
test_cursor_auto_supersede.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

# Allow `python …/harness_g5_f_collision.py` from any cwd.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.git_integration_worker.tests.harness_g5_f_collision_cases import (  # noqa: E402
    provoke_f1,
    provoke_f2,
    provoke_f3,
    provoke_f4,
    provoke_f5,
)
from services.git_integration_worker.tests.harness_g5_f_collision_gate_cases import (  # noqa: E402
    provoke_f6a,
    provoke_f6b,
    provoke_f7,
)


def main() -> int:
    """Run all falsifiers; exit 2 on any reproduction (halt)."""
    order: list[tuple[str, Callable[[], int]]] = [
        ("F-5", provoke_f5),
        ("F-1", provoke_f1),
        ("F-2", provoke_f2),
        ("F-3", provoke_f3),
        ("F-4", provoke_f4),
        ("F-6a", provoke_f6a),
        ("F-6b", provoke_f6b),
        ("F-7", provoke_f7),
    ]
    verdicts: dict[str, str] = {}
    worst = 0
    for name, fn in order:
        rc = int(fn())
        verdicts[name] = "REPRODUCED" if rc != 0 else "SURVIVED"
        if rc != 0:
            worst = 2
    print("VERDICTS", verdicts)
    if worst != 0:
        print(
            "HALT: one or more falsifiers reproduced — "
            "see cortex://notes/system/threads/6792-g5-f-collision-gate-report.md"
        )
    return worst


if __name__ == "__main__":
    sys.exit(main())
