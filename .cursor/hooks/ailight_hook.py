#!/usr/bin/env python3
"""
Cursor hook -> aiLight daemon (lightd).

Forwards lifecycle events to http://127.0.0.1:7801/api/event
Falls back to direct BLE if daemon is unavailable.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.light_client import send_command  # noqa: E402

HOOK_CONFIG_PATH = os.path.join(PROJECT_ROOT, ".cursor", "ailight.json")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

# Maps hook argv[1] -> daemon event name
ACTION_EVENTS = {
    "session_start": "session_start",
    "session_stop": "session_stop",
    "user_prompt": "user_prompt",
    "tool_start": "tool_start",
    "tool_success": "tool_success",
    "permission_wait": "permission_wait",
    "permission_done": "permission_done",
    "tool_failure": "tool_failure",
    "force_idle": "force_idle",
}


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _daemon_url() -> str:
    hook_cfg = _load_json(HOOK_CONFIG_PATH)
    cfg = _load_json(CONFIG_PATH)
    port = int(hook_cfg.get("daemon_port") or cfg.get("web_port") or 7801)
    host = hook_cfg.get("daemon_host") or "127.0.0.1"
    return f"http://{host}:{port}/api/event"


def _allow(extra: dict | None = None, notify: bool = False) -> None:
    payload = {"permission": "allow"}
    if extra and notify:
        payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False))


def _post_daemon(event: str) -> tuple[bool, str]:
    body = json.dumps({"event": event}).encode("utf-8")
    req = urllib.request.Request(
        _daemon_url(),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            return True, data.get("ble_message") or data.get("phase") or "ok"
        return False, data.get("ble_message") or "daemon error"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as ex:
        return False, str(ex)


def _fallback_ble(event: str) -> tuple[bool, str]:
    hook_cfg = _load_json(HOOK_CONFIG_PATH)
    cfg = _load_json(CONFIG_PATH)
    commands = cfg.get("state_commands") or hook_cfg.get("commands") or {}
    phase_map = {
        "session_start": "idle",
        "user_prompt": "thinking",
        "tool_start": "busy",
        "tool_success": "thinking",
        "permission_wait": "waiting",
        "permission_done": "thinking",
        "session_stop": "done",
        "tool_failure": "error",
        "force_idle": "idle",
    }
    phase = phase_map.get(event)
    if not phase or event in ("tool_success", "permission_done"):
        return True, "skip fallback"
    cmd = commands.get(phase)
    if not cmd:
        return False, f"no fallback command for {phase}"
    alias = os.environ.get("AILIGHT_DEVICE") or hook_cfg.get("default_device")
    code, out, err = send_command(cmd, device_alias=alias)
    return code == 0, out or err


def _dispatch(event: str) -> int:
    ok, msg = _post_daemon(event)
    if not ok:
        ok2, msg2 = _fallback_ble(event)
        if not ok2 and event not in ("tool_success", "permission_done"):
            _allow(
                {"user_message": f"aiLight 离线: {msg} | fallback: {msg2}"}, notify=True
            )
            return 0
    _allow()
    return 0


def _read_stdin_json() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _extract_prompt(payload: dict) -> str:
    for key in ("prompt", "text", "input", "userPrompt", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    messages = payload.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
    return ""


def _parse_device_from_prompt(text: str) -> str | None:
    m = re.search(r"(?:灯控|/light)\s*([a-z0-9_-]+)", text.lower())
    return m.group(1) if m else None


def _parse_manual_command(text: str) -> str | None:
    s = text.lower()
    if not ("灯控" in s or "/light" in s or "ailight" in s):
        return None
    mapping = {
        "auto": "MODE AUTO",
        "自动": "MODE AUTO",
        "flash": "MODE FLASH_YELLOW",
        "黄闪": "MODE FLASH_YELLOW",
        "all off": "MODE ALL_OFF",
        "全灭": "MODE ALL_OFF",
        "status": "STATUS",
        "状态": "STATUS",
        "mac": "MAC",
    }
    for k, v in mapping.items():
        if k in s:
            return v
    for pat, cmd in [
        (r"(红灯亮|red on)", "SET RED ON"),
        (r"(红灯灭|red off)", "SET RED OFF"),
        (r"(黄灯亮|yellow on)", "SET YELLOW ON"),
        (r"(黄灯灭|yellow off)", "SET YELLOW OFF"),
        (r"(绿灯亮|green on)", "SET GREEN ON"),
        (r"(绿灯灭|green off)", "SET GREEN OFF"),
        (r"红灯闪", "BLINK RED 6 250"),
        (r"黄灯闪", "BLINK YELLOW 6 250"),
        (r"绿灯闪", "BLINK GREEN 6 250"),
    ]:
        if re.search(pat, s):
            return cmd
    return None


def _handle_prompt() -> int:
    payload = _read_stdin_json()
    prompt = _extract_prompt(payload)
    manual = _parse_manual_command(prompt)
    if manual:
        alias = _parse_device_from_prompt(prompt)
        code, out, err = send_command(manual, device_alias=alias)
        if code == 0:
            _allow({"user_message": f"aiLight: {manual} | {out}"}, notify=True)
        else:
            _allow({"user_message": f"aiLight 失败: {err or out}"}, notify=True)
        return 0
    return _dispatch("user_prompt")


def _handle_test() -> int:
    ok, msg = _post_daemon("force_idle")
    if ok:
        print(f"OK daemon | {msg}")
        return 0
    print(f"FAIL daemon | {msg}")
    print("Tip: start daemon with: python -m tools.lightd")
    return 1


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if action == "prompt":
        return _handle_prompt()
    if action == "test":
        return _handle_test()
    event = ACTION_EVENTS.get(action)
    if event:
        return _dispatch(event)
    _allow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
