# ESP32-C3 SuperMini 蓝牙三色灯 + aiLight 守护进程（对标 PromLight）

用 ESP32-C3 + 三色灯模块，把 **Cursor / TRAE** Agent 的 **忙 / 思考 / 等待 / 完成 / 出错** 映射到物理灯光。架构参考 [PromLight](https://light.ghostyu.com/ai.txt)。

## 架构

```
IDE Hooks (Cursor / TRAE)  →  lightd (127.0.0.1:7801)  →  BLE  →  ESP32
                                  ↑
                           Web 控制台 / 扫描绑定 / 状态机
```

| 组件 | 说明 |
|------|------|
| `firmware/main.py` | 板端 MicroPython + BLE UART |
| `tools/lightd/` | 本地守护进程（状态聚合、长连接 BLE） |
| `.cursor/hooks.json` | Cursor 项目 Hook |
| `.trae/hooks.json` | TRAE IDE 项目 Hook |
| `tools/ailight_hook.py` | 双端共用 Hook 逻辑 |
| `config.json` | 守护进程配置（端口、超时、灯效命令） |
| `devices.json` | 多设备 MAC/名称绑定 |

## 一键安装（Windows）

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

安装后会：
- 安装 Python 依赖
- 后台启动 `lightd`
- 打开控制台 http://127.0.0.1:7801

**首次使用：** 在控制台点击 **「扫描附近设备」** → **「绑定并使用」**，无需手改 `devices.json`。

**然后请：**
1. 上传固件：`mpremote connect COMx fs cp firmware/main.py :main.py`
2. 板子上电（BLE 名 `aiLight-XXXX`）
3. **重启 IDE**（Cursor 或 TRAE），新开 Agent 对话

## TRAE IDE 用户

1. 运行 `install.ps1`，在 Web 控制台 **扫描 → 绑定并使用**
2. 打开 TRAE：**设置 → Hooks → 项目**，确认 `.trae/hooks.json` 已启用
3. Hook 运行方式选 **「本地自动运行」**（需访问本机 BLE 和 lightd）
4. 重启 TRAE，新开对话

Hook 配置已内置，事件映射见 `.trae/hooks.json`（`SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `Stop` / `Notification`）。

## Cursor 用户

Hook 配置见 `.cursor/hooks.json`，重启 Cursor 后生效。

## 状态对照 PromLight

| 状态 | 灯效 | 触发 |
|------|------|------|
| 空闲 | 全灭 | `sessionStart` / 超时回落 |
| 思考 | 黄慢闪 `THINK_SLOW` | 你发消息 |
| 忙碌 | 黄快闪 `FLASH_YELLOW` | Agent 调工具 |
| 等待 | 红灯常亮 | Shell/MCP 执行前（授权） |
| 完成 | 绿灯常亮 | Agent 结束（60s 后自动灭） |
| 出错 | 红灯闪 | 工具失败 |

## 手动命令

```bash
# 启动守护进程
python -m tools.lightd

# 测试 Hook / 守护进程
python tools/ailight_hook.py test
# 或
python .cursor/hooks/ailight_hook.py test

# 直接 BLE 控制
python tools/ble_lightctl.py --device lab-main --cmd "STATUS"
python tools/ble_lightctl.py --scan

# 聊天手动灯控
# 灯控：黄灯闪 / 灯控：切换自动模式
```

## 硬件接线

- `GND` → GND
- `R` → GPIO4
- `Y` → GPIO3
- `G` → GPIO2

默认高电平点亮（`active_high=True`）。

## 配置

`config.json`：

```json
{
  "web_port": 7801,
  "done_timeout_sec": 60,
  "waiting_timeout_sec": 300,
  "state_commands": {
    "idle": "MODE ALL_OFF",
    "thinking": "MODE THINK_SLOW",
    "busy": "MODE FLASH_YELLOW",
    "waiting": "MODE WAIT",
    "done": "MODE DONE",
    "error": "BLINK RED 6 250"
  }
}
```

`devices.json`：每台电脑维护自己的设备表，**建议填写 MAC**。

## 常见问题

- **Hook 不亮**：先确认 `http://127.0.0.1:7801` 能打开；重启 IDE（Cursor / TRAE）
- **TRAE 不亮**：确认 Hooks 为「本地自动运行」；查看 设置 → Hooks → 运行日志
- **BLE 连不上**：不要占用串口；`mpremote reset` 后重试
- **空闲红绿同亮**：升级最新固件（`SET` 互斥 + `ALL_OFF` 清状态）

## 与 PromLight 仍有的差异

- 无成品硬件/按键/续航管理
- 仅 Cursor / TRAE 项目 Hook（未自动配置 Claude Code 等，但 TRAE 可导入 Claude Hook）
- 灯效为三色闪/常亮（无跑马灯）
- 需自行烧录 MicroPython 固件
