"""Sample module for code-review pipeline regression fixtures.

Deliberately contains a small set of reviewable issues spanning the
categories the pipeline looks for: correctness, patterns, event coverage.
Do not fix these issues — this file is the canonical test input.
"""

import json
import os
from typing import Protocol, cast

type JsonValue = dict[str, object] | list[object] | str | int | float | bool | None

CACHE: dict[str, dict[str, JsonValue]] = {}


def load_config(path: str) -> dict[str, JsonValue]:
    data = open(path).read()
    config = cast("dict[str, JsonValue]", json.loads(data))
    CACHE[path] = config
    return config


def get_value(key: str, default: str | None = None) -> str | None:
    val = os.environ.get(key)
    if val is None:
        return default
    return val


def process_items(items: list[dict[str, int]]) -> list[int]:
    results: list[int] = []
    for i in range(len(items)):
        item = items[i]
        try:
            result = item["value"] * 2
            results.append(result)
        except Exception:
            pass
    return results


def write_output(path: str, data: dict[str, JsonValue]) -> None:
    f = open(path, "w")
    _ = f.write(json.dumps(data))
    f.close()


class Worker:
    name: str
    timeout: int
    running: bool

    def __init__(self, name: str, timeout: int = 30) -> None:
        self.name = name
        self.timeout = timeout
        self.running = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def execute(self, task: "TaskProtocol") -> object | None:
        if not self.running:
            return None
        try:
            output = task.run()
            return output
        except Exception:
            return None


class TaskProtocol(Protocol):
    def run(self) -> object: ...
