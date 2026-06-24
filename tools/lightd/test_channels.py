"""Tests for dual-channel routing helpers."""

from tools.lightd.channels import (
    prefix_ble_command,
    resolve_channel,
)


def test_resolve_channel_by_client_id():
    cfg = {"client_routes": {"cursor-main": "1", "trae-side": "2"}}
    assert resolve_channel(cfg, "cursor-main") == "1"
    assert resolve_channel(cfg, "trae-side") == "2"


def test_resolve_channel_explicit_overrides_client():
    cfg = {"client_routes": {"slot-a": "2"}}
    assert resolve_channel(cfg, "slot-a", channel="1") == "1"


def test_prefix_ble_command_ch2_only():
    assert prefix_ble_command("1", "MODE ALL_OFF") == "MODE ALL_OFF"
    assert prefix_ble_command("2", "MODE FLASH_YELLOW") == "CH2 MODE FLASH_YELLOW"
