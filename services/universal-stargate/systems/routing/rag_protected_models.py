"""RAG workload models that must never be evicted or counted as reclaimable VRAM.

Protected set (per node): live embedding model id + any loaded copy of the
contextualize model routing key. IDs are read from pipeline models.yaml, not
hard-coded in eviction logic.
"""

from __future__ import annotations

import functools
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from model_id import ModelId

logger = logging.getLogger(__name__)

_ULG_REPO_DIRNAME = "universal-llm-gateway"
_RAG_MODELS_REL = Path("pipelines") / "rag" / "models.yaml"
_CONTEXTUALIZE_MODELS_REL = Path("pipelines") / "rag_contextualize" / "models.yaml"
_yaml_missing_logged = False


def _repo_root() -> Path:
    here = Path(__file__).resolve().parents[4]
    candidates: list[Path] = [here]
    env_root = os.environ.get("PROJECT_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser())
    seen: set[Path] = set()
    for root in candidates:
        if root in seen:
            continue
        seen.add(root)
        if (root / _RAG_MODELS_REL).is_file():
            return root
        nested = root / _ULG_REPO_DIRNAME
        if (nested / _RAG_MODELS_REL).is_file():
            return nested
    return here


def _read_models_yaml(path: Path) -> dict[str, Any]:
    global _yaml_missing_logged
    if not path.is_file():
        if not _yaml_missing_logged:
            logger.error("RAG protected models yaml missing: %s", path)
            _yaml_missing_logged = True
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models = raw.get("models")
    return models if isinstance(models, dict) else {}


def _rag_models_path() -> Path:
    return _repo_root() / _RAG_MODELS_REL


def _contextualize_models_path() -> Path:
    return _repo_root() / _CONTEXTUALIZE_MODELS_REL


@functools.lru_cache(maxsize=1)
def rag_embedding_model_id() -> str | None:
    """Configured embedding model id (pipelines/rag/models.yaml embedding alias)."""
    models = _read_models_yaml(_rag_models_path())
    emb = models.get("embedding")
    if isinstance(emb, dict):
        mid = emb.get("model")
        if isinstance(mid, str) and mid.strip():
            return mid.strip()
    return None


@functools.lru_cache(maxsize=1)
def rag_contextualize_model_id() -> str | None:
    models = _read_models_yaml(_contextualize_models_path())
    ctx = models.get("contextualize")
    if isinstance(ctx, dict):
        mid = ctx.get("model")
        if isinstance(mid, str) and mid.strip():
            return mid.strip()
    return None


@functools.lru_cache(maxsize=1)
def rag_contextualize_routing_key() -> str | None:
    mid = rag_contextualize_model_id()
    if not mid:
        return None
    try:
        return ModelId.parse(mid).routing_key
    except Exception:
        return mid


def is_rag_protected_model(model_id: ModelId) -> bool:
    """True when this loaded model must not be evicted or counted reclaimable."""
    emb = rag_embedding_model_id()
    if emb:
        try:
            if model_id == ModelId.parse(emb) or model_id.routing_key == ModelId.parse(
                emb
            ).routing_key:
                return True
        except Exception:
            if str(model_id) == emb:
                return True

    ctx_key = rag_contextualize_routing_key()
    if ctx_key and model_id.routing_key == ctx_key:
        return True
    return False


def clear_rag_protected_cache() -> None:
    """Test hook: drop cached yaml reads."""
    rag_embedding_model_id.cache_clear()
    rag_contextualize_model_id.cache_clear()
    rag_contextualize_routing_key.cache_clear()
