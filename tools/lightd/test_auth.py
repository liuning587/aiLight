"""Tests for API token auth."""

from tools.lightd.auth import auth_required, check_auth, redact_config


class _Handler:
    def __init__(self, headers: dict):
        self.headers = headers


def test_empty_token_no_auth():
    cfg = {"api_token": ""}
    assert not auth_required(cfg)
    assert check_auth(_Handler({}), cfg)


def test_token_required():
    cfg = {"api_token": "secret"}
    assert auth_required(cfg)
    assert not check_auth(_Handler({}), cfg)
    assert check_auth(_Handler({"Authorization": "Bearer secret"}), cfg)


def test_redact_config_masks_token():
    out = redact_config({"api_token": "secret", "web_port": 7801})
    assert out["api_token"] == "******"
    assert out["auth_required"] is True
