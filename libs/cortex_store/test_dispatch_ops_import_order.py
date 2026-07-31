"""Import-order regression: session_close_validation before dispatch_ops submodules."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_session_close_validation_imports_first_in_fresh_interpreter() -> None:
    script = textwrap.dedent(
        """
        import cortex_store.session_close_validation
        import cortex_store.dispatch_ops.ops_journals
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
