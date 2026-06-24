#!/usr/bin/env python3
"""
Unified Cursor hook for aiLight traffic light control.

Usage:
  python .cursor/hooks/ailight_hook.py session_start
  python .cursor/hooks/ailight_hook.py session_stop
  python .cursor/hooks/ailight_hook.py tool_start
  python .cursor/hooks/ailight_hook.py tool_failure
  python .cursor/hooks/ailight_hook.py prompt      # reads JSON from stdin
  python .cursor/hooks/ailight_hook.py test        # verify binding
"""

from __future__ import annotations

import json
import os
import re
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.light_client import load_devices_config, send_command  # noqa: E402

HOOK_CONFIG_PATH = os.path.join(PROJECT_ROOT, ".cursor", "ailight.json")


def _load_hook_config() -> dict:
    if not os.path.exists(HOOK_CONFIG_PATH):
        return {"enabled": True, "default_device": "lab-main"}
    with open(HOOK_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _devices_config_path(hook_cfg: dict) -> str:
    rel = hook_cfg.get("devices_config", "devices.json")
    return rel if os.path.isabs(rel) else os.path.join(PROJECT_ROOT, rel)


def _resolve_device_alias(hook_cfg: dict, override: str | None = None) -> str | None:
    if override:
        return override
    env_alias = os.environ.get("AILIGHT_DEVICE", "").strip()
    if env_alias:
        return env_alias
    hook_alias = (hook_cfg.get("default_device") or "").strip()
    if hook_alias:
        return hook_alias
    devices_cfg = load_devices_config(_devices_config_path(hook_cfg))
    return (devices_cfg.get("default_device") or "").strip() or None


def _allow(extra: dict | None = None) -> None:
    payload = {"permission": "allow"}
    if extra:
        payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False))


def _run_ble(command: str, device_alias: str | None = None) -> tuple[int, str, str]:
    hook_cfg = _load_hook_config()
    if not hook_cfg.get("enabled", True):
        return 0, "DISABLED", ""
    alias = _resolve_device_alias(hook_cfg, override=device_alias)
    config_path = _devices_config_path(hook_cfg)
    return send_command(command, device_alias=alias, config_path=config_path)


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
    s = text.lower()
    m = re.search(r"(?:灯控|/light)\s*([a-z0-9_-]+)", s)
    return m.group(1) if m else None


def _parse_prompt_command(text: str) -> str | None:
    s = text.lower()
    if not ("灯控" in s or "/light" in s or "ailight" in s):
        return None
    if "自动" in s or "auto" in s:
        return "MODE AUTO"
    if "黄闪" in s or "警示" in s or "flash" in s:
        return "MODE FLASH_YELLOW"
    if "全灭" in s or "all off" in s:
        return "MODE ALL_OFF"
    if "状态" in s or "status" in s:
        return "STATUS"
    if "mac" in s:
        return "MAC"
    if re.search(r"(红灯亮|red on)", s):
        return "SET RED ON"
    if re.search(r"(红灯灭|red off)", s):
        return "SET RED OFF"
    if re.search(r"(黄灯亮|yellow on)", s):
        return "SET YELLOW ON"
    if re.search(r"(黄灯灭|yellow off)", s):
        return "SET YELLOW OFF"
    if re.search(r"(绿灯亮|green on)", s):
        return "SET GREEN ON"
    if re.search(r"(绿灯灭|green off)", s):
        return "SET GREEN OFF"
    if re.search(r"(红灯闪)", s):
        return "BLINK RED 6 250"
    if re.search(r"(黄灯闪)", s):
        return "BLINK YELLOW 6 250"
    if re.search(r"(绿灯闪)", s):
        return "BLINK GREEN 6 250"
    return None


def _handle_agent_action(action: str) -> int:
    hook_cfg = _load_hook_config()
    commands = hook_cfg.get("commands") if isinstance(hook_cfg.get("commands"), dict) else {}
    command = commands.get(action)
    if not command:
        _allow()
        return 0

    alias = _resolve_device_alias(hook_cfg)
    code, out, err = _run_ble(command, device_alias=alias)
    if code == 0:
        _allow({"user_message": f"aiLight[{action}] {command} | {out}"})
    else:
        _allow({"user_message": f"aiLight[{action}] 失败: {err or out}"})
    return 0


def _handle_prompt() -> int:
    payload = _read_stdin_json()
    prompt = _extract_prompt(payload)
    command = _parse_prompt_command(prompt)
    if not command:
        _allow()
        return 0

    device_alias = _parse_device_from_prompt(prompt)
    code, out, err = _run_ble(command, device_alias=device_alias)
    if code == 0:
        _allow({"user_message": f"aiLight 灯控: {command} | {out}"})
    else:
        _allow({"user_message": f"aiLight 灯控失败: {command} | {err or out}"})
    return 0


def _handle_test() -> int:
    hook_cfg = _load_hook_config()
    alias = _resolve_device_alias(hook_cfg)
    code, out, err = _run_ble("STATUS", device_alias=alias)
    if code == 0:
        print(f"OK device={alias} | {out}")
        return 0
    print(f"FAIL device={alias} | {err or out}")
    return 1


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if action == "prompt":
        return _handle_prompt()
    if action == "test":
        return _handle_test()
    if action:
        return _handle_agent_action(action)
    _allow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
