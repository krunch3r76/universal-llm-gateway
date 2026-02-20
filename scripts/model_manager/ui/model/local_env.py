"""Local environment configuration (.env.local) management."""

import logging
from pathlib import Path

from .node_env import NodeEnv

logger = logging.getLogger(__name__)

# Keys managed by the TUI in .env.local (model path lives in ~/.gateway/nodes/localhost.env)
MANAGED_KEYS = frozenset(
    {
        "GATEWAY_LOCAL_CATALOG_DIR",
        "GATEWAY_USER_CONFIG_DIR",
        "HF_TOKEN",
    }
)


class LocalEnv:
    """
    Read/write .env.local in the workspace root.

    This file is gitignored (*.env.local pattern) and holds per-clone
    configuration like API keys. Model path is stored in
    ~/.gateway/nodes/localhost.env (single source of truth alongside
    NODE_ID and FEDERATION_KEY_EDGE).
    """

    def __init__(self, workspace_root: Path) -> None:
        self._path = workspace_root / ".env.local"
        self._workspace_root = workspace_root
        self._entries: dict[str, str] = {}
        self._node_env = NodeEnv()
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def node_env_path(self) -> Path:
        """Path to ~/.gateway/nodes/localhost.env — where model path is stored."""
        return self._node_env.path

    @property
    def model_path_root(self) -> Path:
        """Live read from ~/.gateway/nodes/localhost.env:MODEL_PATH."""
        return self._node_env.model_path

    @model_path_root.setter
    def model_path_root(self, value: Path) -> None:
        self._node_env.model_path = value

    @property
    def model_search_paths(self) -> list[Path]:
        """Ordered list of directories to search for model files.

        Single path today (MODEL_PATH). Future: MODEL_SEARCH_PATHS as
        colon-separated list, mirroring PATH semantics.
        """
        return [self.model_path_root]

    @property
    def local_catalog_dir(self) -> Path:
        raw = self._entries.get("GATEWAY_LOCAL_CATALOG_DIR")
        if raw:
            return Path(raw).expanduser()
        return Path.home() / ".gateway" / "catalog"

    @property
    def hf_token(self) -> str | None:
        return self._entries.get("HF_TOKEN") or None

    def get(self, key: str) -> str | None:
        return self._entries.get(key) or None

    def set(self, key: str, value: str) -> None:
        self._entries[key] = value

    def save(self) -> None:
        """Write entries back to .env.local, preserving unmanaged lines.

        Also persists MODEL_PATH to ~/.gateway/nodes/localhost.env via NodeEnv.
        """
        lines: list[str] = []
        written_keys: set[str] = set()

        if self._path.exists():
            for line in self._path.read_text().splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in self._entries:
                        lines.append(f"{key}={self._entries[key]}")
                        written_keys.add(key)
                        continue
                lines.append(line)

        for key, value in self._entries.items():
            if key not in written_keys:
                lines.append(f"{key}={value}")

        self._path.write_text("\n".join(lines) + "\n")
        logger.info("Saved %s", self._path)

        self._node_env.save()

    def _load(self) -> None:
        if not self._path.exists():
            return
        for line in self._path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            self._entries[key.strip()] = value.strip()

    def as_dict(self) -> dict[str, str]:
        return dict(self._entries)
