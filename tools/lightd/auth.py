"""Optional API token auth for lightd HTTP API."""

from __future__ import annotations

import secrets
from http.server import BaseHTTPRequestHandler


PUBLIC_PATHS = frozenset({"/", "/index.html", "/docs", "/help"})


def get_token(cfg: dict) -> str:
    return str(cfg.get("api_token") or "").strip()


def auth_required(cfg: dict) -> bool:
    return bool(get_token(cfg))


def extract_token(handler: BaseHTTPRequestHandler) -> str:
    auth = handler.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = handler.headers.get("X-AiLight-Token", "").strip()
    if header:
        return header
    return ""


def check_auth(handler: BaseHTTPRequestHandler, cfg: dict) -> bool:
    expected = get_token(cfg)
    if not expected:
        return True
    provided = extract_token(handler)
    if not provided:
        return False
    return secrets.compare_digest(provided, expected)


def redact_config(cfg: dict) -> dict:
    out = dict(cfg)
    token = get_token(cfg)
    if token:
        out["api_token"] = "******"
        out["auth_required"] = True
    else:
        out["auth_required"] = False
    return out
