"""AC4 import audit — no services.* from maestro_induct trees."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PROBE = r'''
import importlib.util, json, pathlib, sys
import maestro_induct
out = {"maestro_induct": maestro_induct.__file__}
if "--handlers" in sys.argv:
    init = pathlib.Path.cwd() / "pipelines/maestro_induct/v1/handlers/__init__.py"
    spec = importlib.util.spec_from_file_location(
        "maestro_induct_handlers", init, submodule_search_locations=[str(init.parent)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["maestro_induct_handlers"] = mod
    spec.loader.exec_module(mod)
    out["builtin"] = sys.modules["systems.pipeline.core.handlers.builtin"].__file__
out["services"] = sorted(k for k in sys.modules if k == "services" or k.startswith("services."))
print(json.dumps(out))
'''


def _probe(*, handlers: bool) -> dict:
    paths = [REPO / "libs"] + ([REPO / "services" / "universal-stargate"] if handlers else [])
    env = {
        **os.environ,
        "PROJECT_ROOT": str(REPO),
        "PYTHONPATH": os.pathsep.join(str(p) for p in paths),
    }
    argv = [sys.executable, "-c", PROBE] + (["--handlers"] if handlers else [])
    r = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _walk_imports(root: Path) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "services" or alias.name.startswith("services."):
                        hits.append(f"{path}:{alias.name}")
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "services" or node.module.startswith("services."):
                    hits.append(f"{path}:{node.module}")
    return hits


def test_ast_no_services_imports() -> None:
    assert _walk_imports(REPO / "libs" / "maestro_induct") == []
    assert _walk_imports(REPO / "pipelines" / "maestro_induct") == []


def test_runtime_modules_no_services() -> None:
    a = _probe(handlers=False)
    assert a["services"] == []
    assert str(REPO / "libs") in a["maestro_induct"]
    b = _probe(handlers=True)
    assert b["services"] == []
    assert str(REPO / "libs") in b["maestro_induct"]
    assert "universal-stargate" in b["builtin"]


def test_detector_flags_services_import() -> None:
    src = "from services.foo import bar\n"
    tree = ast.parse(src)
    found = any(isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("services") for n in ast.walk(tree))
    assert found
