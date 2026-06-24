#!/usr/bin/env python3
"""
BLE controller for ESP32-C3 traffic light firmware.

Requires:
    pip install bleak
"""

import argparse
import asyncio
from typing import Optional

from bleak import BleakClient, BleakScanner

UART_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # write from PC -> ESP32
UART_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # notify from ESP32 -> PC


def parse_nl_to_cmd(text: str) -> Optional[str]:
    s = text.strip().lower()
    if not s:
        return None

    # Mode commands
    if "自动" in s or "auto" in s:
        return "MODE AUTO"
    if "警示" in s or "黄闪" in s or "flash" in s:
        return "MODE FLASH_YELLOW"
    if "全灭" in s or "关灯" in s or "all off" in s:
        return "MODE ALL_OFF"
    if "手动" in s or "manual" in s:
        return "MODE MANUAL"

    # Blink commands
    if "红灯闪" in s:
        return "BLINK RED 6 250"
    if "黄灯闪" in s:
        return "BLINK YELLOW 6 250"
    if "绿灯闪" in s:
        return "BLINK GREEN 6 250"

    # Set commands
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
    if "帮助" in s or "help" in s:
        return "HELP"
    return None


async def resolve_device(name: str, address: Optional[str]) -> str:
    if address:
        return address
    device = await BleakScanner.find_device_by_name(name, timeout=8.0)
    if device:
        return device.address

    # Fallback: scan by UART service UUID when name discovery is unreliable.
    devices = await BleakScanner.discover(timeout=8.0, service_uuids=[UART_SERVICE_UUID])
    for d in devices:
        if d.address:
            return d.address

    # Last fallback: try matching by prefix in discovered names.
    devices = await BleakScanner.discover(timeout=8.0)
    for d in devices:
        dn = (d.name or "").lower()
        if "esp32" in dn or "traffic" in dn:
            return d.address

    raise RuntimeError(
        f"BLE device not found by name/service: {name} / {UART_SERVICE_UUID}"
    )


async def send_command(name: str, address: Optional[str], command: str, timeout: float) -> str:
    target = await resolve_device(name, address)
    result = {"line": ""}
    done = asyncio.Event()

    def on_notify(_: int, data: bytearray):
        text = data.decode("utf-8", "ignore").strip()
        if text:
            result["line"] = text
            done.set()

    async with BleakClient(target, timeout=timeout) as client:
        await client.start_notify(UART_TX_UUID, on_notify)
        await client.write_gatt_char(UART_RX_UUID, (command + "\n").encode("utf-8"))
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        await client.stop_notify(UART_TX_UUID)
    return result["line"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control ESP32-C3 traffic light over BLE.")
    parser.add_argument("--name", default="ESP32C3-Traffic", help="BLE advertising name")
    parser.add_argument("--address", default=None, help="BLE MAC/address (optional)")
    parser.add_argument("--timeout", type=float, default=8.0, help="BLE connect/response timeout")
    parser.add_argument("--cmd", default=None, help='Raw command, e.g. "MODE AUTO"')
    parser.add_argument("--nl", default=None, help="Natural language command text")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    command = args.cmd
    if args.nl and not command:
        command = parse_nl_to_cmd(args.nl)

    if not command:
        parser.error('No command found. Use "--cmd" or "--nl".')

    line = asyncio.run(send_command(args.name, args.address, command, args.timeout))
    if line:
        print(line)
    else:
        print("NO_RESPONSE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

