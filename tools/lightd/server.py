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
from tools.lightd.channels import (
    channel_labels,
    client_routes_payload,
    list_channel_ids,
    prefix_ble_command,
    resolve_channel,
)
from tools.lightd.state_machine import StateMachine
from tools.light_client import load_devices_config

_DOCS_MD_CANDIDATES = ("docs/使用说明.md",)
_CONFIG_HOT_KEYS = frozenset(
    {
        "done_timeout_sec",
        "waiting_timeout_sec",
        "error_display_sec",
        "busy_timeout_sec",
        "state_commands",
        "dry_run",
        "ble_keepalive_sec",
        "api_token",
        "event_log_max",
        "client_routes",
        "channels",
        "default_channel",
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
        self._sms: dict[str, StateMachine] = {}
        self._channel_phases: dict[str, str] = {}
        self._init_channel_state_machines(cfg)
        self.ble = BleWorker(
            device_alias=self.device_alias, config_path=self.devices_config
        )
        self._lock = threading.Lock()
        self._last_ble_ok = True
        self._last_ble_msg = ""
        self._ble_queue: list[str] = []
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

    def _sm_params(self, cfg: dict) -> dict:
        return {
            "done_timeout_sec": float(cfg.get("done_timeout_sec", 60)),
            "waiting_timeout_sec": float(cfg.get("waiting_timeout_sec", 300)),
            "error_display_sec": float(cfg.get("error_display_sec", 4)),
            "busy_timeout_sec": float(cfg.get("busy_timeout_sec", 120)),
        }

    def _init_channel_state_machines(self, cfg: dict) -> None:
        params = self._sm_params(cfg)
        for ch in list_channel_ids(cfg):
            if ch not in self._sms:
                self._sms[ch] = StateMachine(**params)
                self._channel_phases[ch] = "idle"
            else:
                sm = self._sms[ch]
                sm.done_timeout_sec = params["done_timeout_sec"]
                sm.waiting_timeout_sec = params["waiting_timeout_sec"]
                sm.error_display_sec = params["error_display_sec"]
                sm.busy_timeout_sec = params["busy_timeout_sec"]

    def _sm(self, channel: str) -> StateMachine:
        ch = resolve_channel(self.cfg, channel=channel)
        if ch not in self._sms:
            self._sms[ch] = StateMachine(**self._sm_params(self.cfg))
            self._channel_phases[ch] = "idle"
        return self._sms[ch]

    @property
    def sm(self) -> StateMachine:
        """Channel 1 state machine (backward compatible)."""
        return self._sm("1")

    @property
    def current_phase(self) -> str:
        return self._channel_phases.get("1", "idle")

    @current_phase.setter
    def current_phase(self, value: str) -> None:
        self._channel_phases["1"] = value

    def apply_config(self, cfg: dict) -> None:
        with self._lock:
            self.cfg = cfg
            self.commands = cfg.get("state_commands") or {}
            self.dry_run = bool(cfg.get("dry_run", False))
            self._keepalive_sec = float(cfg.get("ble_keepalive_sec", 30))
            self._init_channel_state_machines(cfg)

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

    def _apply_phase(
        self,
        channel: str,
        phase: str,
        async_send: bool = False,
    ) -> tuple[bool, str]:
        cmd = self.commands.get(phase)
        if not cmd:
            return False, f"no command for phase {phase}"
        ch = resolve_channel(self.cfg, channel=channel)
        self._channel_phases[ch] = phase
        self._sm(ch).state.last_command = cmd
        ble_cmd = prefix_ble_command(ch, cmd)
        if self.dry_run:
            return True, f"DRY_RUN {ble_cmd}"
        if async_send:
            self._enqueue_ble(ble_cmd)
            return True, f"QUEUED {ble_cmd}"
        ok, msg = self.ble.send(ble_cmd)
        self._last_ble_ok = ok
        self._last_ble_msg = msg
        return ok, msg

    def _enqueue_ble(self, command: str) -> None:
        self._ble_queue.append(command)
        self._ble_wake.set()

    def _note_ble_result(self, ok: bool, msg: str) -> None:
        with self._lock:
            self._last_ble_ok = ok
            self._last_ble_msg = msg

    def _ble_queue_loop(self) -> None:
        while True:
            self._ble_wake.wait(timeout=0.1)
            self._ble_wake.clear()
            cmds: list[str] = []
            with self._lock:
                while self._ble_queue:
                    cmds.append(self._ble_queue.pop(0))
            if not cmds:
                continue
            with self._lock:
                while self._ble_queue:
                    cmds.append(self._ble_queue.pop(0))
            if not self.dry_run:
                for cmd in cmds:
                    ok, msg = self.ble.send(cmd, timeout=2.0)
                    with self._lock:
                        self._last_ble_ok = ok
                        self._last_ble_msg = msg

    def handle_event(
        self,
        event: str,
        session_id: str | None = None,
        source: str = "hook",
        client_id: str | None = None,
        channel: str | None = None,
    ) -> dict:
        ch = resolve_channel(self.cfg, client_id=client_id, channel=channel)
        sm = self._sm(ch)
        with self._lock:
            phase, reason = sm.apply(event, session_id=session_id)
            detail_prefix = f"ch={ch}"
            if reason == "no_change":
                self._record_event(
                    event,
                    phase,
                    session_id=session_id,
                    source=source,
                    detail=f"{detail_prefix} {reason}",
                )
                return {
                    "ok": True,
                    "phase": phase,
                    "channel": ch,
                    "skipped": True,
                    "reason": reason,
                    "ble_ok": self._last_ble_ok,
                }
            ok, msg = self._apply_phase(ch, phase, async_send=True)
            self._record_event(
                event,
                phase,
                session_id=session_id,
                source=source,
                detail=f"{detail_prefix} {msg}",
            )
            return {
                "ok": ok,
                "phase": phase,
                "channel": ch,
                "event": event,
                "command": prefix_ble_command(ch, self.commands.get(phase) or ""),
                "queued": True,
                "ble_message": msg,
            }

    def send_command(
        self,
        command: str,
        device_alias: str | None = None,
        client_id: str | None = None,
        channel: str | None = None,
    ) -> dict:
        command = (command or "").strip()
        if not command:
            return {"ok": False, "error": "command required"}
        ch = resolve_channel(self.cfg, client_id=client_id, channel=channel)
        ble_cmd = prefix_ble_command(ch, command)
        with self._lock:
            if self.dry_run:
                return {
                    "ok": True,
                    "response": f"DRY_RUN {ble_cmd}",
                    "channel": ch,
                }
            target = (device_alias or "").strip() or None
            need_switch = bool(target and target != self.device_alias)
            if need_switch:
                set_default_everywhere(
                    target,
                    self.devices_config,
                    _config_path(),
                    project_root(),
                )
                self.device_alias = target
                self.cfg["default_device"] = target
        if need_switch and not self.dry_run:
            ok_sw, msg_sw = self.ble.switch_device(target)
            if not ok_sw:
                return {"ok": False, "error": msg_sw}
        ok, msg = self.ble.send(ble_cmd)
        self._note_ble_result(ok, msg)
        return {
            "ok": ok,
            "response": msg,
            "ble_message": msg,
            "channel": ch,
            "error": "" if ok else msg,
        }

    def _channel_status(self) -> dict[str, dict]:
        labels = channel_labels(self.cfg)
        out: dict[str, dict] = {}
        for ch in list_channel_ids(self.cfg):
            sm = self._sm(ch)
            data = sm.state.to_dict()
            data["phase"] = sm.resolve_phase()
            data["command"] = prefix_ble_command(
                ch, self.commands.get(data["phase"]) or ""
            )
            data["label"] = labels.get(ch, ch)
            data["active_sessions"] = sm.session_count()
            out[ch] = data
        return out

    def status(self) -> dict:
        with self._lock:
            channels = self._channel_status()
            ch1 = channels.get("1", {})
            data = dict(ch1)
            data["phase"] = ch1.get("phase", "idle")
            data["command"] = ch1.get("command")
            data["channels"] = channels
            data["client_routes"] = client_routes_payload(self.cfg)
            data["dry_run"] = self.dry_run
            data["ble"] = self.ble.status() if not self.dry_run else {}
            data["ble_ok"] = self._last_ble_ok
            data["ble_message"] = self._last_ble_msg
            data["default_device"] = self.device_alias
            data["active_sessions"] = ch1.get("active_sessions", 0)
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
        if self.dry_run:
            return {"ok": True, "devices": []}
        ok, result = self.ble.scan(timeout=timeout, show_all=show_all, reconnect=False)
        if not ok:
            self.ble.reconnect_async()
            return {"ok": False, "error": result, "devices": []}
        cfg = load_devices_config(self.devices_config)
        devices = cfg.get("devices") if isinstance(cfg.get("devices"), dict) else {}
        bound = {
            (d.get("address") or "").upper()
            for d in devices.values()
            if isinstance(d, dict)
        }
        for row in result:
            row["already_bound"] = row["address"].upper() in bound
        self.ble.reconnect_async()
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
            for sm in self._sms.values():
                sm.reset()
            for ch in self._channel_phases:
                self._channel_phases[ch] = "idle"

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
                self.ble.send("CH2 MODE ALL_OFF", timeout=2.0)

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
            for sm in self._sms.values():
                sm.reset()
            for ch in self._channel_phases:
                self._channel_phases[ch] = "idle"

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
        switch_alias = None
        with self._lock:
            was_active = alias == self.device_alias
            delete_device(self.devices_config, alias)
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
                if was_active:
                    switch_alias = new_default
            else:
                self.device_alias = None
        if switch_alias and not self.dry_run:
            self.ble.switch_device(switch_alias)
        return {"ok": True, "default_device": self.device_alias}

    def _tick_loop(self) -> None:
        while True:
            time.sleep(1.0)
            with self._lock:
                for ch in list_channel_ids(self.cfg):
                    sm = self._sm(ch)
                    new_phase = sm.tick()
                    prev = self._channel_phases.get(ch, "idle")
                    if new_phase and new_phase != prev:
                        self._apply_phase(ch, new_phase, async_send=True)

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
                        self._channel_phases.get("1", "idle"),
                        source="system",
                        detail=msg,
                    )
            if not ok:
                self.ble.reconnect()


_CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


def make_handler(daemon: Daemon):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def handle(self) -> None:
            try:
                super().handle()
            except _CLIENT_GONE:
                pass

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
                client_id = (payload.get("client_id") or "").strip() or None
                channel = (payload.get("channel") or "").strip() or None
                self._json(
                    200,
                    daemon.handle_event(
                        event,
                        session_id=session_id,
                        source=source,
                        client_id=client_id,
                        channel=channel,
                    ),
                )
                return
            if path == "/api/command":
                command = (payload.get("command") or "").strip()
                if not command:
                    self._json(400, {"error": "command required"})
                    return
                device = (payload.get("device") or "").strip() or None
                client_id = (payload.get("client_id") or "").strip() or None
                channel = (payload.get("channel") or "").strip() or None
                result = daemon.send_command(
                    command,
                    device_alias=device,
                    client_id=client_id,
                    channel=channel,
                )
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
