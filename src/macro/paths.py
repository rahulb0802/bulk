from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """Repo root (parent of src/)."""
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return project_root() / "config"


def data_dir() -> Path:
    path = project_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path
