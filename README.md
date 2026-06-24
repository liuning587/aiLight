# ESP32-C3 SuperMini 蓝牙三色灯控制（MicroPython + Cursor Hook）

这个工程让你在 Cursor 里通过自然语言触发 hook，再经 BLE 控制 `ESP32-C3 SuperMini` 上的红黄绿灯模块。

## 1. 硬件接线（按图里的模块）

你这类 4 线交通灯模块一般是 `R / Y / G / GND`（板上丝印顺序为准），建议接法：

- `GND` -> `ESP32-C3 GND`
- `R` -> `GPIO4`
- `Y` -> `GPIO3`
- `G` -> `GPIO2`

> 当前工程默认按高电平点亮（`active_high=True`）。

## 2. 烧录与运行 MicroPython 固件

先给板子烧好 MicroPython，然后把固件上传为 `main.py`：

```bash
mpremote connect COMx fs cp firmware/main.py :main.py
mpremote connect COMx reset
```

板子启动后会广播 BLE 名称：`aiLight-XXXX`（`XXXX` 为 MAC 后两字节，方便多板区分）。

## 3. 电脑端依赖安装

```bash
pip install -r requirements.txt
```

## 4. 先手动验证 BLE 控制

```bash
python tools/ble_lightctl.py --cmd "STATUS"
python tools/ble_lightctl.py --cmd "MAC"
python tools/ble_lightctl.py --cmd "MODE AUTO"
python tools/ble_lightctl.py --cmd "MODE FLASH_YELLOW"
python tools/ble_lightctl.py --cmd "SET RED ON"
python tools/ble_lightctl.py --cmd "BLINK GREEN 6 250"
```

也可以先扫描附近设备确认 MAC：

```bash
python tools/ble_lightctl.py --scan
python tools/ble_lightctl.py --scan --scan-all
```

## 4.1 多设备绑定（devices.json）

工程根目录新增 `devices.json`，每台电脑都可以维护自己的映射。脚本支持按设备别名控制：

```bash
python tools/ble_lightctl.py --list-devices
python tools/ble_lightctl.py --device lab-main --cmd "STATUS"
python tools/ble_lightctl.py --device lab-a --cmd "MODE AUTO"
python tools/ble_lightctl.py --device lab-b --cmd "MODE FLASH_YELLOW"
```

`devices.json` 示例结构：

```json
{
  "default_timeout": 8.0,
  "default_device": "lab-main",
  "devices": {
    "lab-main": { "name": "aiLight", "name_prefix": "aiLight", "address": "", "timeout": 8.0 },
    "lab-a": { "name": "TL-A", "address": "", "timeout": 8.0 },
    "lab-b": { "name": "TL-B", "address": "", "timeout": 8.0 }
  }
}
```

说明：

- 优先建议填 `address`（MAC）做硬绑定，避免同名设备串控。
- 不填 `address` 时，按 `name` 扫描连接。
- 不传 `--device` 时默认使用 `default_device`。

## 5. Cursor Hook 与 aiLight 关联

本项目通过 **项目级 Hook** 把 Cursor Agent 和你的红绿灯绑定在一起。

### 配置文件

| 文件 | 作用 |
|------|------|
| `.cursor/hooks.json` | Hook 事件注册 |
| `.cursor/hooks/ailight_hook.py` | 统一灯控逻辑 |
| `.cursor/ailight.json` | Hook 行为配置（默认设备、各阶段命令） |
| `devices.json` | BLE 设备绑定（MAC / 名称） |

默认已绑定 `lab-main` → `aiLight-BD92`（`88:56:A6:60:BD:92`）。

### 验证 Hook 绑定

```bash
python .cursor/hooks/ailight_hook.py test
```

### Agent 自动点灯

| Hook 事件 | 灯态 |
|-----------|------|
| `sessionStart` | 黄闪（开始会话） |
| `preToolUse`（Shell/Write/Task） | 黄闪（正在干活） |
| `stop` | 自动模式（本轮结束） |
| `postToolUseFailure` | 红灯闪（工具失败） |

### 手动灯控（聊天里发）

- `灯控：切换自动模式`
- `灯控：黄灯闪`
- `灯控：红灯亮`
- `灯控：查询状态`

也可指定设备：`灯控 lab-a：黄灯闪`

### 启用步骤

1. 板子 USB 上电，确保 `main.py` 在跑（蓝牙名 `aiLight-XXXX`）
2. **重启 Cursor**（修改 `hooks.json` 后必须重启）
3. 运行 `python .cursor/hooks/ailight_hook.py test` 确认连通

可选：固定设备别名

```bash
set AILIGHT_DEVICE=lab-main
```

## 6. 内置交通灯定义（固件里已实现）

`MODE AUTO` 相位：

1. 红灯常亮 10s
2. 红+黄 2s
3. 绿灯常亮 10s
4. 绿灯闪烁 3s（500ms 周期）
5. 黄灯常亮 3s

循环执行。

`MODE FLASH_YELLOW`：

- 黄灯持续闪烁（500ms 周期），常用于警示/故障模式。

## 7. 支持的 BLE 指令

- `MODE AUTO|MANUAL|FLASH_YELLOW|ALL_OFF`
- `SET RED|YELLOW|GREEN ON|OFF`
- `BLINK RED|YELLOW|GREEN <times> <period_ms>`
- `STATUS`
- `MAC`
- `HELP`

## 8. 常见问题

- 搜不到设备：先确认电脑蓝牙打开，且板子已上电并运行 `main.py`。
- 连接失败：先重启板子再试；必要时用 `--address` 指定 BLE 地址。
- 灯不亮：先检查模块是高电平点亮还是低电平点亮，再调整 `active_high`。
