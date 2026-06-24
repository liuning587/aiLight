# ESP32-C3 SuperMini 蓝牙三色灯 + aiLight 守护进程（对标 PromLight）

用 ESP32-C3 + 三色灯模块，把 **Cursor / TRAE** Agent 的 **忙 / 思考 / 等待 / 完成 / 出错** 映射到物理灯光。架构参考 [PromLight](https://light.ghostyu.com/ai.txt)。

> **详细使用说明**（安装、配置、双机部署、故障排查等）见 **[docs/使用说明.md](docs/使用说明.md)**。

## 架构

```
IDE Hooks (Cursor / TRAE)  →  lightd (127.0.0.1:7801)  →  BLE  →  ESP32
                                  ↑
                           Web 控制台 / 扫描绑定 / 状态机
```


| 组件                      | 说明                        |
| ----------------------- | ------------------------- |
| `firmware/main.py`      | 板端 MicroPython + BLE UART |
| `tools/lightd/`         | 本地守护进程（状态聚合、长连接 BLE）      |
| `.cursor/hooks.json`    | Cursor 项目 Hook            |
| `.trae/hooks.json`      | TRAE IDE 项目 Hook          |
| `tools/ailight_hook.py` | 双端共用 Hook 逻辑              |
| `config.json`           | 守护进程配置（端口、超时、灯效命令）        |
| `devices.json`          | 多设备 MAC/名称绑定              |


## 一键安装（Windows）

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

安装后会：

- 安装 Python 依赖
- 后台启动 `lightd`
- 打开控制台 [http://127.0.0.1:7801](http://127.0.0.1:7801) （使用说明 [http://127.0.0.1:7801/docs](http://127.0.0.1:7801/docs) ）

加 `-Autostart` 可注册登录自启：`install.ps1 -Autostart`。无 Python 环境可打包 exe：`scripts\build-lightd.ps1`。

**首次使用：** 在控制台点击 **「扫描附近设备」** → **「绑定并使用」**，无需手改 `devices.json`。

**然后请：**

1. 烧录固件：`powershell -File scripts\flash-firmware.ps1`（或 `mpremote`）
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


| 状态  | 灯效                 | 触发                    |
| --- | ------------------ | --------------------- |
| 空闲  | 全灭                 | `sessionStart` / 超时回落 |
| 思考  | 黄慢闪 `THINK_SLOW`   | 你发消息                  |
| 忙碌  | 黄快闪 `FLASH_YELLOW` | Agent 调工具             |
| 等待  | 红灯常亮               | Shell/MCP 执行前（授权）     |
| 完成  | 绿灯常亮               | Agent 结束（60s 后自动灭）    |
| 出错  | 红灯闪                | 工具失败                  |


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

**双灯位（单板）：** 灯位 A：GPIO4/3/2（R/Y/G）。灯位 B：**红 GPIO7、黄 GPIO6、绿 GPIO5**（与模块丝印 5/6/7 对调了红绿），**GPIO8 常态低电平**（可作第二组 GND）。两组仍建议共板载 GND。`CH2 MODE ...` 控制第二组。

默认高电平点亮（`active_high=True`）。

## 配置

`config.json`（**lightd 守护进程**，跑在接灯/有蓝牙的电脑上）：

```json
{
  "web_host": "127.0.0.1",
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


| 字段                  | 说明                                                            |
| ------------------- | ------------------------------------------------------------- |
| `web_host`          | lightd 监听地址。默认 `127.0.0.1`（仅本机）。局域网远程访问改为 `0.0.0.0`           |
| `web_port`          | HTTP 端口，默认 `7801`                                             |
| `api_token`         | API 鉴权令牌（空=不启用）；双机部署时 Hook 侧 `.cursor/ailight.json` 需同步       |
| `ble_keepalive_sec` | BLE 保活探测间隔（秒）                                                 |
| `busy_timeout_sec`  | 忙碌状态超时回落（秒），防 Hook 丢失导致一直黄闪                                   |
| `client_routes`     | `client_id` → 灯位（`"1"`/`"2"`），如 `{"slot-a":"1","slot-b":"2"}` |
| `channels`          | 灯位标签，默认 CH1=GPIO2/3/4、CH2=R7/Y6/G5+GPIO8(L)                    |


`.cursor/ailight.json` 或 `.trae/ailight.json`（**IDE Hook**，跑在写代码的电脑上）：

```json
{
  "daemon_host": "127.0.0.1",
  "daemon_port": 7801
}
```


| 字段            | 说明                                                       |
| ------------- | -------------------------------------------------------- |
| `daemon_host` | lightd 所在机器的 IP。本机部署用 `127.0.0.1`；双机部署填 lightd 那台的局域网 IP |
| `daemon_port` | 与 `config.json` 的 `web_port` 一致                          |


`devices.json`：在 **lightd 所在电脑** 维护，Web 控制台绑定后会自动写入。

## 双机部署（Cursor 与 lightd 分机）

适合：**A 电脑接灯 + 蓝牙**，**B 电脑写代码 + Cursor**。

```
电脑 B (Cursor Hook)  --HTTP-->  电脑 A (lightd + BLE)  -->  ESP32
```

**电脑 A（灯旁，例如 192.168.1.100）**

1. 安装并启动 lightd，`config.json` 中：
  ```json
   { "web_host": "0.0.0.0", "web_port": 7801 }
  ```
2. Windows 防火墙放行入站 **TCP 7801**
3. 浏览器打开 `http://192.168.1.100:7801` → 扫描绑定设备

**电脑 B（Cursor / TRAE）**

1. 克隆同一项目（或只保留 `.cursor/` + `tools/ailight_hook.py` 依赖）
2. `.cursor/ailight.json`：
  ```json
   {
     "daemon_host": "192.168.1.100",
     "daemon_port": 7801,
     "api_token": "与 lightd 侧 config.json 相同"
   }
  ```
3. 重启 IDE；Hook 只发 HTTP，**不需要本机蓝牙**

> 安全提示：`web_host: 0.0.0.0` 时建议设置 `api_token`（`config.json`），并在 Hook 侧同步。仍建议仅在内网使用；公网请加 TLS 反代。

## 常见问题

- **Hook 不亮**：先确认 lightd 地址能打开（本机 `http://127.0.0.1:7801` 或远程 `http://<lightd的IP>:7801`）；检查 `.cursor/ailight.json` 的 `daemon_host`
- **双机不亮**：lightd 侧 `web_host` 是否为 `0.0.0.0`；防火墙是否放行 7801
- **TRAE 不亮**：确认 Hooks 为「本地自动运行」；查看 设置 → Hooks → 运行日志
- **BLE 连不上**：不要占用串口；`mpremote reset` 后重试
- **空闲红绿同亮**：升级最新固件（`SET` 互斥 + `ALL_OFF` 清状态）

## 与 PromLight 仍有的差异

- 无成品硬件/按键/续航管理
- 仅 Cursor / TRAE 项目 Hook（未自动配置 Claude Code 等，但 TRAE 可导入 Claude Hook）
- 灯效为三色闪/常亮（无跑马灯）
- 需自行烧录 MicroPython 固件

