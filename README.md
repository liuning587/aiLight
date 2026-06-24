# ESP32-C3 SuperMini 蓝牙三色灯控制（MicroPython + Cursor Hook）

这个工程让你在 Cursor 里通过自然语言触发 hook，再经 BLE 控制 `ESP32-C3 SuperMini` 上的红黄绿灯模块。

## 1. 硬件接线（按图里的模块）

你这类 4 线交通灯模块一般是 `R / Y / G / GND`（板上丝印顺序为准），建议接法：

- `GND` -> `ESP32-C3 GND`
- `R` -> `GPIO2`
- `Y` -> `GPIO3`
- `G` -> `GPIO4`

> 如果你的模块是低电平点亮，把 `firmware/main.py` 里的 `active_high=True` 改成 `False`。

## 2. 烧录与运行 MicroPython 固件

先给板子烧好 MicroPython，然后把固件上传为 `main.py`：

```bash
mpremote connect COMx fs cp firmware/main.py :main.py
mpremote connect COMx reset
```

板子启动后会广播 BLE 名称：`ESP32C3-Traffic`。

## 3. 电脑端依赖安装

```bash
pip install -r requirements.txt
```

## 4. 先手动验证 BLE 控制

```bash
python tools/ble_lightctl.py --cmd "STATUS"
python tools/ble_lightctl.py --cmd "MODE AUTO"
python tools/ble_lightctl.py --cmd "MODE FLASH_YELLOW"
python tools/ble_lightctl.py --cmd "SET RED ON"
python tools/ble_lightctl.py --cmd "BLINK GREEN 6 250"
```

## 5. Cursor Hook 自动控制

项目里已经配置：

- `.cursor/hooks.json`
- `.cursor/hooks/traffic_prompt_hook.py`

当你在聊天里输入带“灯控”关键词的句子时，hook 会自动解析并发送 BLE 指令。

示例（在 Cursor 聊天框输入）：

- `灯控：切换自动模式`
- `灯控：黄灯闪`
- `灯控：红灯亮`
- `灯控：查询状态`

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
- `HELP`

## 8. 常见问题

- 搜不到设备：先确认电脑蓝牙打开，且板子已上电并运行 `main.py`。
- 连接失败：先重启板子再试；必要时用 `--address` 指定 BLE 地址。
- 灯不亮：先检查模块是高电平点亮还是低电平点亮，再调整 `active_high`。

