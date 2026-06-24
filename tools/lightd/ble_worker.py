"""Background BLE worker with persistent asyncio loop."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional

from tools.light_client import (
    send_command_async,
    default_config_path,
)


class BleWorker:
    def __init__(self, device_alias: str | None = None, config_path: str | None = None):
        self.device_alias = device_alias
        self.config_path = config_path or default_config_path()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
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
        self._ready.wait(timeout=5.0)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def send(self, command: str, timeout: float = 10.0) -> tuple[bool, str]:
        if not self._loop:
            self.start()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(
            send_command_async(
                command,
                device_alias=self.device_alias,
                config_path=self.config_path,
            ),
            self._loop,
        )
        try:
            line = future.result(timeout=timeout)
            self._last_send = time.time()
            self._last_response = line or "NO_RESPONSE"
            self._last_error = ""
            return True, self._last_response
        except Exception as ex:
            self._last_error = str(ex)
            return False, self._last_error

    def status(self) -> dict:
        return {
            "last_send": self._last_send,
            "last_response": self._last_response,
            "last_error": self._last_error,
            "device_alias": self.device_alias,
        }

    def stop(self) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
