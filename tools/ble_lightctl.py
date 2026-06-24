#!/usr/bin/env python3
"""
BLE controller for ESP32-C3 traffic light firmware.

Requires:
    pip install bleak
"""

import argparse
import asyncio
import sys
import os

# Ensure project root is importable when run as script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.light_client import (  # noqa: E402
    DEFAULT_BLE_NAME,
    default_config_path,
    load_devices_config,
    send_command_async,
)
from bleak import BleakScanner  # noqa: E402

UART_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"


def parse_nl_to_cmd(text: str):
    s = text.strip().lower()
    if not s:
        return None

    if "自动" in s or "auto" in s:
        return "MODE AUTO"
    if "警示" in s or "黄闪" in s or "flash" in s:
        return "MODE FLASH_YELLOW"
    if "全灭" in s or "关灯" in s or "all off" in s:
        return "MODE ALL_OFF"
    if "手动" in s or "manual" in s:
        return "MODE MANUAL"

    if "红灯闪" in s:
        return "BLINK RED 6 250"
    if "黄灯闪" in s:
        return "BLINK YELLOW 6 250"
    if "绿灯闪" in s:
        return "BLINK GREEN 6 250"

    if "红灯亮" in s or "red on" in s:
        return "SET RED ON"
    if "红灯灭" in s or "red off" in s:
        return "SET RED OFF"
    if "黄灯亮" in s or "yellow on" in s:
        return "SET YELLOW ON"
    if "黄灯灭" in s or "yellow off" in s:
        return "SET YELLOW OFF"
    if "绿灯亮" in s or "green on" in s:
        return "SET GREEN ON"
    if "绿灯灭" in s or "green off" in s:
        return "SET GREEN OFF"

    if "状态" in s or "status" in s:
        return "STATUS"
    if "mac" in s:
        return "MAC"
    if "帮助" in s or "help" in s:
        return "HELP"
    return None


async def scan_devices(timeout: float, show_all: bool):
    devices = await BleakScanner.discover(timeout=timeout)
    rows = []
    for d in devices:
        name = d.name or ""
        if not show_all:
            low = name.lower()
            if ("ailight" not in low) and ("esp32" not in low) and ("traffic" not in low) and ("tl-" not in low):
                continue
        rows.append((d.address, name))
    rows.sort(key=lambda x: (x[1], x[0]))
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control ESP32-C3 traffic light over BLE.")
    parser.add_argument("--config", default=default_config_path(), help="Devices config JSON path")
    parser.add_argument("--list-devices", action="store_true", help="List configured devices")
    parser.add_argument("--scan", action="store_true", help="Scan nearby BLE devices")
    parser.add_argument("--scan-all", action="store_true", help="Include non-traffic devices in scan")
    parser.add_argument("--device", default=None, help="Device alias in devices.json")
    parser.add_argument("--name", default=None, help="BLE advertising name or prefix")
    parser.add_argument("--address", default=None, help="BLE MAC/address (optional)")
    parser.add_argument("--timeout", type=float, default=None, help="BLE connect/response timeout")
    parser.add_argument("--cmd", default=None, help='Raw command, e.g. "MODE AUTO"')
    parser.add_argument("--nl", default=None, help="Natural language command text")
    return parser


def print_devices(config):
    devices = config.get("devices") if isinstance(config.get("devices"), dict) else {}
    if not devices:
        print("No devices configured.")
        return
    default_alias = config.get("default_device")
    for alias, dev in devices.items():
        if not isinstance(dev, dict):
            continue
        marker = " (default)" if alias == default_alias else ""
        print(
            "{}{}: name={}, prefix={}, address={}, timeout={}".format(
                alias,
                marker,
                dev.get("name", DEFAULT_BLE_NAME),
                dev.get("name_prefix", ""),
                dev.get("address", ""),
                dev.get("timeout", config.get("default_timeout", 8.0)),
            )
        )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_devices_config(args.config)

    if args.list_devices:
        print_devices(config)
        return 0

    if args.scan:
        timeout = args.timeout if args.timeout is not None else float(config.get("default_timeout", 8.0))
        rows = asyncio.run(scan_devices(timeout=timeout, show_all=args.scan_all))
        if not rows:
            print("No BLE devices found.")
            return 0
        for addr, name in rows:
            print(f"{addr} | {name}")
        return 0

    command = args.cmd
    if args.nl and not command:
        command = parse_nl_to_cmd(args.nl)

    if not command:
        parser.error('No command found. Use "--cmd" or "--nl".')

    line = asyncio.run(
        send_command_async(
            command,
            device_alias=args.device,
            name=args.name,
            address=args.address,
            timeout=args.timeout,
            config_path=args.config,
        )
    )
    print(line or "NO_RESPONSE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
