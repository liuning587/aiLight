"""Dual traffic-light channel routing (single BLE, GPIO CH1/CH2)."""

from __future__ import annotations

DEFAULT_CHANNELS: dict[str, dict[str, str]] = {
    "1": {"label": "灯位 A", "gpio": "GPIO2/3/4"},
    "2": {"label": "灯位 B", "gpio": "R7/Y6/G5, GPIO8=LOW"},
}

DEFAULT_CLIENT_ROUTES: dict[str, str] = {
    "slot-a": "1",
    "slot-b": "2",
}


def list_channel_ids(cfg: dict | None = None) -> list[str]:
    cfg = cfg or {}
    channels = cfg.get("channels")
    if isinstance(channels, dict) and channels:
        return sorted((str(k) for k in channels.keys()), key=lambda x: (len(x), x))
    return ["1", "2"]


def channel_labels(cfg: dict | None = None) -> dict[str, str]:
    cfg = cfg or {}
    channels = cfg.get("channels")
    if not isinstance(channels, dict):
        channels = DEFAULT_CHANNELS
    out: dict[str, str] = {}
    for ch in list_channel_ids(cfg):
        meta = channels.get(ch) if isinstance(channels.get(ch), dict) else {}
        label = (meta or {}).get("label") if isinstance(meta, dict) else None
        out[ch] = str(
            label or DEFAULT_CHANNELS.get(ch, {}).get("label") or f"灯位 {ch}"
        )
    return out


def normalize_channel(channel: str | int | None) -> str:
    text = str(channel or "1").strip().upper()
    if text.startswith("CH"):
        text = text[2:]
    if text in ("1", "2"):
        return text
    return "1"


def resolve_channel(
    cfg: dict,
    client_id: str | None = None,
    *,
    channel: str | None = None,
) -> str:
    if channel is not None and str(channel).strip():
        return normalize_channel(channel)
    routes = cfg.get("client_routes")
    if not isinstance(routes, dict):
        routes = DEFAULT_CLIENT_ROUTES
    cid = (client_id or "").strip()
    if cid and cid in routes:
        return normalize_channel(str(routes[cid]))
    return normalize_channel(cfg.get("default_channel", "1"))


def prefix_ble_command(channel: str, command: str) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return cmd
    ch = normalize_channel(channel)
    if ch == "1":
        return cmd
    return f"CH{ch} {cmd}"


def client_routes_payload(cfg: dict) -> dict[str, str]:
    routes = cfg.get("client_routes")
    if not isinstance(routes, dict):
        routes = dict(DEFAULT_CLIENT_ROUTES)
    return {str(k): normalize_channel(str(v)) for k, v in routes.items()}
