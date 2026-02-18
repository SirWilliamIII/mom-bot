import os
import time
import threading
import spidev

try:
    from gpiozero import PWMOutputDevice, OutputDevice, Device
    from gpiozero.pins.lgpio import LGPIOFactory
    _GPIO_AVAILABLE = True
except ImportError:
    _GPIO_AVAILABLE = False

try:
    import lgpio
    _LGPIO_AVAILABLE = True
except ImportError:
    _LGPIO_AVAILABLE = False


def _detect_gpio_chip():
    if os.path.exists("/dev/gpiochip4"):
        return 4
    return 0

BOARD_TO_BCM = {
    3: 2, 5: 3, 7: 4, 8: 14, 10: 15, 11: 17, 12: 18,
    13: 27, 15: 22, 16: 23, 18: 24, 19: 10, 21: 9,
    22: 25, 23: 11, 24: 8, 26: 7, 29: 5, 31: 6,
    32: 12, 33: 13, 35: 19, 36: 16, 37: 26, 38: 20, 40: 21,
}


class WhisplayBoard:
    LCD_WIDTH = 240
    LCD_HEIGHT = 280
    CornerHeight = 20

    DC_PIN_BOARD = 13
    RST_PIN_BOARD = 7
    LED_PIN_BOARD = 15
    RED_PIN_BOARD = 22
    GREEN_PIN_BOARD = 18
    BLUE_PIN_BOARD = 16
    BUTTON_PIN_BOARD = 11

    def __init__(self):
        dc_bcm = BOARD_TO_BCM[self.DC_PIN_BOARD]
        rst_bcm = BOARD_TO_BCM[self.RST_PIN_BOARD]
        led_bcm = BOARD_TO_BCM[self.LED_PIN_BOARD]
        red_bcm = BOARD_TO_BCM[self.RED_PIN_BOARD]
        green_bcm = BOARD_TO_BCM[self.GREEN_PIN_BOARD]
        blue_bcm = BOARD_TO_BCM[self.BLUE_PIN_BOARD]
        btn_bcm = BOARD_TO_BCM[self.BUTTON_PIN_BOARD]

        if not _GPIO_AVAILABLE:
            raise RuntimeError(
                "gpiozero not available. Install with: pip install gpiozero rpi-lgpio"
            )

        chip = _detect_gpio_chip()
        Device.pin_factory = LGPIOFactory(chip=chip)
        print(f"[GPIO] Using gpiochip{chip}")

        self.dc = OutputDevice(dc_bcm)
        self.rst = OutputDevice(rst_bcm)

        self.backlight = PWMOutputDevice(led_bcm, frequency=1000, initial_value=0)

        self.red_pwm = PWMOutputDevice(red_bcm, frequency=100, initial_value=1)
        self.green_pwm = PWMOutputDevice(green_bcm, frequency=100, initial_value=1)
        self.blue_pwm = PWMOutputDevice(blue_bcm, frequency=100, initial_value=1)
        self._current_r = 0
        self._current_g = 0
        self._current_b = 0

        self.button_press_callback = None
        self.button_release_callback = None
        self._btn_pin = btn_bcm
        self._btn_chip = chip
        self._btn_handle = lgpio.gpiochip_open(chip)
        lgpio.gpio_claim_input(self._btn_handle, btn_bcm, lgpio.SET_PULL_DOWN)
        self._btn_last_state = lgpio.gpio_read(self._btn_handle, btn_bcm)
        print(f"[Button] Initial state: {self._btn_last_state}")
        self._btn_running = True
        self._btn_thread = threading.Thread(target=self._button_poll_loop, daemon=True)
        self._btn_thread.start()

        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 100_000_000
        self.spi.mode = 0b00

        self.previous_frame = None
        self._detect_hardware_version()
        self.set_backlight(0)
        self._reset_lcd()
        self._init_display()
        self.fill_screen(0)

    def _detect_hardware_version(self):
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("Model"):
                        model_name = line.strip().split(":")[1].strip()
                        self.backlight_mode = not (
                            "Zero" in model_name and "2" not in model_name
                        )
                        print(
                            f"Detected hardware: {model_name}, "
                            f"Backlight mode: {'PWM' if self.backlight_mode else 'Simple Switch'}"
                        )
                        return
            self.backlight_mode = True
        except Exception as e:
            print(f"Error detecting hardware version: {e}")
            self.backlight_mode = True

    def set_backlight(self, brightness):
        if self.backlight_mode:
            if 0 <= brightness <= 100:
                self.backlight.value = (100 - brightness) / 100.0
        else:
            if brightness == 0:
                self.backlight.value = 1
            else:
                self.backlight.value = 0

    def _reset_lcd(self):
        self.rst.on()
        time.sleep(0.1)
        self.rst.off()
        time.sleep(0.1)
        self.rst.on()
        time.sleep(0.12)

    def _init_display(self):
        self._send_command(0x11)
        time.sleep(0.12)
        USE_HORIZONTAL = 1
        direction = {0: 0x00, 1: 0xC0, 2: 0x70, 3: 0xA0}.get(USE_HORIZONTAL, 0x00)
        self._send_command(0x36, direction)
        self._send_command(0x3A, 0x05)
        self._send_command(0xB2, 0x0C, 0x0C, 0x00, 0x33, 0x33)
        self._send_command(0xB7, 0x35)
        self._send_command(0xBB, 0x32)
        self._send_command(0xC2, 0x01)
        self._send_command(0xC3, 0x15)
        self._send_command(0xC4, 0x20)
        self._send_command(0xC6, 0x0F)
        self._send_command(0xD0, 0xA4, 0xA1)
        self._send_command(
            0xE0, 0xD0, 0x08, 0x0E, 0x09, 0x09, 0x05,
            0x31, 0x33, 0x48, 0x17, 0x14, 0x15, 0x31, 0x34,
        )
        self._send_command(
            0xE1, 0xD0, 0x08, 0x0E, 0x09, 0x09, 0x15,
            0x31, 0x33, 0x48, 0x17, 0x14, 0x15, 0x31, 0x34,
        )
        self._send_command(0x21)
        self._send_command(0x29)

    def _send_command(self, cmd, *args):
        self.dc.off()
        self.spi.xfer2([cmd])
        if args:
            self.dc.on()
            self._send_data(list(args))

    def _send_data(self, data):
        self.dc.on()
        try:
            self.spi.writebytes2(data)
        except AttributeError:
            max_chunk = 4096
            for i in range(0, len(data), max_chunk):
                self.spi.writebytes(data[i : i + max_chunk])

    def set_window(self, x0, y0, x1, y1, use_horizontal=0):
        if use_horizontal in (0, 1):
            self._send_command(0x2A, x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF)
            self._send_command(
                0x2B,
                (y0 + 20) >> 8, (y0 + 20) & 0xFF,
                (y1 + 20) >> 8, (y1 + 20) & 0xFF,
            )
        elif use_horizontal in (2, 3):
            self._send_command(
                0x2A,
                (x0 + 20) >> 8, (x0 + 20) & 0xFF,
                (x1 + 20) >> 8, (x1 + 20) & 0xFF,
            )
            self._send_command(0x2B, y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF)
        self._send_command(0x2C)

    def draw_pixel(self, x, y, color):
        if x >= self.LCD_WIDTH or y >= self.LCD_HEIGHT:
            return
        self.set_window(x, y, x, y)
        self._send_data([(color >> 8) & 0xFF, color & 0xFF])

    def draw_line(self, x0, y0, x1, y1, color):
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.draw_pixel(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def fill_screen(self, color):
        self.set_window(0, 0, self.LCD_WIDTH - 1, self.LCD_HEIGHT - 1)
        high = (color >> 8) & 0xFF
        low = color & 0xFF
        buf = bytes([high, low]) * (self.LCD_WIDTH * self.LCD_HEIGHT)
        self._send_data(buf)

    def draw_image(self, x, y, width, height, pixel_data):
        if (x + width > self.LCD_WIDTH) or (y + height > self.LCD_HEIGHT):
            raise ValueError("Image size exceeds screen bounds")
        self.set_window(x, y, x + width - 1, y + height - 1)
        self._send_data(pixel_data)

    def set_rgb(self, r, g, b):
        self.red_pwm.value = 1.0 - (r / 255.0)
        self.green_pwm.value = 1.0 - (g / 255.0)
        self.blue_pwm.value = 1.0 - (b / 255.0)
        self._current_r = r
        self._current_g = g
        self._current_b = b

    def set_rgb_fade(self, r_target, g_target, b_target, duration_ms=100):
        steps = 20
        delay = duration_ms / steps / 1000.0
        r_step = (r_target - self._current_r) / steps
        g_step = (g_target - self._current_g) / steps
        b_step = (b_target - self._current_b) / steps
        for i in range(steps + 1):
            self.set_rgb(
                max(0, min(255, int(self._current_r + i * r_step))),
                max(0, min(255, int(self._current_g + i * g_step))),
                max(0, min(255, int(self._current_b + i * b_step))),
            )
            time.sleep(delay)

    def _button_poll_loop(self):
        while self._btn_running:
            state = lgpio.gpio_read(self._btn_handle, self._btn_pin)
            if state != self._btn_last_state:
                self._btn_last_state = state
                if state == 1:
                    print("[Button] PRESSED")
                    cb = self.button_press_callback
                    if cb:
                        threading.Thread(target=cb, daemon=True).start()
                else:
                    print("[Button] RELEASED")
                    cb = self.button_release_callback
                    if cb:
                        threading.Thread(target=cb, daemon=True).start()
                    else:
                        print("[Button] No release callback registered!")
            time.sleep(0.02)

    def button_pressed(self):
        return lgpio.gpio_read(self._btn_handle, self._btn_pin) == 1

    def on_button_press(self, callback):
        self.button_press_callback = callback

    def on_button_release(self, callback):
        self.button_release_callback = callback

    def cleanup(self):
        self._btn_running = False
        self.spi.close()
        self.red_pwm.close()
        self.green_pwm.close()
        self.blue_pwm.close()
        self.backlight.close()
        self.dc.close()
        self.rst.close()
        lgpio.gpiochip_close(self._btn_handle)
