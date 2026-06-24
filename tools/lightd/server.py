"""aiLight daemon HTTP server and web console."""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from tools.lightd.ble_worker import BleWorker
from tools.lightd.state_machine import StateMachine

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _config_path() -> str:
    return os.path.join(PROJECT_ROOT, "config.json")


def load_config() -> dict:
    path = _config_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict) -> None:
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


WEB_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>aiLight Console</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; background: #0f1419; color: #e7ecf3; }
    h1 { margin-bottom: 4px; }
    .sub { color: #8b98a5; margin-bottom: 20px; }
    .card { background: #1a2332; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
    .phase { font-size: 28px; font-weight: 700; }
    button { margin: 4px; padding: 8px 12px; border: 0; border-radius: 6px; cursor: pointer; }
    .ok { background: #1f8b4c; color: white; }
    .warn { background: #c9a227; color: #111; }
    .err { background: #c0392b; color: white; }
    pre { background: #0b1017; padding: 12px; border-radius: 8px; overflow: auto; }
  </style>
</head>
<body>
  <h1>aiLight Console</h1>
  <p class="sub">本地守护进程 · 对标 PromLight 状态机</p>
  <div class="card">
    <div>当前状态</div>
    <div class="phase" id="phase">-</div>
    <div id="detail"></div>
  </div>
  <div class="card">
    <div>手动测试</div>
    <button class="warn" onclick="ev('thinking')">思考(黄慢闪)</button>
    <button class="warn" onclick="ev('tool_start')">忙碌(黄快闪)</button>
    <button class="err" onclick="ev('permission_wait')">等待(红灯)</button>
    <button class="ok" onclick="ev('session_stop')">完成(绿灯)</button>
    <button class="err" onclick="ev('tool_failure')">出错(红闪)</button>
    <button onclick="ev('force_idle')">空闲(全灭)</button>
  </div>
  <div class="card"><pre id="raw">loading...</pre></div>
  <script>
    async function refresh() {
      const r = await fetch('/api/status');
      const j = await r.json();
      document.getElementById('phase').textContent = j.phase || '-';
      document.getElementById('detail').textContent =
        'event=' + (j.last_event||'') + ' | ble=' + (j.ble_ok?'ok':'fail');
      document.getElementById('raw').textContent = JSON.stringify(j, null, 2);
    }
    async function ev(name) {
      await fetch('/api/event', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({event: name})
      });
      refresh();
    }
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


class Daemon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sm = StateMachine(
            done_timeout_sec=float(cfg.get("done_timeout_sec", 60)),
            waiting_timeout_sec=float(cfg.get("waiting_timeout_sec", 300)),
            error_display_sec=float(cfg.get("error_display_sec", 4)),
        )
        self.commands = cfg.get("state_commands") or {}
        self.dry_run = bool(cfg.get("dry_run", False))
        self.device_alias = cfg.get("default_device")
        devices_cfg = cfg.get("devices_config", "devices.json")
        self.devices_config = (
            devices_cfg
            if os.path.isabs(devices_cfg)
            else os.path.join(PROJECT_ROOT, devices_cfg)
        )
        self.ble = BleWorker(
            device_alias=self.device_alias, config_path=self.devices_config
        )
        self.current_phase = "idle"
        self._lock = threading.Lock()
        self._last_ble_ok = True
        self._last_ble_msg = ""
        self._ble_pending: str | None = None
        self._ble_worker_thread = threading.Thread(
            target=self._ble_queue_loop, name="ailight-ble-queue", daemon=True
        )
        if not self.dry_run:
            self.ble.start()
        self._ble_worker_thread.start()
        self._ticker = threading.Thread(
            target=self._tick_loop, name="ailight-ticker", daemon=True
        )
        self._ticker.start()

    def _apply_phase(self, phase: str, async_send: bool = False) -> tuple[bool, str]:
        cmd = self.commands.get(phase)
        if not cmd:
            return False, f"no command for phase {phase}"
        self.current_phase = phase
        self.sm.state.last_command = cmd
        if self.dry_run:
            return True, f"DRY_RUN {cmd}"
        if async_send:
            self._ble_pending = cmd
            return True, f"QUEUED {cmd}"
        ok, msg = self.ble.send(cmd)
        self._last_ble_ok = ok
        self._last_ble_msg = msg
        return ok, msg

    def _ble_queue_loop(self) -> None:
        while True:
            cmd = None
            with self._lock:
                if self._ble_pending:
                    cmd = self._ble_pending
                    self._ble_pending = None
            if not cmd:
                time.sleep(0.05)
                continue
            # Coalesce bursts: wait briefly for newer commands before sending.
            time.sleep(0.08)
            with self._lock:
                if self._ble_pending:
                    cmd = self._ble_pending
                    self._ble_pending = None
            if not self.dry_run:
                ok, msg = self.ble.send(cmd, timeout=6.0)
                with self._lock:
                    self._last_ble_ok = ok
                    self._last_ble_msg = msg

    def handle_event(self, event: str) -> dict:
        with self._lock:
            phase, reason = self.sm.apply(event)
            if reason == "no_change":
                return {
                    "ok": True,
                    "phase": phase,
                    "skipped": True,
                    "reason": reason,
                    "ble_ok": self._last_ble_ok,
                }
            ok, msg = self._apply_phase(phase, async_send=True)
            return {
                "ok": ok,
                "phase": phase,
                "event": event,
                "command": self.commands.get(phase),
                "queued": True,
                "ble_message": msg,
            }

    def status(self) -> dict:
        with self._lock:
            data = self.sm.state.to_dict()
            data["command"] = self.commands.get(data["phase"])
            data["dry_run"] = self.dry_run
            data["ble"] = self.ble.status() if not self.dry_run else {}
            data["ble_ok"] = self._last_ble_ok
            data["ble_message"] = self._last_ble_msg
            return data

    def _tick_loop(self) -> None:
        while True:
            time.sleep(1.0)
            with self._lock:
                new_phase = self.sm.tick()
                if new_phase and new_phase != self.current_phase:
                    self._apply_phase(new_phase, async_send=True)


def make_handler(daemon: Daemon):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                body = WEB_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/status":
                self._json(200, daemon.status())
                return
            if path == "/api/config":
                self._json(200, load_config())
                return
            self._json(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            if path == "/api/event":
                event = (payload.get("event") or "").strip()
                if not event:
                    self._json(400, {"error": "event required"})
                    return
                self._json(200, daemon.handle_event(event))
                return
            if path == "/api/config":
                cfg = load_config()
                cfg.update(payload)
                save_config(cfg)
                self._json(200, {"ok": True, "config": cfg})
                return
            self._json(404, {"error": "not found"})

    return Handler


class SingleInstanceHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False


def run_server(host: str = "127.0.0.1", port: int | None = None) -> None:
    cfg = load_config()
    port = port or int(cfg.get("web_port", 7801))
    daemon = Daemon(cfg)
    handler = make_handler(daemon)
    try:
        httpd = SingleInstanceHTTPServer((host, port), handler)
    except OSError as ex:
        print(f"Port {port} already in use ({ex}). Stop the other lightd first.")
        raise SystemExit(1) from ex
    print(f"aiLight daemon running at http://{host}:{port}")
    print(f"dry_run={daemon.dry_run} device={daemon.device_alias}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    run_server()
