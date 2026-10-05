"""Start the closeout-memo delivery retry loop inside the Stargate process.

The combined CDP-owed + overdue file from the spec is split: this module
starts only the pipeline retry sweep. CDP producer and the overdue watch
are slice 2.
"""

from __future__ import annotations


def start_closeout_memo_retry_sweep() -> None:
    """Arm the 30s retry sweep once. Safe to call from lifespan and admit.

    Loads the handler package by file path so ``pipelines/closeout_memo`` does
    not shadow the ``libs/closeout_memo`` package under pytest importlib mode.
    """
    import importlib
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    for entry in (
        str(root / "libs"),
        str(root / "services" / "universal-stargate"),
        str(root),
    ):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    name = "_pipeline_handlers_closeout_memo_v1"
    if name not in sys.modules:
        handlers = root / "pipelines" / "closeout_memo" / "v1" / "handlers"
        spec = importlib.util.spec_from_file_location(
            name,
            handlers / "__init__.py",
            submodule_search_locations=[str(handlers), str(handlers.parent)],
        )
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    sweep = importlib.import_module(f"{name}.sweep")
    sweep.start_retry_sweep()
