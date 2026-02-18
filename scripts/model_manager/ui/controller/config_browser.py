"""Config browser - enumerates all user-relevant config files."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, kw_only=True)
class ConfigFile:
    path: Path
    category: str
    description: str
    exists: bool


class ConfigBrowser:
    """Enumerates configuration files grouped by category."""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root

    def list_all(self) -> list[ConfigFile]:
        files: list[ConfigFile] = []
        files.extend(self._environment_files())
        files.extend(self._stargate_configs())
        files.extend(self._gateway_configs())
        files.extend(self._catalog_dirs())
        files.extend(self._pipeline_configs())
        files.extend(self._profile_configs())
        files.extend(self._docker_configs())
        return files

    def list_by_category(self, category: str) -> list[ConfigFile]:
        return [f for f in self.list_all() if f.category == category]

    def get_categories(self) -> list[str]:
        return sorted({f.category for f in self.list_all()})

    def _environment_files(self) -> list[ConfigFile]:
        items: list[ConfigFile] = []
        items.append(
            self._cf(
                self._root / ".env.local",
                "Environment",
                "Local overrides (MODEL_PATH_ROOT, HF_TOKEN, etc.)",
            )
        )
        items.append(
            self._cf(
                self._root / ".env.example",
                "Environment",
                "Template for environment variables",
            )
        )
        items.append(
            self._cf(
                self._root / "docker" / "compose" / "engine-optimizations.env",
                "Environment",
                "Shared inference engine optimization variables",
            )
        )
        return items

    def _stargate_configs(self) -> list[ConfigFile]:
        config_dir = self._root / "config"
        items: list[ConfigFile] = []
        if config_dir.exists():
            for path in sorted(config_dir.glob("stargate_config.*.yaml")):
                deployment = path.stem.replace("stargate_config.", "")
                items.append(
                    self._cf(
                        path,
                        "Stargate",
                        f"Stargate config: {deployment}",
                    )
                )
        return items

    def _gateway_configs(self) -> list[ConfigFile]:
        gw = self._root / "services" / "_universal-llm-gateway" / "config"
        return [
            self._cf(gw / "gateway_config.yaml", "Gateway", "Core gateway settings"),
            self._cf(gw / "logging.yaml", "Gateway", "Gateway logging configuration"),
        ]

    def _catalog_dirs(self) -> list[ConfigFile]:
        return [
            self._cf(
                self._root / "config" / "models",
                "Catalog",
                "Static model catalog (metadata-only, version-controlled)",
            ),
            self._cf(
                Path.home() / ".gateway" / "catalog",
                "Catalog",
                "Local model catalog (full entries with profiles, per-install)",
            ),
        ]

    def _pipeline_configs(self) -> list[ConfigFile]:
        return [
            self._cf(
                self._root / "pipelines" / "consensus" / "models.yaml",
                "Pipelines",
                "Consensus pipeline model panel",
            ),
            self._cf(
                self._root
                / "pipelines"
                / "consensus"
                / "v5.0"
                / "chain-v5.0-synergize.yaml",
                "Pipelines",
                "Consensus v5.0 pipeline chain",
            ),
            self._cf(
                self._root / "pipelines" / "consensus" / "v5.0" / "prompts.yaml",
                "Pipelines",
                "Consensus v5.0 prompt definitions",
            ),
        ]

    def _profile_configs(self) -> list[ConfigFile]:
        sg = self._root / "services" / "universal-stargate" / "config"
        return [
            self._cf(sg / "profiles.yaml", "Profiles", "Generation parameter profiles"),
            self._cf(
                sg / "model_profiles.yaml", "Profiles", "Model-to-profile mappings"
            ),
            self._cf(
                sg / "audio_profiles.yaml", "Profiles", "Audio streaming profiles"
            ),
            self._cf(
                sg / "system_messages.yaml", "Profiles", "System message behavior"
            ),
        ]

    def _docker_configs(self) -> list[ConfigFile]:
        dc = self._root / "docker" / "compose"
        items: list[ConfigFile] = []
        if dc.exists():
            for path in sorted(dc.glob("*.yml")):
                items.append(self._cf(path, "Docker", f"Compose: {path.stem}"))
        return items

    def _cf(self, path: Path, category: str, description: str) -> ConfigFile:
        return ConfigFile(
            path=path,
            category=category,
            description=description,
            exists=path.exists(),
        )
