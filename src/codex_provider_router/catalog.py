from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import threading
from typing import Any

from .config import RouterConfig


class CatalogStore:
    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._stamp: tuple[tuple[str, int, int], ...] | None = None
        self._catalog: dict[str, Any] | None = None

    def get(self) -> dict[str, Any]:
        stamp = self._source_stamp()
        with self._lock:
            if stamp == self._stamp and self._catalog is not None:
                return copy.deepcopy(self._catalog)

            sources = [self._bundled_catalog()]
            for path in self.config.catalog_paths:
                sources.append(_read_catalog(path))
            template = self._template(sources)
            entries = []
            for priority, route in enumerate(self.config.public_routes, start=1):
                entry = self._find_model(sources, route.upstream_model)
                if entry is None:
                    entry = copy.deepcopy(template)
                    entry["display_name"] = route.display_name
                    entry["description"] = (
                        f"Route requests through {route.provider} using "
                        f"{route.upstream_model}."
                    )
                    entry["default_reasoning_level"] = "medium"
                    entry["supported_reasoning_levels"] = [
                        {"effort": "low", "description": "Fast responses"},
                        {"effort": "medium", "description": "Balanced reasoning"},
                        {"effort": "high", "description": "Deeper reasoning"},
                        {"effort": "xhigh", "description": "Extended reasoning"},
                    ]
                entry.update(
                    slug=route.alias,
                    display_name=route.display_name,
                    priority=priority,
                    visibility="list",
                    upgrade=None,
                )
                entry.pop("service_tiers", None)
                entry.pop("additional_speed_tiers", None)
                entries.append(entry)

            self._catalog = {"models": entries}
            self._stamp = stamp
            return copy.deepcopy(self._catalog)

    def _source_stamp(self) -> tuple[tuple[str, int, int], ...]:
        paths = (Path(self.config.codex_bin), *self.config.catalog_paths)
        values = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError as error:
                raise ValueError(f"catalog source is unavailable: {path}") from error
            values.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(values)

    def _bundled_catalog(self) -> dict[str, Any]:
        result = subprocess.run(
            [self.config.codex_bin, "debug", "models", "--bundled"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode:
            raise ValueError("cannot read the Codex bundled model catalog")
        try:
            catalog = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ValueError("Codex returned an invalid bundled model catalog") from error
        if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list):
            raise ValueError("Codex bundled model catalog has an invalid shape")
        return catalog

    @staticmethod
    def _template(sources: list[dict[str, Any]]) -> dict[str, Any]:
        for source in sources:
            models = source.get("models")
            if isinstance(models, list):
                for model in models:
                    if isinstance(model, dict) and model.get("slug"):
                        return copy.deepcopy(model)
        raise ValueError("no model metadata is available for catalog fallback")

    @staticmethod
    def _find_model(
        sources: list[dict[str, Any]], upstream_model: str
    ) -> dict[str, Any] | None:
        for source in sources:
            models = source.get("models")
            if not isinstance(models, list):
                continue
            for model in models:
                if isinstance(model, dict) and model.get("slug") == upstream_model:
                    return copy.deepcopy(model)
        return None


def _read_catalog(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read model catalog {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid model catalog JSON at {path}: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError(f"model catalog has an invalid shape: {path}")
    return payload
