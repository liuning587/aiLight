"""Resolve project root and bundled resource paths (dev + PyInstaller exe)."""

from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> str:
    """Writable root: config.json, devices.json live here."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def bundle_root() -> str:
    """Read-only bundled assets (html, docs) when frozen."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", project_root())
    return project_root()


def resource_path(*parts: str) -> str:
    return os.path.join(bundle_root(), *parts)
