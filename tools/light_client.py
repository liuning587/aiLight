"""Shared BLE light control helpers for CLI and Cursor hooks."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

from bleak import BleakClient, BleakScanner

UART_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
UART_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
DEFAULT_BLE_NAME = "aiLight"


def default_config_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "devices.json"))


def load_devices_config(path: Optional[str] = None) -> dict:
    path = path or default_config_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def resolve_device_target(
    config: dict,
    device_alias: Optional[str] = None,
    name: Optional[str] = None,
    address: Optional[str] = None,
    timeout: Optional[float] = None,
) -> tuple[str, Optional[str], float]:
    devices = config.get("devices") if isinstance(config.get("devices"), dict) else {}

    if address or name:
        return name or DEFAULT_BLE_NAME, address, timeout if timeout is not None else 8.0

    alias = (device_alias or os.environ.get("AILIGHT_DEVICE") or config.get("default_device") or "").strip()
    if alias and alias in devices:
        dev = devices[alias]
        if not isinstance(dev, dict):
            raise RuntimeError(f"Invalid device entry: {alias}")
        dev_name = dev.get("name_prefix") or dev.get("name") or DEFAULT_BLE_NAME
        dev_address = (dev.get("address") or "").strip() or None
        dev_timeout = float(
            timeout if timeout is not None else dev.get("timeout", config.get("default_timeout", 8.0))
        )
        return dev_name, dev_address, dev_timeout

    if alias and alias not in devices:
        raise RuntimeError(f'Device alias not found in config: "{alias}"')

    return DEFAULT_BLE_NAME, None, float(timeout if timeout is not None else config.get("default_timeout", 8.0))


def _name_matches(name: str, pattern: str) -> bool:
    low_name = (name or "").lower()
    low_pattern = (pattern or "").lower()
    if not low_pattern:
        return False
    return low_name == low_pattern or low_name.startswith(low_pattern + "-") or low_pattern in low_name


async def resolve_device(name: str, address: Optional[str], scan_timeout: float = 8.0) -> str:
    if address:
        return address

    device = await BleakScanner.find_device_by_name(name, timeout=scan_timeout)
    if device:
        return device.address

    devices = await BleakScanner.discover(timeout=scan_timeout)
    for d in devices:
        if _name_matches(d.name or "", name):
            return d.address

    devices = await BleakScanner.discover(timeout=scan_timeout, service_uuids=[UART_SERVICE_UUID])
    for d in devices:
        if d.address:
            return d.address

    raise RuntimeError(f"BLE device not found: name={name}, address={address}")


async def send_command_async(
    command: str,
    device_alias: Optional[str] = None,
    name: Optional[str] = None,
    address: Optional[str] = None,
    timeout: Optional[float] = None,
    config_path: Optional[str] = None,
) -> str:
    config = load_devices_config(config_path)
    dev_name, dev_address, dev_timeout = resolve_device_target(
        config, device_alias=device_alias, name=name, address=address, timeout=timeout
    )
    target = await resolve_device(dev_name, dev_address, scan_timeout=dev_timeout)
    result = {"line": ""}
    done = asyncio.Event()

    def on_notify(_: int, data: bytearray):
        text = data.decode("utf-8", "ignore").strip()
        if text:
            result["line"] = text
            done.set()

    async with BleakClient(target, timeout=dev_timeout) as client:
        await client.start_notify(UART_TX_UUID, on_notify)
        await client.write_gatt_char(UART_RX_UUID, (command + "\n").encode("utf-8"))
        try:
            await asyncio.wait_for(done.wait(), timeout=dev_timeout)
        except asyncio.TimeoutError:
            pass
        await client.stop_notify(UART_TX_UUID)
    return result["line"]


def send_command(
    command: str,
    device_alias: Optional[str] = None,
    name: Optional[str] = None,
    address: Optional[str] = None,
    timeout: Optional[float] = None,
    config_path: Optional[str] = None,
) -> tuple[int, str, str]:
    try:
        line = asyncio.run(
            send_command_async(
                command,
                device_alias=device_alias,
                name=name,
                address=address,
                timeout=timeout,
                config_path=config_path,
            )
        )
        return 0, line or "NO_RESPONSE", ""
    except Exception as ex:
        return 1, "", str(ex)
