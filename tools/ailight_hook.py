#!/usr/bin/env python3
"""
aiLight hook -> lightd daemon (Cursor + TRAE IDE).

Forwards lifecycle events to lightd HTTP API (host/port from .cursor/ailight.json or config.json).
Falls back to direct BLE if daemon is unavailable.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.light_client import send_command  # noqa: E402

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

TRAE_HOOK_EVENTS = frozenset(
    {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "Notification",
    }
)

# argv[1] -> daemon event name
ACTION_EVENTS = {
    "session_start": "session_start",
    "session_stop": "session_stop",
    "user_prompt": "user_prompt",
    "prompt": "user_prompt",
    "tool_start": "tool_start",
    "tool_success": "tool_success",
    "post_tool": "tool_success",
    "permission_wait": "permission_wait",
    "permission_done": "permission_done",
    "tool_failure": "tool_failure",
    "notification": "permission_wait",
    "force_idle": "force_idle",
}

# argv action -> TRAE hook_event_name for stdout format
ACTION_TRAE_EVENT = {
    "session_start": "SessionStart",
    "session_stop": "Stop",
    "prompt": "UserPromptSubmit",
    "tool_start": "PreToolUse",
    "tool_success": "PostToolUse",
    "post_tool": "PostToolUse",
    "tool_failure": "PostToolUse",
    "notification": "Notification",
    "permission_wait": "PreToolUse",
}


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _hook_config_path() -> str:
    for rel in (".trae/ailight.json", ".cursor/ailight.json"):
        path = os.path.join(PROJECT_ROOT, rel)
        if os.path.exists(path):
            return path
    if os.environ.get("TRAE_PROJECT_DIR"):
        return os.path.join(PROJECT_ROOT, ".trae", "ailight.json")
    return os.path.join(PROJECT_ROOT, ".cursor", "ailight.json")


def _detect_ide(stdin_payload: dict | None = None) -> str:
    if os.environ.get("TRAE_PROJECT_DIR"):
        return "trae"
    if stdin_payload:
        event = stdin_payload.get("hook_event_name", "")
        if event in TRAE_HOOK_EVENTS:
            return "trae"
    return "cursor"


def _daemon_base_url() -> str:
    hook_cfg = _load_json(_hook_config_path())
    cfg = _load_json(CONFIG_PATH)
    port = int(hook_cfg.get("daemon_port") or cfg.get("web_port") or 7801)
    host = hook_cfg.get("daemon_host") or "127.0.0.1"
    return f"http://{host}:{port}"


def _daemon_url() -> str:
    return f"{_daemon_base_url()}/api/event"


def _daemon_command_url() -> str:
    return f"{_daemon_base_url()}/api/command"


def _is_enabled() -> bool:
    return bool(_load_json(_hook_config_path()).get("enabled", True))


def _api_token() -> str:
    hook_cfg = _load_json(_hook_config_path())
    cfg = _load_json(CONFIG_PATH)
    return str(hook_cfg.get("api_token") or cfg.get("api_token") or "").strip()


def _daemon_headers(content_type: bool = True) -> dict[str, str]:
    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = "application/json"
    token = _api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _emit_response(
    ide: str,
    trae_event: str | None = None,
    extra: dict | None = None,
    notify: bool = False,
) -> None:
    if ide == "trae":
        if trae_event == "PreToolUse":
            body: dict = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }
            if extra and notify and extra.get("user_message"):
                body["hookSpecificOutput"]["permissionDecisionReason"] = extra[
                    "user_message"
                ]
            print(json.dumps(body, ensure_ascii=False))
        elif trae_event == "UserPromptSubmit":
            if extra and notify and extra.get("user_message"):
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "UserPromptSubmit",
                                "additionalContext": extra["user_message"],
                            }
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                print("{}")
        else:
            # SessionStart / PostToolUse / Stop / Notification: never block agent
            print("{}")
        return

    payload = {"permission": "allow"}
    if extra and notify:
        payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False))


def _post_daemon(event: str, session_id: str | None = None) -> tuple[bool, str]:
    body_obj: dict = {"event": event}
    if session_id:
        body_obj["session_id"] = session_id
    body = json.dumps(body_obj).encode("utf-8")
    req = urllib.request.Request(
        _daemon_url(),
        data=body,
        headers=_daemon_headers(),
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


def _post_daemon_command(
    command: str, device_alias: str | None = None
) -> tuple[bool, str]:
    body_obj: dict = {"command": command}
    if device_alias:
        body_obj["device"] = device_alias
    body = json.dumps(body_obj).encode("utf-8")
    req = urllib.request.Request(
        _daemon_command_url(),
        data=body,
        headers=_daemon_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            return True, data.get("response") or data.get("ble_message") or "ok"
        return False, data.get("error") or data.get("ble_message") or "command failed"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as ex:
        return False, str(ex)


def _fallback_ble(event: str) -> tuple[bool, str]:
    hook_cfg = _load_json(_hook_config_path())
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


def _dispatch(
    event: str,
    ide: str = "cursor",
    trae_event: str | None = None,
    session_id: str | None = None,
) -> int:
    ok, msg = _post_daemon(event, session_id=session_id)
    if not ok:
        ok2, msg2 = _fallback_ble(event)
        if not ok2 and event not in ("tool_success", "permission_done"):
            _emit_response(
                ide,
                trae_event,
                {"user_message": f"aiLight 离线: {msg} | fallback: {msg2}"},
                notify=True,
            )
            return 0
    _emit_response(ide, trae_event)
    return 0


def _read_stdin_json() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _extract_session_id(payload: dict) -> str | None:
    for key in (
        "session_id",
        "conversation_id",
        "composerId",
        "composer_id",
        "chat_id",
        "thread_id",
    ):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


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


def _tool_response_failed(payload: dict) -> bool:
    resp = payload.get("tool_response")
    if resp is None:
        return False
    if isinstance(resp, dict):
        if resp.get("error") or resp.get("is_error") or resp.get("success") is False:
            return True
        status = str(resp.get("status", "")).lower()
        if status in ("error", "failed", "failure"):
            return True
        for key in ("exit_code", "exitCode", "return_code"):
            code = resp.get(key)
            if code is not None:
                try:
                    if int(code) != 0:
                        return True
                except (TypeError, ValueError):
                    pass
    elif isinstance(resp, str):
        low = resp.lower()
        if "error" in low or "failed" in low or "exception" in low:
            return True
    return False


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


def _handle_prompt(payload: dict) -> int:
    ide = _detect_ide(payload)
    trae_event = payload.get("hook_event_name") or "UserPromptSubmit"
    session_id = _extract_session_id(payload)
    prompt = _extract_prompt(payload)
    manual = _parse_manual_command(prompt)
    if manual:
        alias = _parse_device_from_prompt(prompt)
        ok, out = _post_daemon_command(manual, device_alias=alias)
        if not ok:
            code, out_ble, err = send_command(manual, device_alias=alias)
            if code == 0:
                ok, out = True, out_ble
            else:
                out = err or out_ble or out
        if ok:
            _emit_response(
                ide,
                trae_event,
                {"user_message": f"aiLight: {manual} | {out}"},
                notify=True,
            )
        else:
            _emit_response(
                ide,
                trae_event,
                {"user_message": f"aiLight 失败: {out}"},
                notify=True,
            )
        return 0
    return _dispatch(
        "user_prompt", ide=ide, trae_event=trae_event, session_id=session_id
    )


def _handle_post_tool(payload: dict) -> int:
    ide = _detect_ide(payload)
    trae_event = payload.get("hook_event_name") or "PostToolUse"
    session_id = _extract_session_id(payload)
    if ide == "trae":
        _dispatch(
            "permission_done",
            ide=ide,
            trae_event=trae_event,
            session_id=session_id,
        )
    if _tool_response_failed(payload):
        return _dispatch(
            "tool_failure", ide=ide, trae_event=trae_event, session_id=session_id
        )
    return _dispatch(
        "tool_success", ide=ide, trae_event=trae_event, session_id=session_id
    )


def _handle_notification(payload: dict) -> int:
    ide = _detect_ide(payload)
    session_id = _extract_session_id(payload)
    ntype = (payload.get("notification_type") or "").strip()
    if ntype == "permission_prompt":
        _dispatch(
            "permission_wait",
            ide=ide,
            trae_event="Notification",
            session_id=session_id,
        )
    else:
        _emit_response(ide, "Notification")
    return 0


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
    if action == "test":
        return _handle_test()
    if not _is_enabled():
        _emit_response(_detect_ide())
        return 0

    payload = _read_stdin_json()
    session_id = _extract_session_id(payload)

    if action == "prompt":
        return _handle_prompt(payload)
    if action == "post_tool":
        return _handle_post_tool(payload)
    if action == "notification":
        return _handle_notification(payload)

    event = ACTION_EVENTS.get(action)
    if event:
        ide = _detect_ide(payload)
        trae_event = ACTION_TRAE_EVENT.get(action)
        return _dispatch(event, ide=ide, trae_event=trae_event, session_id=session_id)

    _emit_response(_detect_ide())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
