"""Offline egress inventory: git-tracked sources plus stargate StaticFiles roots."""

from __future__ import annotations

import ast
import re
import stat
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

_STARGATE_REL = Path("services/universal-stargate")
_PROXY_APP = _STARGATE_REL / "systems/proxy/app.py"
_ROUTERS_DIR = _STARGATE_REL / "systems/proxy/routers"

_SCAN_GIT_PATHS = ("libs", "services", "scripts", "pipelines")
_EXTENSIONS = frozenset({".py", ".js", ".jsx", ".ts"})

# Built without contiguous domain / key literals so this module does not self-match.
_API_DOMAIN_RE = re.compile(
    "".join(
        (
            r"api",
            r"\.",
            "anthrop",
            "ic",
            r"\.",
            "com",
        )
    )
)
_RESOLVE_ANTHROPIC_RE = re.compile(
    "".join(
        (
            r"resolve_",
            r"llm_",
            r"adapter\s*\(\s*",
            r"['\"]",
            "anthrop",
            "ic",
            r"['\"]",
            r"\s*\)",
        )
    )
)
_KEY_HEADER_RE = re.compile(
    "(?i)"
    + "x"
    + "-"
    + "api"
    + "-"
    + "key"
    + "|"
    + "anthrop"
    + "ic-dangerous-direct-browser-access"
)
_SK_PREFIX = "sk" + "-" + "ant" + "-"
_SK_ANT_KEY_RE = re.compile(re.escape(_SK_PREFIX) + r"[A-Za-z0-9_-]{16,}")

_RESOLVE_HIT_LABEL = (
    "resolve_" + "llm_" + "adapter(" + "'" + "anthrop" + "ic" + "'" + ")"
)
_RESOLVE_SECTION_TITLE = "Ungated " + _RESOLVE_HIT_LABEL + ":"

_ALLOWLIST: dict[str, str] = {
    "services/universal_cloud_proxy/config.py": (
        "Cloud-proxy provider default base URL; requests go through gated AnthropicAdapter"
    ),
    "services/cortex-workbench/CortexWorkbench.jsx": (
        "Browser-direct workbench UI outside ULG billing (r1 § 2.6)"
    ),
    "scripts/dispatch-anti-drift/declarative_probe.py": (
        "A8: Models API GET /v1/models only, not token-billed"
    ),
    "scripts/model_manager/ui/controller/service_config.py": (
        "Comment-only YAML example, not runtime egress"
    ),
    "libs/claude_bundles/skills_api.py": (
        "Skills upload API, not token-billed Messages"
    ),
}

_LINE_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "libs/llm_adapters/core.py": (
        r'os\.getenv\("ANTHROPIC_BASE_URL", "https://api\.anthropic\.com"\)',
    ),
    "libs/llm_adapters/anthropic/adapter.py": (
        r'os\.getenv\("ANTHROPIC_BASE_URL", "https://api\.anthropic\.com"\)',
    ),
}

_SERVED_ROOT_ALLOWLIST: dict[str, str] = {
    "services/cortex-workbench-app/dist": (
        "Vite build of services/cortex-workbench/CortexWorkbench.jsx (allowlisted); "
        "keyless browser-direct call, 401 standalone, $0 (15851#1496, #1500)"
    ),
}


@dataclass(frozen=True)
class ServedRoot:
    rel_dir: str
    mount_path: str | None
    mount_site: str
    present: bool


@dataclass
class Report:
    ok: bool = True
    tracked_line_violations: list[str] = field(default_factory=list)
    tracked_whole_file_violations: list[str] = field(default_factory=list)
    tracked_resolve_violations: list[str] = field(default_factory=list)
    served_key_header_violations: list[str] = field(default_factory=list)
    served_unlisted_violations: list[str] = field(default_factory=list)
    key_literal_violations: list[str] = field(default_factory=list)
    derivation_errors: list[str] = field(default_factory=list)
    stale_allowlist_keys: list[str] = field(default_factory=list)
    keyless_receipts: list[str] = field(default_factory=list)
    matched_allowlisted_tracked: list[str] = field(default_factory=list)
    scan_errors: list[str] = field(default_factory=list)

    def merge_ok(self) -> None:
        self.ok = not (
            self.tracked_line_violations
            or self.tracked_whole_file_violations
            or self.tracked_resolve_violations
            or self.served_key_header_violations
            or self.served_unlisted_violations
            or self.key_literal_violations
            or self.derivation_errors
            or self.stale_allowlist_keys
            or self.scan_errors
        )


class _DerivationError(Exception):
    def __init__(self, site: str, message: str) -> None:
        self.site = site
        self.message = message
        super().__init__(f"{site}: {message}")


def _rel_posix(repo_root: Path, path: Path) -> str:
    return path.relative_to(repo_root).as_posix()


def _rel_posix_in_repo(repo_root: Path, path: Path, site: str) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise _DerivationError(site, "static root resolves outside repository") from exc


def _is_test_or_fixture_path(rel: str) -> bool:
    name = Path(rel).name
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    if rel.startswith("tests/"):
        return True
    return False


def _iter_module_level_nodes(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    for node in body:
        yield node
        if isinstance(node, (ast.If, ast.Try)):
            for sub in node.body:
                yield from _iter_module_level_nodes([sub])
            if isinstance(node, ast.If):
                for sub in node.orelse:
                    yield from _iter_module_level_nodes([sub])
        elif isinstance(node, ast.With):
            for sub in node.body:
                yield from _iter_module_level_nodes([sub])


def _router_import_names(app_tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in app_tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "routers"
            and node.level >= 1
        ):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _is_app_arg(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "app"


def _call_from_stmt(node: ast.stmt) -> ast.Call | None:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return node.value
    if isinstance(node, ast.Call):
        return node
    return None


def _collect_app_py_triggers(
    app_tree: ast.Module, router_names: set[str]
) -> list[tuple[str, str, int]]:
    """Return (router_module, function_name, line) for each module-level mount trigger."""
    out: list[tuple[str, str, int]] = []
    for node in _iter_module_level_nodes(app_tree.body):
        call = _call_from_stmt(node)
        if call is None:
            continue
        func = call.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            mod = func.value.id
            if mod in router_names and call.args and _is_app_arg(call.args[0]):
                out.append((mod, func.attr, call.lineno))
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "app"
            and func.attr == "mount"
        ):
            out.append(("__direct__", "mount", call.lineno))
    return out


def _parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_level_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    assigns: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigns[target.id] = node.value
    return assigns


def _eval_path_expr(
    expr: ast.expr,
    *,
    router_file: Path,
    repo_root: Path,
    assigns: dict[str, ast.expr],
    site: str,
) -> Path:
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        if expr.func.id == "str" and len(expr.args) == 1:
            return _eval_path_expr(
                expr.args[0],
                router_file=router_file,
                repo_root=repo_root,
                assigns=assigns,
                site=site,
            )
        if expr.func.id == "Path" and len(expr.args) == 1:
            arg = expr.args[0]
            if isinstance(arg, ast.Name) and arg.id == "__file__":
                return router_file.resolve()
            raise _DerivationError(site, "Path() argument outside grammar")
    if isinstance(expr, ast.Name):
        if expr.id not in assigns:
            raise _DerivationError(site, f"unresolved name {expr.id!r}")
        return _eval_path_expr(
            assigns[expr.id],
            router_file=router_file,
            repo_root=repo_root,
            assigns=assigns,
            site=site,
        )
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        base = _eval_path_expr(
            expr.func.value,
            router_file=router_file,
            repo_root=repo_root,
            assigns=assigns,
            site=site,
        )
        if expr.func.attr == "resolve":
            if expr.args or expr.keywords:
                raise _DerivationError(site, "resolve() with arguments")
            return base.resolve()
        raise _DerivationError(site, f"call .{expr.func.attr}() outside grammar")
    if isinstance(expr, ast.Attribute) and expr.attr == "parent":
        base = _eval_path_expr(
            expr.value,
            router_file=router_file,
            repo_root=repo_root,
            assigns=assigns,
            site=site,
        )
        return base.parent
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        left = _eval_path_expr(
            expr.left,
            router_file=router_file,
            repo_root=repo_root,
            assigns=assigns,
            site=site,
        )
        right = expr.right
        if isinstance(right, ast.Constant) and isinstance(right.value, str):
            return left / right.value
        raise _DerivationError(site, "/ right-hand side outside grammar")
    raise _DerivationError(site, "expression outside grammar")


def _staticfiles_directory(node: ast.Call, site: str) -> ast.expr | None:
    if not (
        isinstance(node.func, ast.Name)
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "StaticFiles")
    ):
        return None
    func = node.func
    is_static = (isinstance(func, ast.Name) and func.id == "StaticFiles") or (
        isinstance(func, ast.Attribute) and func.attr == "StaticFiles"
    )
    if not is_static:
        return None
    for kw in node.keywords:
        if kw.arg == "directory":
            if kw.value is None:
                return None
            return kw.value
    if node.args:
        return node.args[0]
    return None


def _find_function_def(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _collect_static_mounts_in_function(
    fn: ast.FunctionDef,
) -> list[tuple[ast.expr, ast.expr | None, int]]:
    """Return (mount_path_expr, directory_expr, line) for each app.mount StaticFiles."""
    mounts: list[tuple[ast.expr, ast.expr | None, int]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "app"
            and node.func.attr == "mount"
        ):
            continue
        path_expr = node.args[0] if node.args else None
        dir_expr = None
        for arg in node.args[1:]:
            if isinstance(arg, ast.Call):
                d = _staticfiles_directory(arg, "mount")
                if d is not None:
                    dir_expr = d
        for kw in node.keywords:
            if isinstance(kw.value, ast.Call):
                d = _staticfiles_directory(kw.value, "mount")
                if d is not None:
                    dir_expr = d
        if dir_expr is not None and path_expr is not None:
            mounts.append((path_expr, dir_expr, node.lineno))
    return mounts


def _mount_path_literal(path_expr: ast.expr) -> str | None:
    if isinstance(path_expr, ast.Constant) and isinstance(path_expr.value, str):
        return path_expr.value
    return None


def _collect_all_staticfiles_calls(
    repo_root: Path,
) -> list[tuple[Path, int, ast.Call]]:
    stargate = repo_root / _STARGATE_REL
    found: list[tuple[Path, int, ast.Call]] = []
    if not stargate.is_dir():
        return found
    for py_path in sorted(stargate.rglob("*.py")):
        if not py_path.is_file():
            continue
        try:
            rel = _rel_posix(repo_root, py_path)
            tracked = subprocess.run(
                ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", rel],
                capture_output=True,
                check=False,
            )
            if tracked.returncode != 0:
                continue
        except OSError:
            continue
        tree = _parse_module(py_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                d = _staticfiles_directory(node, f"{rel}:{node.lineno}")
                if d is not None:
                    found.append((py_path, node.lineno, node))
    return found


def derive_served_roots(repo_root: Path) -> list[ServedRoot]:
    app_path = repo_root / _PROXY_APP
    if not app_path.is_file():
        raise _DerivationError(str(_PROXY_APP), "app.py missing")

    app_tree = _parse_module(app_path)
    router_names = _router_import_names(app_tree)
    triggers = _collect_app_py_triggers(app_tree, router_names)

    roots_by_dir: dict[str, ServedRoot] = {}
    resolved_sites: set[tuple[str, int]] = set()

    for mod, fn_name, app_line in triggers:
        mount_site = f"services/universal-stargate/systems/proxy/app.py:{app_line}"
        if mod == "__direct__":
            for node in _iter_module_level_nodes(app_tree.body):
                call = _call_from_stmt(node)
                if call is None or call.lineno != app_line:
                    continue
                for arg in call.args[1:]:
                    if not isinstance(arg, ast.Call):
                        continue
                    dir_expr = _staticfiles_directory(
                        arg, f"{_PROXY_APP}:{call.lineno}"
                    )
                    if dir_expr is not None:
                        abs_dir = _eval_path_expr(
                            dir_expr,
                            router_file=app_path,
                            repo_root=repo_root,
                            assigns=_module_level_assignments(app_tree),
                            site=f"{_PROXY_APP}:{call.lineno}",
                        )
                        rel_dir = _rel_posix_in_repo(
                            repo_root,
                            abs_dir,
                            f"{_PROXY_APP}:{call.lineno}",
                        )
                        path_lit = (
                            _mount_path_literal(call.args[0]) if call.args else None
                        )
                        roots_by_dir[rel_dir] = ServedRoot(
                            rel_dir=rel_dir,
                            mount_path=path_lit,
                            mount_site=mount_site,
                            present=(repo_root / rel_dir).is_dir(),
                        )
                        resolved_sites.add((str(app_path), call.lineno))
                        resolved_sites.add((str(app_path), arg.lineno))
            continue

        router_path = repo_root / _ROUTERS_DIR / f"{mod}.py"
        if not router_path.is_file():
            raise _DerivationError(mount_site, f"router module missing: {mod}.py")
        router_tree = _parse_module(router_path)
        fn = _find_function_def(router_tree, fn_name)
        if fn is None:
            raise _DerivationError(mount_site, f"function {fn_name} not found")
        assigns = _module_level_assignments(router_tree)
        for path_expr, dir_expr, mount_line in _collect_static_mounts_in_function(fn):
            site = f"systems/proxy/routers/{mod}.py:{mount_line}"
            abs_dir = _eval_path_expr(
                dir_expr,
                router_file=router_path,
                repo_root=repo_root,
                assigns=assigns,
                site=site,
            )
            rel_dir = _rel_posix_in_repo(repo_root, abs_dir, site)
            path_lit = _mount_path_literal(path_expr)
            roots_by_dir[rel_dir] = ServedRoot(
                rel_dir=rel_dir,
                mount_path=path_lit,
                mount_site=mount_site,
                present=(repo_root / rel_dir).is_dir(),
            )
            resolved_sites.add((str(router_path), mount_line))
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and node.lineno == mount_line:
                    for arg in node.args[1:]:
                        if isinstance(arg, ast.Call):
                            d = _staticfiles_directory(arg, site)
                            if d is not None:
                                resolved_sites.add((str(router_path), arg.lineno))

    all_static = _collect_all_staticfiles_calls(repo_root)
    for py_path, line_no, _call in all_static:
        key = (str(py_path), line_no)
        if key not in resolved_sites:
            rel = _rel_posix(repo_root, py_path)
            raise _DerivationError(f"{rel}:{line_no}", "unresolved StaticFiles mount")

    if not roots_by_dir:
        zero_line = triggers[0][2] if triggers else 1
        zero_site = f"services/universal-stargate/systems/proxy/app.py:{zero_line}"
        raise _DerivationError(zero_site, "zero served roots derived")

    return sorted(roots_by_dir.values(), key=lambda r: r.rel_dir)


def _git_tracked_scan_files(repo_root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--", *_SCAN_GIT_PATHS],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise OSError(
            f"{repo_root}:git-ls-files: git ls-files failed (exit {proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace')}"
        )
    raw = proc.stdout.split(b"\0")
    rels: list[str] = []
    for entry in raw:
        if not entry:
            continue
        rel = entry.decode("utf-8")
        if Path(rel).suffix not in _EXTENSIONS:
            continue
        if _is_test_or_fixture_path(rel):
            continue
        if (repo_root / rel).is_file():
            rels.append(rel)
    return sorted(rels)


def _served_root_for_file(rel_file: str, roots: list[ServedRoot]) -> ServedRoot | None:
    for root in roots:
        prefix = root.rel_dir.rstrip("/") + "/"
        if rel_file == root.rel_dir or rel_file.startswith(prefix):
            return root
    return None


def _find_key_literal_lines(text: str, rel: str) -> list[str]:
    violations: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _SK_ANT_KEY_RE.search(line):
            violations.append(f"{rel}:{line_no}")
    return violations


def _path_under_served_root(path: Path, root_abs: Path) -> bool:
    try:
        path.resolve().relative_to(root_abs.resolve())
        return True
    except ValueError:
        return False


def _collect_served_files(
    repo_root: Path, root: ServedRoot, scan_errors: list[str]
) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    try:
        root_abs = (repo_root / root.rel_dir).resolve()
    except (OSError, RuntimeError):
        scan_errors.append(f"{root.rel_dir}: symlink loop in served root")
        return files
    if not root_abs.is_dir():
        return files
    active_dir_keys: set[tuple[int, int]] = set()
    completed_dir_keys: set[tuple[int, int]] = set()

    def _resolve_or_loop(path: Path, entry_rel: str) -> Path | None:
        try:
            return path.resolve()
        except (OSError, RuntimeError):
            scan_errors.append(f"{entry_rel}: symlink loop in served root")
            return None

    def walk(dir_path: Path) -> None:
        dir_resolved = _resolve_or_loop(dir_path, _rel_posix(repo_root, dir_path))
        if dir_resolved is None:
            return
        try:
            st = dir_resolved.stat()
            dir_key = (st.st_dev, st.st_ino)
        except OSError as exc:
            scan_errors.append(f"{root.rel_dir}: {exc}")
            return
        if dir_key in active_dir_keys:
            rel_loop = _rel_posix(repo_root, dir_path)
            scan_errors.append(f"{rel_loop}: symlink loop in served root")
            return
        if dir_key in completed_dir_keys:
            return
        active_dir_keys.add(dir_key)
        try:
            try:
                entries = sorted(dir_path.iterdir(), key=lambda p: p.name)
            except OSError as exc:
                scan_errors.append(f"{_rel_posix(repo_root, dir_path)}: {exc}")
                return
            for entry in entries:
                try:
                    entry_rel = _rel_posix(repo_root, entry)
                except ValueError:
                    scan_errors.append(f"{root.rel_dir}: path outside repository")
                    continue
                try:
                    if entry.is_symlink():
                        target = _resolve_or_loop(entry, entry_rel)
                        if target is None:
                            continue
                        if not _path_under_served_root(target, root_abs):
                            scan_errors.append(
                                f"{entry_rel}: symlink target outside served root"
                            )
                            continue
                        if target.is_dir():
                            walk(target)
                        elif target.is_file():
                            files.append((_rel_posix(repo_root, target), target))
                        continue
                    mode = entry.lstat().st_mode
                    if stat.S_ISDIR(mode):
                        resolved = _resolve_or_loop(entry, entry_rel)
                        if resolved is None:
                            continue
                        if not _path_under_served_root(resolved, root_abs):
                            scan_errors.append(
                                f"{entry_rel}: directory outside served root"
                            )
                            continue
                        walk(entry)
                    elif stat.S_ISREG(mode):
                        resolved = _resolve_or_loop(entry, entry_rel)
                        if resolved is None:
                            continue
                        if not _path_under_served_root(resolved, root_abs):
                            scan_errors.append(
                                f"{entry_rel}: file resolves outside served root"
                            )
                            continue
                        files.append((entry_rel, entry))
                except OSError as exc:
                    scan_errors.append(f"{entry_rel}: {exc}")
        finally:
            active_dir_keys.discard(dir_key)
            completed_dir_keys.add(dir_key)

    walk(root_abs)
    return files


def _record_served_key_header_violations(rel: str, text: str, report: Report) -> bool:
    if not (_API_DOMAIN_RE.search(text) and _KEY_HEADER_RE.search(text)):
        return False
    file_hits: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _API_DOMAIN_RE.search(line) and _KEY_HEADER_RE.search(line):
            file_hits.append(f"{rel}:{line_no}")
    if not file_hits:
        file_hits.append(f"{rel}: (key header)")
    report.served_key_header_violations.extend(file_hits)
    return True


def scan(repo_root: Path) -> Report:
    report = Report()
    repo_root = repo_root.resolve()

    try:
        served_roots = derive_served_roots(repo_root)
    except _DerivationError as exc:
        report.derivation_errors.append(f"{exc.site}: {exc.message}")
        report.merge_ok()
        return report

    derived_dirs = {r.rel_dir for r in served_roots}
    for key in _SERVED_ROOT_ALLOWLIST:
        if key not in derived_dirs:
            report.stale_allowlist_keys.append(key)

    try:
        tracked_rels = _git_tracked_scan_files(repo_root)
    except OSError as exc:
        report.derivation_errors.append(str(exc))
        report.merge_ok()
        return report

    tracked_set = set(tracked_rels)
    file_text: dict[str, str] = {}

    for rel in tracked_rels:
        path = repo_root / rel
        try:
            file_text[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

    for root in served_roots:
        if not root.present:
            continue
        for rel, file_path in _collect_served_files(
            repo_root, root, report.scan_errors
        ):
            if rel in file_text:
                continue
            try:
                file_text[rel] = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

    hits: dict[str, list[str]] = {}
    for rel, text in file_text.items():
        reasons: list[str] = []
        if _API_DOMAIN_RE.search(text):
            reasons.append("api")
        if _RESOLVE_ANTHROPIC_RE.search(text):
            reasons.append("resolve")
        if reasons:
            hits[rel] = reasons
    key_literal_set: set[str] = set()
    for rel, text in file_text.items():
        key_literal_set.update(_find_key_literal_lines(text, rel))
    report.key_literal_violations = sorted(key_literal_set)

    for rel, reasons in hits.items():
        if (
            "resolve" in reasons
            and rel not in _ALLOWLIST
            and rel not in _LINE_ALLOWLIST
        ):
            report.tracked_resolve_violations.append(f"{rel}: {_RESOLVE_HIT_LABEL}")

    for rel, reasons in hits.items():
        if "api" not in reasons:
            continue
        text = file_text[rel]
        served = _served_root_for_file(rel, served_roots)
        if served is not None and _record_served_key_header_violations(
            rel, text, report
        ):
            continue
        if served is not None and rel not in tracked_set:
            if served.rel_dir in _SERVED_ROOT_ALLOWLIST:
                report.keyless_receipts.append(
                    f"{served.rel_dir} {rel}: keyless; "
                    f"{_SERVED_ROOT_ALLOWLIST[served.rel_dir]}"
                )
            else:
                report.served_unlisted_violations.append(
                    f"{rel}: keyless api hit outside allowlist"
                )
            continue

        allowed = _LINE_ALLOWLIST.get(rel, ())
        if allowed:
            for line_no, line in enumerate(text.splitlines(), start=1):
                if _API_DOMAIN_RE.search(line) and not any(
                    re.search(pat, line) for pat in allowed
                ):
                    if _SK_ANT_KEY_RE.search(line):
                        report.tracked_line_violations.append(f"{rel}:{line_no}")
                    else:
                        report.tracked_line_violations.append(
                            f"{rel}:{line_no}: {line.strip()}"
                        )
        elif rel not in _ALLOWLIST:
            report.tracked_whole_file_violations.append(f"{rel}: (whole-file hit)")
        else:
            report.matched_allowlisted_tracked.append(rel)

    for rel in hits:
        if rel in _ALLOWLIST and rel not in report.matched_allowlisted_tracked:
            if _served_root_for_file(rel, served_roots) is None:
                report.matched_allowlisted_tracked.append(rel)

    report.merge_ok()
    return report


def render(report: Report) -> str:
    lines: list[str] = []
    if report.ok:
        lines.append("PASS")
    else:
        lines.append("FAIL")
    if report.derivation_errors:
        lines.append("Derivation errors:")
        lines.extend(f"  {e}" for e in report.derivation_errors)
    if report.stale_allowlist_keys:
        lines.append("Stale served-root allowlist keys:")
        lines.extend(f"  {k}" for k in report.stale_allowlist_keys)
    if report.tracked_line_violations:
        lines.append("Ungated api on non-allowlisted lines:")
        lines.extend(f"  {v}" for v in report.tracked_line_violations)
    if report.tracked_whole_file_violations:
        lines.append("Ungated whole-file api hits:")
        lines.extend(f"  {v}" for v in report.tracked_whole_file_violations)
    if report.tracked_resolve_violations:
        lines.append(_RESOLVE_SECTION_TITLE)
        lines.extend(f"  {v}" for v in report.tracked_resolve_violations)
    if report.scan_errors:
        lines.append("Served-root scan errors:")
        lines.extend(f"  {e}" for e in report.scan_errors)
    if report.served_key_header_violations:
        lines.append("Served-root key-header violations:")
        lines.extend(f"  {v}" for v in report.served_key_header_violations)
    if report.served_unlisted_violations:
        lines.append("Served-root unlisted keyless violations:")
        lines.extend(f"  {v}" for v in report.served_unlisted_violations)
    if report.key_literal_violations:
        lines.append("Anthropic key literal violations (file:line only):")
        lines.extend(f"  {v}" for v in report.key_literal_violations)
    if report.keyless_receipts:
        lines.append("Keyless served-root receipts:")
        lines.extend(f"  {r}" for r in report.keyless_receipts)
    if report.matched_allowlisted_tracked:
        lines.append("Allowlisted tracked hits:")
        lines.extend(
            f"  {rel}: {_ALLOWLIST[rel]}"
            for rel in sorted(set(report.matched_allowlisted_tracked))
        )
    return "\n".join(lines)
