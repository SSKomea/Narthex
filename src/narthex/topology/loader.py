"""Topology loading utilities."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

from ..errors import TopologyError
from ..models import Topology
from . import yaml_lite


def loads_topology(text: str) -> Topology:
    """Parse a topology from YAML or JSON text."""
    try:
        stripped = text.lstrip("\ufeff").lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            data = json.loads(stripped)
        else:
            data = yaml_lite.loads(text)
    except Exception as exc:
        raise TopologyError(f"could not parse topology: {exc}") from exc
    if not isinstance(data, Mapping):
        raise TopologyError("topology document root must be a mapping")
    try:
        return Topology.from_dict(data)
    except Exception as exc:
        raise TopologyError(str(exc)) from exc


def load_topology(path: str | Path) -> Topology:
    """Load a topology from ``.yaml``, ``.yml``, ``.json``, or ``.py``."""
    path = Path(path)
    if not path.exists():
        raise TopologyError(f"topology file not found: {path}")
    if path.suffix.lower() == ".py":
        return load_python_topology(path)
    try:
        data = yaml_lite.load(path)
    except Exception as exc:
        raise TopologyError(f"could not load {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise TopologyError(f"{path}: document root must be a mapping")
    try:
        return Topology.from_dict(data)
    except Exception as exc:
        raise TopologyError(f"{path}: {exc}") from exc


def load_python_topology(path: str | Path) -> Topology:
    """Execute a Python DSL file and read its ``topology`` or ``build_topology``."""
    path = Path(path)
    module_name = f"narthex_user_topology_{abs(hash(path.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise TopologyError(f"could not import topology module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return topology_from_module(module, source=str(path))


def topology_from_module(module: ModuleType, source: str | None = None) -> Topology:
    builder = getattr(module, "build_topology", None)
    value: Any = None
    if callable(builder):
        value = builder()
    elif hasattr(module, "topology"):
        raw = getattr(module, "topology")
        value = raw() if callable(raw) else raw
    else:
        raise TopologyError(
            f"{source or module.__name__}: define a Topology in 'topology' "
            "or a callable 'build_topology'"
        )
    if isinstance(value, Mapping):
        try:
            return Topology.from_dict(value)
        except Exception as exc:
            raise TopologyError(f"{source or module.__name__}: {exc}") from exc
    if not isinstance(value, Topology):
        raise TopologyError(
            f"{source or module.__name__}: build_topology must return a Topology"
        )
    try:
        value.validate()
    except Exception as exc:
        raise TopologyError(f"{source or module.__name__}: {exc}") from exc
    return value
