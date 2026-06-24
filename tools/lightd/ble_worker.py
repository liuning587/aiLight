"""Background BLE worker with persistent connection."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional

from bleak import BleakClient

from tools.light_client import (
    UART_RX_UUID,
    UART_TX_UUID,
    default_config_path,
    load_devices_config,
    resolve_device,
    resolve_device_target,
    scan_ailight_devices,
)


class BleSession:
    """Keep one GATT connection open so mode switches are near-instant."""

    def __init__(
        self,
        device_alias: str | None = None,
        config_path: str | None = None,
    ):
        self.device_alias = device_alias
        self.config_path = config_path or default_config_path()
        self._client: BleakClient | None = None
        self._address: str | None = None
        self._timeout = 8.0
        self._line = ""
        self._done = asyncio.Event()
        self._lock = asyncio.Lock()

    def _on_notify(self, _: int, data: bytearray) -> None:
        text = data.decode("utf-8", "ignore").strip()
        if text:
            self._line = text
            self._done.set()

    async def _disconnect(self) -> None:
        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(UART_TX_UUID)
            except Exception:
                pass
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._client = None

    async def ensure_connected(self) -> None:
        config = load_devices_config(self.config_path)
        dev_name, dev_address, dev_timeout = resolve_device_target(
            config, device_alias=self.device_alias
        )
        self._timeout = dev_timeout
        target = await resolve_device(
            dev_name,
            dev_address,
            scan_timeout=min(dev_timeout, 4.0),
        )
        if self._client and self._client.is_connected and self._address == target:
            return
        await self._disconnect()
        self._address = target
        client = BleakClient(target, timeout=dev_timeout)
        await client.connect()
        await client.start_notify(UART_TX_UUID, self._on_notify)
        self._client = client

    async def send(self, command: str, timeout: float = 2.0) -> str:
        async with self._lock:
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    await self.ensure_connected()
                    assert self._client is not None
                    self._line = ""
                    self._done.clear()
                    await self._client.write_gatt_char(
                        UART_RX_UUID, (command + "\n").encode("utf-8")
                    )
                    try:
                        await asyncio.wait_for(self._done.wait(), timeout=timeout)
                    except asyncio.TimeoutError:
                        pass
                    return self._line or "NO_RESPONSE"
                except Exception as ex:
                    last_error = ex
                    await self._disconnect()
            raise last_error or RuntimeError("BLE send failed")

    async def close(self) -> None:
        async with self._lock:
            await self._disconnect()

    def set_device_alias(self, alias: str | None) -> None:
        self.device_alias = alias
        self._address = None

    async def scan_nearby(
        self, timeout: float = 8.0, show_all: bool = False, reconnect: bool = True
    ) -> list[dict]:
        async with self._lock:
            await self._disconnect()
            self._address = None
            results = await scan_ailight_devices(timeout=timeout, show_all=show_all)
            if reconnect:
                try:
                    await self.ensure_connected()
                except Exception:
                    pass
            return results

    async def reconnect(self) -> None:
        async with self._lock:
            await self._disconnect()
            self._address = None
            await self.ensure_connected()


class BleWorker:
    def __init__(self, device_alias: str | None = None, config_path: str | None = None):
        self.device_alias = device_alias
        self.config_path = config_path or default_config_path()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._session: BleSession | None = None
        self._last_send = 0.0
        self._last_response = ""
        self._last_error = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop, name="ailight-ble", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            return
        # Pre-connect so the first hook event does not pay connect latency.
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._session.ensure_connected(),  # type: ignore[union-attr]
                self._loop,  # type: ignore[arg-type]
            )
            future.result(timeout=12.0)
        except Exception as ex:
            self._last_error = str(ex)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._session = BleSession(
            device_alias=self.device_alias, config_path=self.config_path
        )
        self._ready.set()
        self._loop.run_forever()

    def scan(
        self, timeout: float = 8.0, show_all: bool = False
    ) -> tuple[bool, list[dict] | str]:
        if not self._loop:
            self.start()
        assert self._loop is not None and self._session is not None
        future = asyncio.run_coroutine_threadsafe(
            self._session.scan_nearby(timeout=timeout, show_all=show_all),
            self._loop,
        )
        try:
            rows = future.result(timeout=timeout + 12.0)
            return True, rows
        except Exception as ex:
            self._last_error = str(ex)
            return False, str(ex)

    def switch_device(self, alias: str) -> tuple[bool, str]:
        if not self._loop:
            self.start()
        assert self._loop is not None and self._session is not None
        self.device_alias = alias
        self._session.set_device_alias(alias)
        future = asyncio.run_coroutine_threadsafe(
            self._session.reconnect(),
            self._loop,
        )
        try:
            future.result(timeout=15.0)
            self._last_error = ""
            return True, "connected"
        except Exception as ex:
            self._last_error = str(ex)
            return False, str(ex)

    def send(self, command: str, timeout: float = 3.0) -> tuple[bool, str]:
        if not self._loop:
            self.start()
        assert self._loop is not None and self._session is not None
        future = asyncio.run_coroutine_threadsafe(
            self._session.send(command, timeout=timeout),
            self._loop,
        )
        try:
            line = future.result(timeout=timeout + 4.0)
            self._last_send = time.time()
            self._last_response = line or "NO_RESPONSE"
            self._last_error = ""
            return True, self._last_response
        except Exception as ex:
            self._last_error = str(ex)
            return False, self._last_error

    def status(self) -> dict:
        connected = False
        if self._session and self._session._client:
            connected = bool(self._session._client.is_connected)
        return {
            "connected": connected,
            "last_send": self._last_send,
            "last_response": self._last_response,
            "last_error": self._last_error,
            "device_alias": self.device_alias,
        }

    def stop(self) -> None:
        if self._loop and self._session:
            asyncio.run_coroutine_threadsafe(self._session.close(), self._loop).result(
                timeout=5.0
            )
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
