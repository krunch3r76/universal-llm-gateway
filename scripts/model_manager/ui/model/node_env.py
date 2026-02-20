"""Node environment (~/.gateway/nodes/<node_id>.env) management."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_NODES_DIR = Path.home() / ".gateway" / "nodes"
DEFAULT_MODEL_PATH = Path.home() / ".models"


class NodeEnv:
    """
    Read/write ~/.gateway/nodes/<node_id>.env for per-node configuration.

    Manages MODEL_PATH only. NODE_ID and FEDERATION_KEY_EDGE are written
    once by ServiceController._ensure_node_env() and are never modified here.
    """

    def __init__(self, node_id: str = "localhost") -> None:
        self._path = _NODES_DIR / f"{node_id}.env"
        self._entries: dict[str, str] = {}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def model_path(self) -> Path:
        raw = self._entries.get("MODEL_PATH")
        if raw:
            return Path(raw).expanduser()
        return DEFAULT_MODEL_PATH

    @model_path.setter
    def model_path(self, value: Path) -> None:
        self._entries["MODEL_PATH"] = str(value)

    def save(self) -> None:
        """Update MODEL_PATH in the node env file, preserving all other lines.

        No-op (with warning) when the file does not exist yet — the file is
        created by ServiceController._ensure_node_env() on first Gateway start.
        """
        if not self._path.exists():
            logger.warning("Node env %s not found; MODEL_PATH not saved", self._path)
            return

        new_value = self._entries.get("MODEL_PATH")
        if new_value is None:
            return

        lines: list[str] = []
        written = False
        for line in self._path.read_text().splitlines():
            stripped = line.strip()
            if (
                stripped
                and not stripped.startswith("#")
                and "=" in stripped
                and stripped.split("=", 1)[0].strip() == "MODEL_PATH"
            ):
                lines.append(f"MODEL_PATH={new_value}")
                written = True
            else:
                lines.append(line)

        if not written:
            lines.append(f"MODEL_PATH={new_value}")

        self._path.write_text("\n".join(lines) + "\n")
        logger.info("Saved MODEL_PATH to %s", self._path)

    def _load(self) -> None:
        if not self._path.exists():
            return
        for line in self._path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            self._entries[key.strip()] = value.strip()
