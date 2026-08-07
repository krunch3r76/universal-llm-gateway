"""Module-path import verification for consumer nomination at residue mint.

Package-level ``CONSUMERS`` and ownership maps nominate candidates; this module
decides whether a candidate's **import closure** reaches the landed file's
module. That stops sibling-module over-nomination (oracle:
``deploy_identity.code_ref_relation`` vs mcp importing only ``code_version``).

Granularity is file/module — not package. The harvest helper
``propagation_libs_closure.services_for_lib_path`` is package-granular and must
not be reused as the verifier (it would re-encode the defect one layer down).

Callers: ``episode_residue`` / ``rows_from_lib_consumers``.

Authorship-time check: ``check_consumers_declarations`` applies the same
``verify_consumer_import`` predicate to every module that assigns ``CONSUMERS``,
so a wrong slug fails when written rather than weeks later at nomination.
"""

from __future__ import annotations

import ast
import re
from collections import deque
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Literal

from implement_admission.service_lib_ownership import path_prefixes, service_ownership

ImportPathStatus = Literal["verified", "unverified", "contradicted"]
DerivedSource = Literal["consumers", "path_prefix", "ownership", "import_graph"]

_LIBS_DIR = "libs"
_ABS_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)",
    re.MULTILINE,
)
_REL_FROM_RE = re.compile(
    r"^\s*from\s+(\.+)([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)?\s+import\s+",
    re.MULTILINE,
)


def repo_root() -> Path:
    """Return the universal-llm-gateway checkout root used for import-graph scans."""
    return Path(__file__).resolve().parents[2]


def module_for_lib_path(path: str) -> str | None:
    """Return the importable module for a ``libs/`` Python path, else ``None``."""
    text = str(path or "").replace("\\", "/")
    if not text.startswith("libs/") or not text.endswith(".py"):
        return None
    rel = text[len("libs/") : -3]
    if not rel or rel.endswith("/"):
        return None
    parts = rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or any(not p for p in parts):
        return None
    return ".".join(parts)


def _lib_file_for_module(root: Path, module: str) -> Path | None:
    """Resolve a dotted module to a ``libs/`` file when it exists."""
    parts = module.split(".")
    if not parts:
        return None
    libs = root / _LIBS_DIR
    as_mod = libs.joinpath(*parts[:-1], f"{parts[-1]}.py") if len(parts) > 1 else libs / f"{parts[0]}.py"
    if as_mod.is_file():
        return as_mod
    as_init = libs.joinpath(*parts, "__init__.py")
    if as_init.is_file():
        return as_init
    if len(parts) == 1:
        pkg_init = libs / parts[0] / "__init__.py"
        if pkg_init.is_file():
            return pkg_init
    return None


def _resolve_relative(module: str, dots: str, tail: str | None) -> str | None:
    """Resolve ``from .x import`` / ``from ..y import`` against *module*'s package."""
    parts = module.split(".")
    # File module: parent package is parts[:-1]; package __init__: parts is the package.
    if module.endswith(".__init__"):
        pkg_parts = parts[:-1]
    else:
        # Prefer package dir when this module is a package init resolved as pkg name.
        pkg_parts = parts[:-1] if len(parts) > 1 else parts
    up = len(dots) - 1
    if up > len(pkg_parts):
        return None
    base = pkg_parts[: len(pkg_parts) - up]
    if tail:
        return ".".join([*base, *tail.split(".")]) if base else tail
    return ".".join(base) if base else None


def _imports_in_file(path: Path, *, module: str | None = None) -> frozenset[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return frozenset()
    found: set[str] = set(_ABS_IMPORT_RE.findall(text))
    if module:
        for match in _REL_FROM_RE.finditer(text):
            resolved = _resolve_relative(module, match.group(1), match.group(2))
            if resolved:
                found.add(resolved)
    return frozenset(found)


def _tree_files(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]


@lru_cache(maxsize=64)
def _service_dir(root_str: str, slug: str) -> str | None:
    own = service_ownership().get(slug)
    if own is None:
        for prefix, mapped in path_prefixes():
            if mapped == slug:
                return str(Path(root_str) / prefix.rstrip("/"))
        return None
    return str(Path(root_str) / own.path_prefix.rstrip("/"))


@lru_cache(maxsize=128)
def _reachable_modules(slug: str, root_str: str) -> frozenset[str] | None:
    """Modules reachable from *slug*'s service tree via libs imports (file-granular)."""
    service_dir = _service_dir(root_str, slug)
    if service_dir is None:
        return None
    base = Path(service_dir)
    if not base.is_dir():
        return None
    root = Path(root_str)
    seeds: set[str] = set()
    for path in _tree_files(base):
        seeds.update(_imports_in_file(path))

    seen: set[str] = set()
    reach: set[str] = set()
    queue: deque[str] = deque(seeds)
    while queue:
        module = queue.popleft()
        if module in seen:
            continue
        seen.add(module)
        lib_file = _lib_file_for_module(root, module)
        if lib_file is None:
            # Truncate to a resolvable prefix (from x.y.z when only x.y exists).
            parts = module.split(".")
            while len(parts) > 1 and lib_file is None:
                parts.pop()
                lib_file = _lib_file_for_module(root, ".".join(parts))
            if lib_file is None:
                continue
            module = ".".join(parts)
            if module in reach:
                continue
        reach.add(module)
        for nxt in _imports_in_file(lib_file, module=module):
            if nxt not in seen:
                queue.append(nxt)
    return frozenset(reach)


def _module_reached(target: str, reach: frozenset[str]) -> bool:
    """True when *target* is imported, or (for packages) any submodule import loads it."""
    if target in reach:
        return True
    # Package __init__ loads whenever a submodule is imported.
    prefix = target + "."
    return any(mod == target or mod.startswith(prefix) for mod in reach)


def verify_consumer_import(
    slug: str,
    lib_path: str,
    *,
    root: Path | None = None,
) -> ImportPathStatus:
    """Return import_path status for whether *slug* reaches landed *lib_path*."""
    module = module_for_lib_path(lib_path)
    if module is None:
        return "unverified"
    base = root if root is not None else repo_root()
    reach = _reachable_modules(slug, str(base))
    if reach is None:
        return "unverified"
    return "verified" if _module_reached(module, reach) else "contradicted"


def format_verification_tags(
    *,
    derived: DerivedSource,
    import_path: ImportPathStatus,
) -> str:
    """Machine-readable tag suffix consumed by seats reading RESIDUE / row reason."""
    return f"derived:{derived}; import_path:{import_path}"


_VERIFICATION_TAGS_RE = re.compile(
    r"derived:(?P<derived>consumers|path_prefix|ownership|import_graph);\s*"
    r"import_path:(?P<import_path>verified|unverified|contradicted)"
)


def parse_verification_tags(text: str | None) -> dict[str, str] | None:
    """Extract ``derived`` / ``import_path`` tags from residue or row ``reason``.

    Returns ``None`` when absent — absence means seat-authored (or an untagged
    legacy coerce), not "unknown derivation." Does not invent tags.
    """
    match = _VERIFICATION_TAGS_RE.search(str(text or ""))
    if match is None:
        return None
    return {
        "derived": match.group("derived"),
        "import_path": match.group("import_path"),
    }


def verification_tags_fragment(text: str | None) -> str | None:
    """Return the exact ``derived:…; import_path:…`` fragment when present.

    Used by prose→row coerce to preserve tags without inventing a second dialect.
    """
    match = _VERIFICATION_TAGS_RE.search(str(text or ""))
    return match.group(0) if match else None


def residue_actions_for_lib_consumers(
    path: str,
    consumers: tuple[str, ...],
    *,
    root: Path | None = None,
) -> tuple[str, ...]:
    """Build RESIDUE action lines for CONSUMERS after module-path verify.

    Verified slugs become ``sync_restart`` lines; when none verify, emit a tagged
    ``libs_touched`` line (unverified or contradicted) so uncertainty stays visible.
    """
    verified: list[str] = []
    saw_unverified = False
    saw_contradicted = False
    for slug in consumers:
        status = verify_consumer_import(slug, path, root=root)
        tags = format_verification_tags(derived="consumers", import_path=status)
        if status == "verified":
            line = (
                f'sync_restart: {slug} — manage(action="sync_restart", '
                f'service="{slug}"); {tags}'
            )
            verified.append(line)
        elif status == "unverified":
            saw_unverified = True
        else:
            saw_contradicted = True
    if verified:
        return tuple(verified)
    status: ImportPathStatus = (
        "unverified" if saw_unverified else "contradicted" if saw_contradicted else "unverified"
    )
    tags = format_verification_tags(derived="consumers", import_path=status)
    return (
        f"libs_touched: {path} — shared lib; lead must decide which consumers "
        f"restart; {tags}",
    )


def clear_verify_caches() -> None:
    """Drop scan caches (tests that mutate trees / monkeypatch roots)."""
    _reachable_modules.cache_clear()
    _service_dir.cache_clear()


def _literal_str_tuple(node: ast.AST | None) -> tuple[str, ...] | None:
    """Return string elements from a tuple/list literal, else ``None``."""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    values: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            values.append(elt.value)
        else:
            return None
    return tuple(values)


def consumers_declared_in_source(text: str) -> tuple[str, ...] | None:
    """Parse a module-level ``CONSUMERS = (…)`` / annotated assign from *text*.

    Returns ``None`` when the module does not declare ``CONSUMERS``. Only the
    declaring file's own assignment is considered — walk-up inheritance is an
    application concern at mint time, not an authorship declaration.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "CONSUMERS":
                return _literal_str_tuple(node.value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CONSUMERS":
                    return _literal_str_tuple(node.value)
    return None


def iter_consumers_declarations(
    root: Path | None = None,
) -> Iterator[tuple[str, tuple[str, ...]]]:
    """Yield ``(libs/…path, consumers)`` for each libs module that declares CONSUMERS."""
    base = root if root is not None else repo_root()
    libs = base / _LIBS_DIR
    if not libs.is_dir():
        return
    for path in sorted(libs.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(base).as_posix()
        if is_lib_test_module_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        consumers = consumers_declared_in_source(text)
        if consumers is None:
            continue
        yield rel, consumers


def is_lib_test_module_path(path: str) -> bool:
    """True for ``libs/**/test_*.py`` (same skip class as propagation mint)."""
    text = str(path or "").replace("\\", "/")
    if not text.startswith("libs/") or not text.endswith(".py"):
        return False
    return text.rsplit("/", 1)[-1].startswith("test_")


def check_consumers_declarations(
    *,
    root: Path | None = None,
) -> list[str]:
    """Return failure lines for CONSUMERS slugs that do not reach their declaring module.

    Applies the same ``verify_consumer_import`` predicate used at nomination.
    Empty list means every declared slug is ``import_path:verified`` for its
    declaring file (package ``__init__`` grain included — that is intentional).
    """
    base = root if root is not None else repo_root()
    failures: list[str] = []
    for path, consumers in iter_consumers_declarations(base):
        for slug in consumers:
            status = verify_consumer_import(slug, path, root=base)
            if status != "verified":
                failures.append(
                    f"{path}: CONSUMERS slug {slug!r} is import_path:{status} "
                    f"(declaring module must be reached)"
                )
    return failures


__all__ = [
    "DerivedSource",
    "ImportPathStatus",
    "check_consumers_declarations",
    "clear_verify_caches",
    "consumers_declared_in_source",
    "format_verification_tags",
    "is_lib_test_module_path",
    "iter_consumers_declarations",
    "module_for_lib_path",
    "parse_verification_tags",
    "repo_root",
    "residue_actions_for_lib_consumers",
    "verification_tags_fragment",
    "verify_consumer_import",
]
