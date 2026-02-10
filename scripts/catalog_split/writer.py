"""YAML file writer for catalog split with atomic writes."""

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


class IndentDumper(yaml.SafeDumper):
    """Custom YAML dumper with proper indentation."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    """Represent multiline strings with literal block style."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


IndentDumper.add_representer(str, str_representer)


def write_model_file(output_path: Path, model_entry: dict[str, Any]) -> None:
    """
    Write model entry to YAML file atomically.

    Atomicity: Writes to temp file in same directory, then os.replace().
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        suffix=".yaml.tmp",
        dir=output_path.parent,
        prefix=f".{output_path.stem}.",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(
                model_entry,
                f,
                Dumper=IndentDumper,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
                width=120,
            )
        os.replace(temp_path, output_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
