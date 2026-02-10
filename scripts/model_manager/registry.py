"""Registry classes for model-manager CLI."""

from pathlib import Path
from typing import Any

from filelock import FileLock

from .models import CatalogModel, VerifiedModel
from .utils import load_json, load_yaml, save_json, save_yaml


class VerifiedRegistry:
    """Operations on verified_models.json."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, Any] | None = None

    @property
    def data(self) -> dict[str, Any]:
        if self._data is None:
            if self.path.exists():
                self._data = load_json(self.path)
            else:
                self._data = {"version": "1.0", "models": []}
        return self._data

    def save(self) -> None:
        lock_path = self.path.with_suffix(".json.lock")
        with FileLock(lock_path, timeout=30.0):
            save_json(self.path, self.data)

    def get(self, model_id: str) -> VerifiedModel | None:
        for m in self.data.get("models", []):
            if m.get("model_id") == model_id:
                return VerifiedModel.from_dict(m)
        return None

    def exists(self, model_id: str) -> bool:
        return self.get(model_id) is not None

    def add(self, model: VerifiedModel) -> None:
        if self.exists(model.model_id):
            models = self.data.get("models", [])
            for i, m in enumerate(models):
                if m.get("model_id") == model.model_id:
                    models[i] = model.to_dict()
                    break
        else:
            self.data.setdefault("models", []).append(model.to_dict())

    def list_all(self) -> list[VerifiedModel]:
        return [VerifiedModel.from_dict(m) for m in self.data.get("models", [])]


class Catalog:
    """Operations on model_catalog.yaml."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, Any] | None = None

    @property
    def data(self) -> dict[str, Any]:
        if self._data is None:
            if self.path.exists():
                self._data = load_yaml(self.path)
            else:
                self._data = {
                    "catalog_version": "1.0",
                    "catalog_type": "dynamic",
                    "transformations": {},
                    "models": {},
                }
        return self._data

    def save(self) -> None:
        save_yaml(self.path, self.data)

    def get(self, model_id: str) -> CatalogModel | None:
        models = self.data.get("models", {})
        if model_id in models:
            return CatalogModel.from_dict(model_id, models[model_id])
        return None

    def exists(self, model_id: str) -> bool:
        return model_id in self.data.get("models", {})

    def add(self, model_id: str, entry: dict[str, Any]) -> None:
        self.data.setdefault("models", {})[model_id] = entry

    def list_all(self) -> list[CatalogModel]:
        return [
            CatalogModel.from_dict(mid, mdata)
            for mid, mdata in self.data.get("models", {}).items()
        ]

    def get_transformation(self, name: str) -> dict[str, Any] | None:
        return self.data.get("transformations", {}).get(name)
