"""Concurrent tree-sitter parses must not share one Parser (friction:39420 r2)."""

from __future__ import annotations

from threading import Thread

from services.rag.chunkers.code_chunking import chunk_code_ast

_SAMPLES = {
    "alpha.py": "def alpha():\n    return 1\n",
    "beta.py": "class Beta:\n    def beta(self):\n        return 2\n",
    "gamma.py": "def gamma(x):\n    y = x + 1\n    return y\n\nclass Gamma:\n    pass\n",
    "delta.py": "def delta():\n    '''note'''\n    return None\n",
}


def _texts(path: str, content: str) -> list[str]:
    return [chunk.text for chunk in chunk_code_ast(path, content)]


def test_chunk_code_ast_concurrent_threads_match_sequential() -> None:
    """Several .py inputs parsed together match a single-threaded parse.

    A shared ``tree_sitter.Parser`` is unsafe when index workers call
    ``chunk_code_ast`` at once. Wrong trees or a parse exception fail this.
    """
    expected = {name: _texts(name, content) for name, content in _SAMPLES.items()}
    found: dict[str, list[str] | BaseException] = {}

    def _run(name: str, content: str) -> None:
        try:
            found[name] = _texts(name, content)
        except BaseException as exc:  # noqa: BLE001 — surface the worker failure
            found[name] = exc

    threads = [
        Thread(target=_run, args=(name, content)) for name, content in _SAMPLES.items()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(found) == set(_SAMPLES)
    for name, content_texts in expected.items():
        assert found[name] == content_texts
