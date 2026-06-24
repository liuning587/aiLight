"""
ESP32-C3 SuperMini traffic light controller (MicroPython).

Features:
- BLE UART command channel (Nordic UART compatible service UUID)
- Auto traffic cycle with blink definitions
- Manual single-lamp control
- Flash yellow warning mode
"""

from micropython import const
import bluetooth
import machine
import time


_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)

_FLAG_READ = const(0x0002)
_FLAG_WRITE = const(0x0008)
_FLAG_NOTIFY = const(0x0010)

_UART_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_UART_TX = (
    bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E"),
    _FLAG_READ | _FLAG_NOTIFY,
)
_UART_RX = (
    bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E"),
    _FLAG_WRITE,
)
_UART_SERVICE = (_UART_UUID, (_UART_TX, _UART_RX))


def _adv_payload(name):
    """Build a BLE advertisement payload with flags and complete local name."""
    n = name.encode("utf-8")
    payload = bytearray()
    # Flags: LE General Discoverable Mode + BR/EDR Not Supported
    payload += bytes((2, 0x01, 0x06))
    payload += bytes((len(n) + 1, 0x09)) + n
    return bytes(payload)


class BleUart:
    def __init__(self, ble, name="ESP32C3-Traffic"):
        self._ble = ble
        self._ble.active(True)
        self._ble.irq(self._irq)
        ((self._tx_handle, self._rx_handle),) = self._ble.gatts_register_services(
            (_UART_SERVICE,)
        )
        self._ble.gatts_set_buffer(self._rx_handle, 256, True)
        self._connections = set()
        self._rx_buffer = b""
        self._lines = []
        self._payload = _adv_payload(name)
        self._advertise()

    def _advertise(self):
        self._ble.gap_advertise(100_000, adv_data=self._payload)

    def _irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            conn_handle, _, _ = data
            self._connections.add(conn_handle)
        elif event == _IRQ_CENTRAL_DISCONNECT:
            conn_handle, _, _ = data
            if conn_handle in self._connections:
                self._connections.remove(conn_handle)
            self._advertise()
        elif event == _IRQ_GATTS_WRITE:
            conn_handle, value_handle = data
            if value_handle == self._rx_handle and conn_handle in self._connections:
                incoming = self._ble.gatts_read(self._rx_handle)
                self._rx_buffer += incoming
                while b"\n" in self._rx_buffer:
                    line, self._rx_buffer = self._rx_buffer.split(b"\n", 1)
                    text = line.decode("utf-8", "ignore").strip()
                    if text:
                        self._lines.append(text)

    def read_line(self):
        if self._lines:
            return self._lines.pop(0)
        return None

    def write_line(self, text):
        packet = (text + "\n").encode("utf-8")
        for conn_handle in self._connections:
            self._ble.gatts_notify(conn_handle, self._tx_handle, packet)


class TrafficLight:
    def __init__(self, red_pin=4, yellow_pin=3, green_pin=2, active_high=True):
        # Adjust pins here to match your wiring if needed.
        self.active_high = active_high
        self.red = machine.Pin(red_pin, machine.Pin.OUT)
        self.yellow = machine.Pin(yellow_pin, machine.Pin.OUT)
        self.green = machine.Pin(green_pin, machine.Pin.OUT)
        self.mode = "AUTO"
        self.phase_index = 0
        self.phase_started_ms = time.ticks_ms()
        self.blink_state = False
        self.last_blink_ms = time.ticks_ms()
        self.blink_remaining = 0
        self._manual_state = {"RED": 0, "YELLOW": 0, "GREEN": 0}
        self.set_all(0, 0, 0)
        self._auto_phases = [
            ("RED", 10_000),
            ("RED_YELLOW", 2_000),
            ("GREEN", 10_000),
            ("GREEN_BLINK", 3_000),
            ("YELLOW", 3_000),
        ]

    def _write_pin(self, pin_obj, value):
        if self.active_high:
            pin_obj.value(1 if value else 0)
        else:
            pin_obj.value(0 if value else 1)

    def set_all(self, red, yellow, green):
        self._write_pin(self.red, red)
        self._write_pin(self.yellow, yellow)
        self._write_pin(self.green, green)

    def set_named(self, color, onoff):
        if color == "RED":
            self._manual_state["RED"] = onoff
        elif color == "YELLOW":
            self._manual_state["YELLOW"] = onoff
        elif color == "GREEN":
            self._manual_state["GREEN"] = onoff
        self.set_all(
            self._manual_state["RED"],
            self._manual_state["YELLOW"],
            self._manual_state["GREEN"],
        )

    def set_mode(self, mode):
        self.mode = mode
        self.phase_index = 0
        self.phase_started_ms = time.ticks_ms()
        self.last_blink_ms = time.ticks_ms()
        self.blink_state = False
        if mode == "ALL_OFF":
            self.set_all(0, 0, 0)
        elif mode == "FLASH_YELLOW":
            self.set_all(0, 0, 0)
        elif mode == "AUTO":
            self.set_all(1, 0, 0)
        elif mode == "MANUAL":
            self.set_all(
                self._manual_state["RED"],
                self._manual_state["YELLOW"],
                self._manual_state["GREEN"],
            )

    def blink(self, color, times, period_ms):
        self.mode = "MANUAL"
        self.blink_remaining = times * 2
        self.blink_state = False
        self.last_blink_ms = time.ticks_ms()
        self._blink_color = color
        self._blink_period_ms = period_ms

    def _apply_auto_phase(self, name):
        if name == "RED":
            self.set_all(1, 0, 0)
        elif name == "RED_YELLOW":
            self.set_all(1, 1, 0)
        elif name == "GREEN":
            self.set_all(0, 0, 1)
        elif name == "GREEN_BLINK":
            self.set_all(0, 0, 1)
            self.blink_state = True
            self.last_blink_ms = time.ticks_ms()
        elif name == "YELLOW":
            self.set_all(0, 1, 0)

    def tick(self):
        now = time.ticks_ms()

        if self.mode == "AUTO":
            phase_name, phase_ms = self._auto_phases[self.phase_index]
            if phase_name == "GREEN_BLINK":
                if time.ticks_diff(now, self.last_blink_ms) >= 500:
                    self.blink_state = not self.blink_state
                    self.last_blink_ms = now
                    self.set_all(0, 0, 1 if self.blink_state else 0)

            if time.ticks_diff(now, self.phase_started_ms) >= phase_ms:
                self.phase_index = (self.phase_index + 1) % len(self._auto_phases)
                self.phase_started_ms = now
                self._apply_auto_phase(self._auto_phases[self.phase_index][0])

        elif self.mode == "FLASH_YELLOW":
            if time.ticks_diff(now, self.last_blink_ms) >= 500:
                self.last_blink_ms = now
                self.blink_state = not self.blink_state
                self.set_all(0, 1 if self.blink_state else 0, 0)

        elif self.mode == "MANUAL" and self.blink_remaining > 0:
            if time.ticks_diff(now, self.last_blink_ms) >= self._blink_period_ms:
                self.last_blink_ms = now
                self.blink_state = not self.blink_state
                self.blink_remaining -= 1
                self.set_named(self._blink_color, 1 if self.blink_state else 0)

    def status(self):
        return (
            "MODE={mode}, POLARITY={pol}, MANUAL(R={r},Y={y},G={g})".format(
                mode=self.mode,
                pol=("HIGH" if self.active_high else "LOW"),
                r=self._manual_state["RED"],
                y=self._manual_state["YELLOW"],
                g=self._manual_state["GREEN"],
            )
        )

    def set_polarity(self, active_high):
        self.active_high = active_high
        if self.mode == "MANUAL":
            self.set_all(
                self._manual_state["RED"],
                self._manual_state["YELLOW"],
                self._manual_state["GREEN"],
            )
        elif self.mode == "ALL_OFF":
            self.set_all(0, 0, 0)
        elif self.mode == "FLASH_YELLOW":
            self.set_all(0, 0, 0)
        elif self.mode == "AUTO":
            phase_name, _ = self._auto_phases[self.phase_index]
            self._apply_auto_phase(phase_name)


def _parse_int(value, default_value):
    try:
        return int(value)
    except Exception:
        return default_value


def main():
    ble = bluetooth.BLE()
    uart = BleUart(ble, name="ESP32C3-Traffic")
    light = TrafficLight(red_pin=4, yellow_pin=3, green_pin=2, active_high=True)
    light.set_mode("AUTO")
    uart.write_line("READY ESP32C3-Traffic")

    while True:
        cmd = uart.read_line()
        if cmd:
            parts = cmd.strip().upper().split()
            response = "ERR UNKNOWN"
            if not parts:
                response = "ERR EMPTY"
            elif parts[0] == "HELP":
                response = (
                    "OK CMDS: MODE <AUTO|MANUAL|FLASH_YELLOW|ALL_OFF>; "
                    "SET <RED|YELLOW|GREEN> <ON|OFF>; "
                    "BLINK <RED|YELLOW|GREEN> <TIMES> <PERIOD_MS>; STATUS"
                )
            elif parts[0] == "MODE" and len(parts) >= 2:
                mode = parts[1]
                if mode in ("AUTO", "MANUAL", "FLASH_YELLOW", "ALL_OFF"):
                    light.set_mode(mode)
                    response = "OK MODE {}".format(mode)
                else:
                    response = "ERR MODE"
            elif parts[0] == "SET" and len(parts) >= 3:
                color = parts[1]
                if color in ("RED", "YELLOW", "GREEN"):
                    onoff = 1 if parts[2] == "ON" else 0
                    light.set_mode("MANUAL")
                    light.set_named(color, onoff)
                    response = "OK SET {} {}".format(color, parts[2])
                else:
                    response = "ERR COLOR"
            elif parts[0] == "BLINK" and len(parts) >= 4:
                color = parts[1]
                times = _parse_int(parts[2], 3)
                period_ms = _parse_int(parts[3], 300)
                if color in ("RED", "YELLOW", "GREEN"):
                    light.blink(color, times, period_ms)
                    response = "OK BLINK {} {} {}".format(color, times, period_ms)
                else:
                    response = "ERR COLOR"
            elif parts[0] == "STATUS":
                response = "OK " + light.status()
            elif parts[0] == "POLARITY" and len(parts) >= 2:
                if parts[1] == "HIGH":
                    light.set_polarity(True)
                    response = "OK POLARITY HIGH"
                elif parts[1] == "LOW":
                    light.set_polarity(False)
                    response = "OK POLARITY LOW"
                else:
                    response = "ERR POLARITY"
            else:
                response = "ERR CMD"

            uart.write_line(response)

        light.tick()
        time.sleep_ms(30)


main()

