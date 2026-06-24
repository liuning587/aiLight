"""devices.json persistence and default-device sync."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone

from tools.light_client import load_devices_config


def _atomic_write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_devices_config(path: str, cfg: dict) -> None:
    _atomic_write_json(path, cfg)


def _slug_alias(name: str, address: str) -> str:
    if name:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if base.startswith("ailight"):
            base = base.replace("ailight", "ailight", 1)
        if base and base != "-":
            suffix = address.replace(":", "")[-4:].lower()
            return f"{base}-{suffix}" if suffix else base
    return f"ailight-{address.replace(':', '')[-4:].lower()}"


def _unique_alias(cfg: dict, preferred: str) -> str:
    devices = cfg.get("devices") if isinstance(cfg.get("devices"), dict) else {}
    if preferred not in devices:
        return preferred
    n = 2
    while f"{preferred}-{n}" in devices:
        n += 1
    return f"{preferred}-{n}"


def bind_device(
    devices_path: str,
    address: str,
    name: str,
    alias: str | None = None,
    note: str = "",
) -> tuple[str, dict]:
    address = address.strip().upper()
    name = (name or "").strip() or f"aiLight-{address.replace(':', '')[-4:]}"
    cfg = load_devices_config(devices_path)
    if not isinstance(cfg.get("devices"), dict):
        cfg["devices"] = {}
    devices: dict = cfg["devices"]

    for existing_alias, dev in devices.items():
        if isinstance(dev, dict) and (dev.get("address") or "").upper() == address:
            dev["name"] = name
            dev["name_prefix"] = "aiLight"
            if note:
                dev["note"] = note
            dev["bound_at"] = datetime.now(timezone.utc).isoformat()
            save_devices_config(devices_path, cfg)
            return existing_alias, cfg

    preferred = (alias or "").strip() or _slug_alias(name, address)
    preferred = re.sub(r"[^a-z0-9_-]", "-", preferred.lower()).strip("-") or "ailight"
    new_alias = _unique_alias(cfg, preferred)
    devices[new_alias] = {
        "name": name,
        "name_prefix": "aiLight",
        "address": address,
        "timeout": float(cfg.get("default_timeout", 8.0)),
        "bound_at": datetime.now(timezone.utc).isoformat(),
    }
    if note:
        devices[new_alias]["note"] = note
    save_devices_config(devices_path, cfg)
    return new_alias, cfg


def delete_device(devices_path: str, alias: str) -> dict:
    cfg = load_devices_config(devices_path)
    devices = cfg.get("devices") if isinstance(cfg.get("devices"), dict) else {}
    if alias not in devices:
        raise KeyError(f"device not found: {alias}")
    del devices[alias]
    if cfg.get("default_device") == alias:
        cfg["default_device"] = next(iter(devices), "")
    save_devices_config(devices_path, cfg)
    return cfg


def _sync_hook_default(project_root: str, alias: str) -> None:
    for rel in (".cursor/ailight.json", ".trae/ailight.json"):
        path = os.path.join(project_root, rel)
        if not os.path.exists(path):
            continue
        hook_cfg = _load_json_file(path)
        if isinstance(hook_cfg, dict):
            hook_cfg["default_device"] = alias
            _atomic_write_json(path, hook_cfg)


def _load_json_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def set_default_everywhere(
    alias: str,
    devices_path: str,
    app_config_path: str,
    project_root: str | None = None,
) -> None:
    cfg = load_devices_config(devices_path)
    devices = cfg.get("devices") if isinstance(cfg.get("devices"), dict) else {}
    if alias not in devices:
        raise KeyError(f"device not found: {alias}")
    cfg["default_device"] = alias
    save_devices_config(devices_path, cfg)

    if os.path.exists(app_config_path):
        app_cfg = _load_json_file(app_config_path)
        if isinstance(app_cfg, dict):
            app_cfg["default_device"] = alias
            _atomic_write_json(app_config_path, app_cfg)

    root = project_root or os.path.abspath(
        os.path.join(os.path.dirname(devices_path), ".")
    )
    _sync_hook_default(root, alias)


def list_devices_summary(
    devices_path: str,
    active_alias: str | None,
    ble_status: dict | None = None,
) -> dict:
    cfg = load_devices_config(devices_path)
    default_alias = (cfg.get("default_device") or "").strip()
    devices = cfg.get("devices") if isinstance(cfg.get("devices"), dict) else {}
    rows = []
    for alias, dev in devices.items():
        if not isinstance(dev, dict):
            continue
        rows.append(
            {
                "alias": alias,
                "name": dev.get("name", ""),
                "address": dev.get("address", ""),
                "is_default": alias == default_alias,
                "is_active": alias == active_alias,
                "connected": bool(
                    ble_status and ble_status.get("connected") and alias == active_alias
                ),
                "bound_at": dev.get("bound_at", ""),
                "note": dev.get("note", ""),
            }
        )
    rows.sort(key=lambda r: (not r["is_default"], r["alias"]))
    active = next((r for r in rows if r["is_active"]), None)
    if not active and default_alias and default_alias in devices:
        dev = devices[default_alias]
        active = {
            "alias": default_alias,
            "name": dev.get("name", ""),
            "address": dev.get("address", ""),
            "is_default": True,
            "is_active": default_alias == active_alias,
            "connected": bool(ble_status and ble_status.get("connected")),
        }
    return {
        "default_device": default_alias,
        "active_device": active_alias,
        "active": active,
        "devices": rows,
        "updated_at": time.time(),
    }
