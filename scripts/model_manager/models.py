"""Data models for model-manager CLI."""

from dataclasses import dataclass
from typing import Any


@dataclass
class VerifiedModel:
    """Model entry from verified_models.json."""

    model_id: str
    local_path: str
    format: str
    repo: str
    file: str | None
    size_bytes: int
    sha256: str | None
    verified_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerifiedModel":
        download = data.get("download", {})
        hf = download.get("huggingface", {})
        return cls(
            model_id=data["model_id"],
            local_path=data["local_path"],
            format=data["format"],
            repo=hf.get("repo", ""),
            file=hf.get("file"),
            size_bytes=download.get("size_bytes", 0),
            sha256=download.get("sha256"),
            verified_at=data.get("verified_at", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "local_path": self.local_path,
            "format": self.format,
            "download": {
                "huggingface": {
                    "repo": self.repo,
                    "file": self.file,
                },
                "size_bytes": self.size_bytes,
                "sha256": self.sha256,
            },
            "verified_at": self.verified_at,
        }


@dataclass
class CatalogModel:
    """Model entry from catalog."""

    model_id: str
    metadata: dict[str, Any]
    download: dict[str, Any]
    configurations: dict[str, Any]

    @classmethod
    def from_dict(cls, model_id: str, data: dict[str, Any]) -> "CatalogModel":
        return cls(
            model_id=model_id,
            metadata=data.get("metadata", {}),
            download=data.get("download", {}),
            configurations=data.get("configurations", {}),
        )
