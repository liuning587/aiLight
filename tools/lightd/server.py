"""aiLight daemon HTTP server and web console."""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from tools.lightd.auth import (
    PUBLIC_PATHS,
    auth_required,
    check_auth,
    redact_config,
)
from tools.lightd.ble_worker import BleWorker
from tools.lightd.devices_store import (
    bind_device,
    delete_device,
    list_devices_summary,
    set_default_everywhere,
)
from tools.lightd.paths import project_root, resource_path
from tools.lightd.event_log import EventLog
from tools.lightd.state_machine import StateMachine

_DOCS_MD_CANDIDATES = ("docs/使用说明.md",)
_CONFIG_HOT_KEYS = frozenset(
    {
        "done_timeout_sec",
        "waiting_timeout_sec",
        "error_display_sec",
        "state_commands",
        "dry_run",
        "ble_keepalive_sec",
        "api_token",
        "event_log_max",
    }
)


def _config_path() -> str:
    return os.path.join(project_root(), "config.json")


def _console_html_path() -> str:
    bundled = resource_path("tools", "lightd", "console.html")
    if os.path.isfile(bundled):
        return bundled
    return os.path.join(os.path.dirname(__file__), "console.html")


def _docs_html_path() -> str:
    bundled = resource_path("tools", "lightd", "docs.html")
    if os.path.isfile(bundled):
        return bundled
    return os.path.join(os.path.dirname(__file__), "docs.html")


def _docs_md_path() -> str | None:
    for rel in _DOCS_MD_CANDIDATES:
        bundled = resource_path(*rel.split("/"))
        if os.path.isfile(bundled):
            return bundled
        local = os.path.join(project_root(), *rel.split("/"))
        if os.path.isfile(local):
            return local
    return None


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


def load_console_html() -> str:
    with open(_console_html_path(), "r", encoding="utf-8") as f:
        return f.read()


def load_docs_html() -> str:
    with open(_docs_html_path(), "r", encoding="utf-8") as f:
        return f.read()


def load_docs_markdown() -> tuple[str, str]:
    path = _docs_md_path()
    if not path:
        return (
            "使用说明",
            "# 使用说明\n\n未找到 `docs/使用说明.md`，请确认文件存在于 aiLight 项目目录。",
        )
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    title = "使用说明"
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return title, text


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
        self.event_log = EventLog(maxlen=int(cfg.get("event_log_max", 100)))
        self._keepalive_sec = float(cfg.get("ble_keepalive_sec", 30))
        self._last_keepalive_at = 0.0
        self._keepalive_ok = True
        devices_cfg = cfg.get("devices_config", "devices.json")
        self.devices_config = (
            devices_cfg
            if os.path.isabs(devices_cfg)
            else os.path.join(project_root(), devices_cfg)
        )
        self.ble = BleWorker(
            device_alias=self.device_alias, config_path=self.devices_config
        )
        self.current_phase = "idle"
        self._lock = threading.Lock()
        self._last_ble_ok = True
        self._last_ble_msg = ""
        self._ble_pending: str | None = None
        self._ble_wake = threading.Event()
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
        self._keepalive = threading.Thread(
            target=self._keepalive_loop, name="ailight-keepalive", daemon=True
        )
        if not self.dry_run:
            self._keepalive.start()

    def apply_config(self, cfg: dict) -> None:
        with self._lock:
            self.cfg = cfg
            self.sm.done_timeout_sec = float(cfg.get("done_timeout_sec", 60))
            self.sm.waiting_timeout_sec = float(cfg.get("waiting_timeout_sec", 300))
            self.sm.error_display_sec = float(cfg.get("error_display_sec", 4))
            self.commands = cfg.get("state_commands") or {}
            self.dry_run = bool(cfg.get("dry_run", False))
            self._keepalive_sec = float(cfg.get("ble_keepalive_sec", 30))

    def _record_event(
        self,
        event: str,
        phase: str,
        session_id: str | None = None,
        source: str = "hook",
        detail: str = "",
    ) -> None:
        self.event_log.add(
            event=event,
            phase=phase,
            session_id=session_id,
            source=source,
            detail=detail,
        )

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
            self._ble_wake.set()
            return True, f"QUEUED {cmd}"
        ok, msg = self.ble.send(cmd)
        self._last_ble_ok = ok
        self._last_ble_msg = msg
        return ok, msg

    def _ble_queue_loop(self) -> None:
        while True:
            self._ble_wake.wait(timeout=0.1)
            self._ble_wake.clear()
            cmd = None
            with self._lock:
                if self._ble_pending:
                    cmd = self._ble_pending
                    self._ble_pending = None
            if not cmd:
                continue
            # Coalesce rapid hook bursts; keep short so single events stay snappy.
            time.sleep(0.025)
            with self._lock:
                if self._ble_pending:
                    cmd = self._ble_pending
                    self._ble_pending = None
            if not self.dry_run:
                ok, msg = self.ble.send(cmd, timeout=2.0)
                with self._lock:
                    self._last_ble_ok = ok
                    self._last_ble_msg = msg

    def handle_event(
        self,
        event: str,
        session_id: str | None = None,
        source: str = "hook",
    ) -> dict:
        with self._lock:
            phase, reason = self.sm.apply(event, session_id=session_id)
            if reason == "no_change":
                self._record_event(
                    event, phase, session_id=session_id, source=source, detail=reason
                )
                return {
                    "ok": True,
                    "phase": phase,
                    "skipped": True,
                    "reason": reason,
                    "ble_ok": self._last_ble_ok,
                }
            ok, msg = self._apply_phase(phase, async_send=True)
            self._record_event(
                event, phase, session_id=session_id, source=source, detail=msg
            )
            return {
                "ok": ok,
                "phase": phase,
                "event": event,
                "command": self.commands.get(phase),
                "queued": True,
                "ble_message": msg,
            }

    def send_command(self, command: str, device_alias: str | None = None) -> dict:
        command = (command or "").strip()
        if not command:
            return {"ok": False, "error": "command required"}
        with self._lock:
            if self.dry_run:
                return {"ok": True, "response": f"DRY_RUN {command}"}
            target = (device_alias or "").strip() or None
            if target and target != self.device_alias:
                set_default_everywhere(
                    target,
                    self.devices_config,
                    _config_path(),
                    project_root(),
                )
                self.device_alias = target
                self.cfg["default_device"] = target
                ok_sw, msg_sw = self.ble.switch_device(target)
                if not ok_sw:
                    return {"ok": False, "error": msg_sw}
            ok, msg = self.ble.send(command)
            self._last_ble_ok = ok
            self._last_ble_msg = msg
            return {
                "ok": ok,
                "response": msg,
                "ble_message": msg,
                "error": "" if ok else msg,
            }

    def status(self) -> dict:
        with self._lock:
            data = self.sm.state.to_dict()
            data["command"] = self.commands.get(data["phase"])
            data["dry_run"] = self.dry_run
            data["ble"] = self.ble.status() if not self.dry_run else {}
            data["ble_ok"] = self._last_ble_ok
            data["ble_message"] = self._last_ble_msg
            data["default_device"] = self.device_alias
            data["active_sessions"] = self.sm.session_count()
            data["auth_required"] = auth_required(self.cfg)
            data["ble_keepalive"] = {
                "interval_sec": self._keepalive_sec,
                "last_at": self._last_keepalive_at,
                "last_ok": self._keepalive_ok,
            }
            if data["ble"]:
                data["ble"]["keepalive_ok"] = self._keepalive_ok
                data["ble"]["keepalive_at"] = self._last_keepalive_at
            return data

    def devices_payload(self) -> dict:
        ble = self.ble.status() if not self.dry_run else {}
        return list_devices_summary(
            self.devices_config, self.device_alias, ble_status=ble
        )

    def scan_devices(self, timeout: float = 8.0, show_all: bool = False) -> dict:
        with self._lock:
            if self.dry_run:
                return {"ok": True, "devices": []}
            ok, result = self.ble.scan(timeout=timeout, show_all=show_all)
            if not ok:
                return {"ok": False, "error": result, "devices": []}
            from tools.light_client import load_devices_config

            cfg = load_devices_config(self.devices_config)
            devices = cfg.get("devices") if isinstance(cfg.get("devices"), dict) else {}
            bound = {
                (d.get("address") or "").upper()
                for d in devices.values()
                if isinstance(d, dict)
            }
            for row in result:
                row["already_bound"] = row["address"].upper() in bound
            return {"ok": True, "devices": result}

    def bind_and_use(
        self,
        address: str,
        name: str,
        alias: str | None = None,
        set_default: bool = True,
        run_test: bool = True,
    ) -> dict:
        with self._lock:
            new_alias, _ = bind_device(self.devices_config, address, name, alias=alias)
            if set_default:
                set_default_everywhere(
                    new_alias,
                    self.devices_config,
                    _config_path(),
                    project_root(),
                )
                self.device_alias = new_alias
                self.cfg["default_device"] = new_alias
            self.sm.reset()
            self.current_phase = "idle"

        switch_ok, switch_msg = True, "dry_run"
        test_resp = ""
        if not self.dry_run:
            switch_ok, switch_msg = self.ble.switch_device(new_alias)
            if run_test and switch_ok:
                ok, test_resp = self.ble.send("MAC", timeout=3.0)
                if ok:
                    self._last_ble_ok = True
                    self._last_ble_msg = test_resp
                self.ble.send("MODE ALL_OFF", timeout=2.0)

        return {
            "ok": switch_ok,
            "alias": new_alias,
            "switch": switch_msg,
            "test": test_resp,
        }

    def activate_device(self, alias: str) -> dict:
        with self._lock:
            set_default_everywhere(
                alias,
                self.devices_config,
                _config_path(),
                project_root(),
            )
            self.device_alias = alias
            self.cfg["default_device"] = alias
            self.sm.reset()
            self.current_phase = "idle"

        if self.dry_run:
            return {"ok": True, "alias": alias}
        ok, msg = self.ble.switch_device(alias)
        return {"ok": ok, "alias": alias, "message": msg}

    def test_device(self, alias: str | None = None) -> dict:
        target = alias or self.device_alias
        if target and target != self.device_alias and not self.dry_run:
            act = self.activate_device(target)
            if not act.get("ok"):
                return {"ok": False, "error": act.get("message", "activate failed")}
        if self.dry_run:
            return {"ok": True, "response": "DRY_RUN"}
        ok, msg = self.ble.send("STATUS", timeout=3.0)
        self._last_ble_ok = ok
        self._last_ble_msg = msg
        return {"ok": ok, "response": msg, "error": "" if ok else msg}

    def remove_device(self, alias: str) -> dict:
        with self._lock:
            was_active = alias == self.device_alias
            delete_device(self.devices_config, alias)
            from tools.light_client import load_devices_config

            cfg = load_devices_config(self.devices_config)
            new_default = (cfg.get("default_device") or "").strip()
            if new_default:
                set_default_everywhere(
                    new_default,
                    self.devices_config,
                    _config_path(),
                    project_root(),
                )
                self.device_alias = new_default
                self.cfg["default_device"] = new_default
            else:
                self.device_alias = None

        if was_active and self.device_alias and not self.dry_run:
            self.ble.switch_device(self.device_alias)
        return {"ok": True, "default_device": self.device_alias}

    def _tick_loop(self) -> None:
        while True:
            time.sleep(1.0)
            with self._lock:
                new_phase = self.sm.tick()
                if new_phase and new_phase != self.current_phase:
                    self._apply_phase(new_phase, async_send=True)

    def _keepalive_loop(self) -> None:
        while True:
            with self._lock:
                interval = max(5.0, float(self._keepalive_sec))
            time.sleep(interval)
            if self.dry_run:
                continue
            ok, msg = self.ble.ping(timeout=3.0)
            with self._lock:
                self._last_keepalive_at = time.time()
                self._keepalive_ok = ok
                if ok:
                    self._last_ble_ok = True
                    self._last_ble_msg = msg
                else:
                    self._last_ble_ok = False
                    self._last_ble_msg = msg
                    self._record_event(
                        "ble_keepalive_fail",
                        self.current_phase,
                        source="system",
                        detail=msg,
                    )
            if not ok:
                self.ble.reconnect()


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

        def _authorized(self, path: str) -> bool:
            if path in PUBLIC_PATHS:
                return True
            if check_auth(self, daemon.cfg):
                return True
            self._json(
                401,
                {
                    "error": "unauthorized",
                    "auth_required": auth_required(daemon.cfg),
                },
            )
            return False

        def do_GET(self):
            path = urlparse(self.path).path
            if not self._authorized(path):
                return
            if path in ("/", "/index.html"):
                body = load_console_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path in ("/docs", "/help"):
                body = load_docs_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/docs":
                title, content = load_docs_markdown()
                self._json(200, {"title": title, "content": content})
                return
            if path == "/api/status":
                self._json(200, daemon.status())
                return
            if path == "/api/config":
                self._json(200, redact_config(load_config()))
                return
            if path == "/api/events":
                limit = 50
                parsed = urlparse(self.path)
                if parsed.query:
                    for part in parsed.query.split("&"):
                        if part.startswith("limit="):
                            try:
                                limit = int(part.split("=", 1)[1])
                            except ValueError:
                                pass
                self._json(200, {"events": daemon.event_log.list(limit=limit)})
                return
            if path == "/api/devices":
                self._json(200, daemon.devices_payload())
                return
            self._json(404, {"error": "not found"})

        def do_DELETE(self):
            path = urlparse(self.path).path
            if not self._authorized(path):
                return
            prefix = "/api/devices/"
            if path.startswith(prefix):
                alias = path[len(prefix) :].strip("/")
                if not alias:
                    self._json(400, {"error": "alias required"})
                    return
                try:
                    self._json(200, daemon.remove_device(alias))
                except KeyError:
                    self._json(404, {"error": "device not found"})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._authorized(path):
                return
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
                session_id = (payload.get("session_id") or "").strip() or None
                source = (payload.get("source") or "hook").strip() or "hook"
                self._json(
                    200,
                    daemon.handle_event(event, session_id=session_id, source=source),
                )
                return
            if path == "/api/command":
                command = (payload.get("command") or "").strip()
                if not command:
                    self._json(400, {"error": "command required"})
                    return
                device = (payload.get("device") or "").strip() or None
                result = daemon.send_command(command, device_alias=device)
                self._json(200 if result.get("ok") else 502, result)
                return
            if path == "/api/devices/scan":
                timeout = float(payload.get("timeout", 8))
                show_all = bool(payload.get("show_all", False))
                self._json(200, daemon.scan_devices(timeout=timeout, show_all=show_all))
                return
            if path == "/api/devices/bind":
                address = (payload.get("address") or "").strip()
                name = (payload.get("name") or "").strip()
                if not address:
                    self._json(400, {"error": "address required"})
                    return
                result = daemon.bind_and_use(
                    address=address,
                    name=name,
                    alias=(payload.get("alias") or "").strip() or None,
                    set_default=bool(payload.get("set_default", True)),
                    run_test=bool(payload.get("run_test", True)),
                )
                self._json(200 if result.get("ok") else 502, result)
                return
            if path == "/api/devices/activate":
                alias = (payload.get("alias") or "").strip()
                if not alias:
                    self._json(400, {"error": "alias required"})
                    return
                self._json(200, daemon.activate_device(alias))
                return
            if path == "/api/devices/test":
                alias = (payload.get("alias") or "").strip() or None
                self._json(200, daemon.test_device(alias=alias))
                return
            if path == "/api/config":
                updates = {k: payload[k] for k in _CONFIG_HOT_KEYS if k in payload}
                if updates.get("api_token") in ("******", ""):
                    updates.pop("api_token", None)
                cfg = load_config()
                cfg.update(updates)
                save_config(cfg)
                daemon.apply_config(cfg)
                self._json(
                    200,
                    {
                        "ok": True,
                        "config": redact_config(cfg),
                        "reloaded": True,
                    },
                )
                return
            self._json(404, {"error": "not found"})

    return Handler


class SingleInstanceHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False


def run_server(host: str | None = None, port: int | None = None) -> None:
    cfg = load_config()
    host = host or str(cfg.get("web_host", "127.0.0.1"))
    port = port or int(cfg.get("web_port", 7801))
    daemon = Daemon(cfg)
    handler = make_handler(daemon)
    try:
        httpd = SingleInstanceHTTPServer((host, port), handler)
    except OSError as ex:
        print(f"Port {port} already in use ({ex}). Stop the other lightd first.")
        raise SystemExit(1) from ex
    print(f"aiLight daemon listening on http://{host}:{port}")
    if host in ("0.0.0.0", "::"):
        print(f"LAN access: http://<本机IP>:{port}")
    print(f"dry_run={daemon.dry_run} device={daemon.device_alias}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    run_server()
