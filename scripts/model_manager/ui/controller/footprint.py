"""Deployment footprint — managed files and config resolution."""

import os
from dataclasses import dataclass
from pathlib import Path

_GATEWAY_DIR = Path.home() / ".gateway"
_NODES_DIR = _GATEWAY_DIR / "nodes"
_VENV_DIR = Path.home() / ".venvs" / "universal"
_SOCKET_DIR = Path("/tmp/universal-protocol")
_LOG_DIR = Path("/tmp/logs/universal-stargate")
_VLLM_CACHE = Path.home() / ".cache" / "vllm"


@dataclass(slots=True, kw_only=True)
class ManagedFile:
    """A file or directory created/managed by ./manage."""

    path: Path
    purpose: str
    category: str
    exists: bool


@dataclass(slots=True, kw_only=True)
class EnvLayer:
    """One layer in the environment variable resolution chain."""

    name: str
    path: Path | None
    entries: dict[str, str]


@dataclass(slots=True, kw_only=True)
class ConfigResolution:
    """Full config resolution state: env layers + service configs."""

    env_layers: list[EnvLayer]
    stargate_config: Path
    edge_template: Path
    compose_file: Path
    engine_env: Path
    project_name: str


class FootprintInspector:
    """Scans managed files and builds the config resolution chain."""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root

    def managed_files(self) -> list[ManagedFile]:
        """Return all files/dirs that ./manage creates."""
        files: list[ManagedFile] = []
        a = files.append

        a(self._mf(_GATEWAY_DIR, "Per-install config directory", "config"))
        a(
            self._mf(
                _GATEWAY_DIR / "stargate.yaml",
                "Master Stargate config (TUI-generated, editable)",
                "config",
            )
        )
        a(
            self._mf(
                _GATEWAY_DIR / "profiles.yaml",
                "Stargate profiles (empty seed, editable)",
                "config",
            )
        )
        a(
            self._mf(
                _GATEWAY_DIR / "model_transformations.yaml",
                "Stargate model transformations (empty seed)",
                "config",
            )
        )
        a(
            self._mf(
                _GATEWAY_DIR / "stargate.pid",
                "Stargate PID (present while running)",
                "runtime",
            )
        )
        a(self._mf(_NODES_DIR, "Per-node environment directory", "config"))
        self._add_node_files(files)
        a(
            self._mf(
                _GATEWAY_DIR / "catalog",
                "Local model catalog (measured models)",
                "config",
            )
        )
        a(
            self._mf(
                self._root / ".env.local",
                "Environment overrides (MODEL_PATH_ROOT, HF_TOKEN)",
                "workspace",
            )
        )
        a(self._mf(_SOCKET_DIR, "Unix socket dir (edge communication)", "runtime"))
        a(self._mf(_LOG_DIR, "Stargate log directory", "runtime"))

        gpu_nodes = self._root / "tmp" / "gpu-nodes"
        if gpu_nodes.exists():
            a(self._mf(gpu_nodes, "Worker logs and output", "runtime"))

        a(self._mf(_VLLM_CACHE, "vLLM model cache", "cache"))
        a(self._mf(_VENV_DIR, "Python virtual environment", "cache"))
        return files

    def _add_node_files(self, files: list[ManagedFile]) -> None:
        """Add per-node env files, or a placeholder if none exist yet."""
        if _NODES_DIR.exists():
            for env_file in sorted(_NODES_DIR.glob("*.env")):
                files.append(
                    self._mf(env_file, f"Node '{env_file.stem}' environment", "config")
                )
        else:
            files.append(
                self._mf(
                    _NODES_DIR / "localhost.env",
                    "Local node env (created on first start)",
                    "config",
                )
            )

    def config_resolution(self) -> ConfigResolution:
        """Build the layered config resolution chain."""
        env_local_path = self._root / ".env.local"
        env_local = _load_env_file(env_local_path)

        node_env_path = _NODES_DIR / "localhost.env"
        node_env = _load_env_file(node_env_path)

        all_keys = set(env_local) | set(node_env)
        os_entries = {k: os.environ[k] for k in all_keys if k in os.environ}

        return ConfigResolution(
            env_layers=[
                EnvLayer(name="os.environ", path=None, entries=os_entries),
                EnvLayer(name=".env.local", path=env_local_path, entries=env_local),
                EnvLayer(
                    name="~/.gateway/nodes/localhost.env",
                    path=node_env_path,
                    entries=node_env,
                ),
            ],
            stargate_config=_GATEWAY_DIR / "stargate.yaml",
            edge_template=self._root / "config" / "templates" / "edge-stargate.yaml",
            compose_file=self._root / "docker" / "compose" / "gpu-edge.yml",
            engine_env=self._root / "docker" / "compose" / "engine-optimizations.env",
            project_name="edge-localhost",
        )

    def clean_slate_commands(self) -> str:
        """Generate shell commands to reset all managed state."""
        lines = [
            "# Stop services first",
            'pkill -f "universal-" 2>/dev/null',
            "docker stop edge-localhost-edge-1 2>/dev/null",
            "docker rm edge-localhost-edge-1 2>/dev/null",
            "",
            "# Remove managed state (preserves models and venv)",
            f"rm -rf {_GATEWAY_DIR}",
            f"rm -rf {_SOCKET_DIR}",
            f"rm -rf {_LOG_DIR}",
            f"rm -f {self._root / '.env.local'}",
        ]
        gpu_nodes = self._root / "tmp" / "gpu-nodes"
        if gpu_nodes.exists():
            lines.append(f"rm -rf {gpu_nodes}")
        lines.extend(
            [
                "",
                "# Optional: remove venv (rebuilt on next ./manage)",
                f"# rm -rf {_VENV_DIR}",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _mf(path: Path, purpose: str, category: str) -> ManagedFile:
        return ManagedFile(
            path=path, purpose=purpose, category=category, exists=path.exists()
        )


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from an env file."""
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            entries[key.strip()] = value.strip()
    return entries
