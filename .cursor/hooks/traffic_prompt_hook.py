#!/usr/bin/env python3
"""
Cursor hook: detect traffic-light intent from prompt and relay command via BLE.
"""

import json
import os
import re
import subprocess
import sys


def _read_input():
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _extract_prompt(payload):
    # Different Cursor versions may pass slightly different keys.
    for key in ("prompt", "text", "input", "userPrompt", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if isinstance(payload.get("messages"), list):
        for msg in reversed(payload["messages"]):
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
    return ""


def _parse_nl_to_cmd(text):
    s = text.lower()
    # Explicit trigger keeps false positives low.
    if not ("灯控" in s or "/light" in s or "traffic light" in s):
        return None

    if "自动" in s or "auto" in s:
        return "MODE AUTO"
    if "黄闪" in s or "警示" in s or "flash" in s:
        return "MODE FLASH_YELLOW"
    if "全灭" in s or "all off" in s:
        return "MODE ALL_OFF"
    if "状态" in s or "status" in s:
        return "STATUS"

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


def _run_ble_cmd(command):
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    script = os.path.join(project_root, "tools", "ble_lightctl.py")
    proc = subprocess.run(
        [sys.executable, script, "--cmd", command],
        capture_output=True,
        text=True,
        timeout=15,
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return proc.returncode, out, err


def main():
    payload = _read_input()
    prompt = _extract_prompt(payload)
    command = _parse_nl_to_cmd(prompt)

    if not command:
        print(json.dumps({"permission": "allow"}))
        return 0

    try:
        code, out, err = _run_ble_cmd(command)
        if code == 0:
            msg = "已发送灯控命令: {}{}".format(command, (" | " + out) if out else "")
            print(json.dumps({"permission": "allow", "user_message": msg}))
        else:
            msg = "灯控命令发送失败: {} {}".format(command, err or out or "unknown error")
            print(json.dumps({"permission": "allow", "user_message": msg}))
    except Exception as ex:
        print(
            json.dumps(
                {"permission": "allow", "user_message": "灯控 hook 异常: {}".format(str(ex))}
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

