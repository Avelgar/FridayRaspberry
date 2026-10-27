# -*- coding: utf-8 -*-
import os
import time
import subprocess
import threading
import base64
import io

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "1"
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

import pygame

try:
    from adafruit_servokit import ServoKit
except ImportError:
    ServoKit = None

# =========================================================================
# КОНТРОЛЛЕР 18 СЕРВОПРИВОДОВ (2x PCA9685: 0x40 и 0x60)
# =========================================================================
class RobotController:
    def __init__(self):
        self.available = False
        self.lock = threading.Lock()
        self.servos = []  # Список кортежей: (kit, channel)
        self.pca1 = None
        self.pca2 = None
        self.current_angles = []
        self._stop_motion_flag = False
        self._active_motion_thread = None

        if ServoKit is None:
            print("⚠️ Библиотека adafruit_servokit не установлена. Моторы отключены.")
            return

        print("🤖 Инициализация плат управления 18 сервоприводами...")

        # 1. Первая плата PCA9685 (0x40) - 9 сервоприводов (MG90S)
        try:
            self.pca1 = ServoKit(channels=16, address=0x40)
            for ch in range(9):
                try:
                    self.pca1.servo[ch].set_pulse_width_range(500, 2500)
                    self.servos.append((self.pca1, ch))
                except Exception as e:
                    print(f"⚠️ Ошибка калибровки платы 0x40 ch={ch}: {e}")
            print(f"✅ Плата 0x40 подключена (каналов: {len(self.servos)})")
        except Exception as e:
            print(f"❌ Ошибка подключения платы PCA9685 (0x40): {e}")

        # 2. Вторая плата PCA9685 (0x60) - 9 сервоприводов (MG90S)
        count_before_pca2 = len(self.servos)
        try:
            self.pca2 = ServoKit(channels=16, address=0x60)
            for ch in range(9):
                try:
                    self.pca2.servo[ch].set_pulse_width_range(500, 2500)
                    self.servos.append((self.pca2, ch))
                except Exception as e:
                    print(f"⚠️ Ошибка калибровки платы 0x60 ch={ch}: {e}")
            print(f"✅ Плата 0x60 подключена (каналов: {len(self.servos) - count_before_pca2})")
        except Exception as e:
            print(f"❌ Ошибка подключения платы PCA9685 (0x60): {e}")

        if self.servos:
            self.available = True
            self.current_angles = [90] * len(self.servos)
            print(f"🤖 Всего активно сервоприводов: {len(self.servos)} из 18!")
            # Приветственное движение в фоне
            threading.Thread(target=self.startup_dance, daemon=True).start()
        else:
            print("❌ Ни одна плата сервоприводов не обнаружена. Проверьте I2C и питание.")

    def stop(self):
        """Экстренная остановка любого текущего движения"""
        self._stop_motion_flag = True
        print("🛑 Остановка движения всех моторов.")

    def set_servo_angle(self, index, angle):
        """Установка угла для конкретного сервопривода (0..17)"""
        if not self.available or index < 0 or index >= len(self.servos):
            return
        angle = max(0, min(180, int(angle)))
        kit, ch = self.servos[index]
        with self.lock:
            try:
                kit.servo[ch].angle = angle
                self.current_angles[index] = angle
            except Exception:
                pass

    def set_all_angles(self, angle):
        """Установка одного угла на все 18 сервоприводов"""
        if not self.available:
            return
        angle = max(0, min(180, int(angle)))
        with self.lock:
            for idx, (kit, ch) in enumerate(self.servos):
                try:
                    kit.servo[ch].angle = angle
                    self.current_angles[idx] = angle
                except Exception:
                    pass

    def center_all(self):
        """Возврат всех 18 сервоприводов в нейтральное положение 90°"""
        if not self.available:
            return
        print("⚙️ Выставляем все 18 серв в исходное положение 90°...")
        self.set_all_angles(90)

    def smooth_move_all(self, start_angle, end_angle, step=2, delay=0.002):
        """Синхронный плавный ход всех 18 сервоприводов (как в test_servo.py)"""
        if not self.available:
            return
        step = abs(step) if end_angle >= start_angle else -abs(step)
        for cur_angle in range(start_angle, end_angle + step, step):
            if self._stop_motion_flag:
                break
            target_angle = max(0, min(180, cur_angle))
            with self.lock:
                for idx, (kit, ch) in enumerate(self.servos):
                    try:
                        kit.servo[ch].angle = target_angle
                        self.current_angles[idx] = target_angle
                    except Exception:
                        pass
            time.sleep(delay)

    def startup_dance(self):
        """Приветственное движение всеми сервами при старте Пятницы"""
        if not self.available:
            return
        try:
            self._stop_motion_flag = False
            print("🤖 Пятница выполняет стартовый взмах всеми сервами...")
            self.set_all_angles(90)
            time.sleep(0.3)
            self.smooth_move_all(90, 130, step=3, delay=0.005)
            time.sleep(0.15)
            self.smooth_move_all(130, 50, step=3, delay=0.005)
            time.sleep(0.15)
            self.smooth_move_all(50, 90, step=3, delay=0.005)
            print("✅ Приветственное движение всеми 18 сервами завершено.")
        except Exception as e:
            print(f"❌ Ошибка моторов при запуске: {e}")

    def dance(self, cycles=2):
        """Плавный волновой танец всех 18 серв (0° -> 180° -> 0°)"""
        if not self.available:
            return
        self._stop_motion_flag = False
        print(f"💃 Пятница танцует (циклов: {cycles})!")
        try:
            for _ in range(cycles):
                if self._stop_motion_flag:
                    break
                self.smooth_move_all(90, 180, step=2, delay=0.003)
                time.sleep(0.1)
                if self._stop_motion_flag:
                    break
                self.smooth_move_all(180, 0, step=2, delay=0.003)
                time.sleep(0.1)
                if self._stop_motion_flag:
                    break
                self.smooth_move_all(0, 90, step=2, delay=0.003)
                time.sleep(0.1)
            self.set_all_angles(90)
            print("✅ Танец завершен.")
        except Exception as e:
            print(f"❌ Ошибка в танце: {e}")

    def _tripod_gait_step(self, direction=1):
        """
        Универсальный шаг для шагающего робота / гексапода (18 сервоприводов: 6 ног x 3 сустава).
        direction: 1 (вперед), -1 (назад), 2 (поворот влево), -2 (поворот вправо)
        """
        if not self.available or len(self.servos) < 18:
            # Если подключено меньше 18, делаем синхронный волновой шаг
            cur = self.current_angles[0] if self.current_angles else 90
            target1 = 135 if direction > 0 else 45
            target2 = 45 if direction > 0 else 135
            self.smooth_move_all(cur, target1, step=3, delay=0.004)
            time.sleep(0.1)
            self.smooth_move_all(target1, target2, step=3, delay=0.004)
            time.sleep(0.1)
            self.smooth_move_all(target2, 90, step=3, delay=0.004)
            return

        # Индексы 6 ног по 3 сервопривода (coxa, femur, tibia)
        # Нога 0, 1, 2 на плате 0x40 (левый борт)
        # Нога 3, 4, 5 на плате 0x60 (правый борт)
        tripod_a_legs = [0, 2, 4]  # Тройка ног A
        tripod_b_legs = [1, 3, 5]  # Тройка ног B

        coxa_shift = 25 * (1 if direction in [1, 2] else -1)
        femur_lift = 25

        def set_leg(leg_idx, coxa_offset, femur_offset):
            base_idx = leg_idx * 3
            # Угол поворота ноги (coxa)
            angle_c = 90 + (coxa_offset if leg_idx < 3 else -coxa_offset)
            # Угол подъема ноги (femur)
            angle_f = 90 + femur_offset
            self.set_servo_angle(base_idx, angle_c)
            self.set_servo_angle(base_idx + 1, angle_f)

        # Фаза 1: Тройка A поднимается и выносится вперед, Тройка B толкает назад
        for l in tripod_a_legs:
            set_leg(l, 0, femur_lift)
        time.sleep(0.08)

        for l in tripod_a_legs:
            set_leg(l, coxa_shift, femur_lift)
        for l in tripod_b_legs:
            mult = -1 if direction in [1, -1] else 1
            set_leg(l, -coxa_shift * mult, 0)
        time.sleep(0.08)

        # Опускаем тройку A
        for l in tripod_a_legs:
            set_leg(l, coxa_shift, 0)
        time.sleep(0.08)

        # Фаза 2: Тройка B поднимается и выносится вперед, Тройка A толкает назад
        for l in tripod_b_legs:
            set_leg(l, 0, femur_lift)
        time.sleep(0.08)

        for l in tripod_b_legs:
            mult = 1 if direction in [1, -1] else -1
            set_leg(l, coxa_shift * mult, femur_lift)
        for l in tripod_a_legs:
            set_leg(l, -coxa_shift, 0)
        time.sleep(0.08)

        # Опускаем тройку B
        for l in tripod_b_legs:
            set_leg(l, coxa_shift, 0)
        time.sleep(0.08)

    def move_forward(self, steps=2):
        """Движение робота ВПЕРЕД всеми сервоприводами"""
        if not self.available:
            return
        self._stop_motion_flag = False
        print("⚙️ Движение: ВПЕРЕД (работают все 18 сервоприводов)!")
        try:
            for s in range(steps):
                if self._stop_motion_flag:
                    break
                self._tripod_gait_step(direction=1)
            self.set_all_angles(90)
        except Exception as e:
            print(f"❌ Ошибка при движении вперед: {e}")

    def move_backward(self, steps=2):
        """Движение робота НАЗАД всеми сервоприводами"""
        if not self.available:
            return
        self._stop_motion_flag = False
        print("⚙️ Движение: НАЗАД (работают все 18 сервоприводов)!")
        try:
            for s in range(steps):
                if self._stop_motion_flag:
                    break
                self._tripod_gait_step(direction=-1)
            self.set_all_angles(90)
        except Exception as e:
            print(f"❌ Ошибка при движении назад: {e}")

    def turn_left(self, steps=2):
        """Поворот ВЛЕВО всеми сервоприводами"""
        if not self.available:
            return
        self._stop_motion_flag = False
        print("⚙️ Поворот: ВЛЕВО (работают все 18 сервоприводов)!")
        try:
            for s in range(steps):
                if self._stop_motion_flag:
                    break
                self._tripod_gait_step(direction=2)
            self.set_all_angles(90)
        except Exception as e:
            print(f"❌ Ошибка при повороте влево: {e}")

    def turn_right(self, steps=2):
        """Поворот ВПРАВО всеми сервоприводами"""
        if not self.available:
            return
        self._stop_motion_flag = False
        print("⚙️ Поворот: ВПРАВО (работают все 18 сервоприводов)!")
        try:
            for s in range(steps):
                if self._stop_motion_flag:
                    break
                self._tripod_gait_step(direction=-2)
            self.set_all_angles(90)
        except Exception as e:
            print(f"❌ Ошибка при повороте вправо: {e}")


# =========================================================================
# КОНТРОЛЛЕР XBOX ДЖОЙСТИКА (РАБОТАЕТ БЕЗ ИНТЕРНЕТА / WI-FI)
# =========================================================================
class JoystickController:
    """
    Автономный контроллер Xbox геймпада через USB-приемник.
    Работает в отдельном потоке, не зависит от наличия Wi-Fi и сети.
    """
    def __init__(self, robot_controller):
        self.robot = robot_controller
        self.running = False
        self.thread = None
        self.joystick = None
        self.master_angle = 90
        self.last_move_time = 0
        self.last_analog_time = 0
        self.deadzone = 0.25

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _worker(self):
        os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
        try:
            pygame.init()
            pygame.joystick.init()
        except Exception as e:
            print(f"❌ Ошибка инициализации подсистемы джойстика Pygame: {e}")
            return

        print("🎮 Служба Xbox джойстика активна (автономно, Wi-Fi не нужен).")
        print("🎮 Ожидание подключения USB-приемника / включения геймпада...")

        while self.running:
            try:
                pygame.event.pump()
                count = pygame.joystick.get_count()

                if count == 0:
                    if self.joystick is not None:
                        print("⚠️ Xbox геймпад отключен или перешел в спящий режим.")
                        try:
                            self.joystick.quit()
                        except Exception:
                            pass
                        self.joystick = None
                    time.sleep(1.0)
                    continue

                if self.joystick is None:
                    try:
                        self.joystick = pygame.joystick.Joystick(0)
                        self.joystick.init()
                        name = self.joystick.get_name()
                        print("\n" + "=" * 50)
                        print(f"🎮 XBOX ДЖОЙСТИК ПОДКЛЮЧЕН: {name}")
                        print("🎮 Управление 18 сервоприводами:")
                        print("   🕹️ Левый стик / D-Pad: Вперед / Назад / Влево / Вправо")
                        print("   🕹️ Правый стик (Y): Плавный угол ВСЕХ 18 серв (0°-180°)")
                        print("   🟢 Кнопка A: Центр (исходное 90°)")
                        print("   🔴 Кнопка B: Танец / Волна всех серв (как в test_servo)")
                        print("   🔵 Кнопка X: Все сервы в 0°")
                        print("   🟡 Кнопка Y: Все сервы в 180°")
                        print("   🔘 LB / RB: Точная подстройка угла (-10° / +10°)")
                        print("   🔘 Back / Start: Экстренный СТОП")
                        print("=" * 50 + "\n")
                    except Exception as e:
                        print(f"⚠️ Ошибка захвата геймпада: {e}")
                        self.joystick = None
                        time.sleep(1.0)
                        continue

                # Обработка событий кнопок и крестовины (D-Pad)
                for event in pygame.event.get():
                    if event.type == pygame.JOYBUTTONDOWN:
                        btn = event.button
                        if btn == 0:  # A
                            print("🎮 [A] -> Центр всех 18 серв (90°)")
                            self.master_angle = 90
                            self.robot.center_all()
                        elif btn == 1:  # B
                            print("🎮 [B] -> Танец всех 18 серв (тест)")
                            threading.Thread(target=self.robot.dance, daemon=True).start()
                        elif btn == 2:  # X
                            print("🎮 [X] -> Все 18 серв в 0°")
                            self.master_angle = 0
                            self.robot.set_all_angles(0)
                        elif btn == 3:  # Y
                            print("🎮 [Y] -> Все 18 серв в 180°")
                            self.master_angle = 180
                            self.robot.set_all_angles(180)
                        elif btn == 4:  # LB
                            self.master_angle = max(0, self.master_angle - 10)
                            print(f"🎮 [LB] -> Угол {self.master_angle}°")
                            self.robot.set_all_angles(self.master_angle)
                        elif btn == 5:  # RB
                            self.master_angle = min(180, self.master_angle + 10)
                            print(f"🎮 [RB] -> Угол {self.master_angle}°")
                            self.robot.set_all_angles(self.master_angle)
                        elif btn in [6, 7]:  # Back / Start
                            print("🎮 [Back/Start] -> Экстренный СТОП")
                            self.robot.stop()

                    elif event.type == pygame.JOYHATMOTION:  # D-Pad
                        hat_x, hat_y = event.value
                        if hat_y == 1:
                            print("🎮 [D-Pad Вверх] -> ВПЕРЕД")
                            threading.Thread(target=self.robot.move_forward, daemon=True).start()
                        elif hat_y == -1:
                            print("🎮 [D-Pad Вниз] -> НАЗАД")
                            threading.Thread(target=self.robot.move_backward, daemon=True).start()
                        elif hat_x == -1:
                            print("🎮 [D-Pad Влево] -> ВЛЕВО")
                            threading.Thread(target=self.robot.turn_left, daemon=True).start()
                        elif hat_x == 1:
                            print("🎮 [D-Pad Вправо] -> ВПРАВО")
                            threading.Thread(target=self.robot.turn_right, daemon=True).start()

                # Непрерывный опрос стиков
                now = time.time()
                num_axes = self.joystick.get_numaxes()

                # 1. Левый стик: Ходьба (оси 0 и 1)
                if num_axes >= 2 and (now - self.last_move_time > 0.45):
                    lx = self.joystick.get_axis(0)
                    ly = self.joystick.get_axis(1)

                    if ly < -0.6:
                        self.last_move_time = now
                        print("🕹️ [Левый стик] ВПЕРЕД")
                        threading.Thread(target=self.robot.move_forward, daemon=True).start()
                    elif ly > 0.6:
                        self.last_move_time = now
                        print("🕹️ [Левый стик] НАЗАД")
                        threading.Thread(target=self.robot.move_backward, daemon=True).start()
                    elif lx < -0.6:
                        self.last_move_time = now
                        print("🕹️ [Левый стик] ВЛЕВО")
                        threading.Thread(target=self.robot.turn_left, daemon=True).start()
                    elif lx > 0.6:
                        self.last_move_time = now
                        print("🕹️ [Левый стик] ВПРАВО")
                        threading.Thread(target=self.robot.turn_right, daemon=True).start()

                # 2. Правый стик: Плавное синхронное вращение всех 18 серв (ось 3 или 4)
                ry_axis = None
                if num_axes >= 5:
                    ry_axis = 4  # Стандартная вертикальная ось правого стика Xbox
                elif num_axes >= 4:
                    ry_axis = 3

                if ry_axis is not None and (now - self.last_analog_time > 0.04):
                    ry_val = self.joystick.get_axis(ry_axis)
                    if abs(ry_val) > self.deadzone:
                        # ry_val: -1.0 (вверх) -> 180°, +1.0 (вниз) -> 0°
                        target_deg = int(((-ry_val + 1.0) / 2.0) * 180.0)
                        target_deg = max(0, min(180, target_deg))
                        if abs(target_deg - self.master_angle) >= 2:
                            self.master_angle = target_deg
                            self.robot.set_all_angles(target_deg)
                            self.last_analog_time = now

                time.sleep(0.02)

            except Exception:
                time.sleep(1.0)


# =========================================================================
# МУЗЫКАЛЬНЫЙ ПЛЕЕР
# =========================================================================
class MusicPlayer:
    def __init__(self, folder):
        self.folder = folder
        self.files = []
        self.current_index = 0
        self.is_playing = False
        self.volume = 1.0
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
            print("🎵 Музыкальный плеер инициализирован")
        except Exception:
            pass
        self.update_playlist()

    def update_playlist(self):
        if not os.path.exists(self.folder):
            try:
                os.makedirs(self.folder)
            except Exception:
                pass
        self.files = [f for f in os.listdir(self.folder) if f.lower().endswith('.mp3')]
        self.files.sort()

    def play(self, index=None):
        if not self.files:
            return
        if index is not None:
            self.current_index = index
        if self.current_index >= len(self.files):
            self.current_index = 0
        if self.current_index < 0:
            self.current_index = len(self.files) - 1

        attempts = 0
        total = len(self.files)
        while attempts < total:
            track_path = os.path.join(self.folder, self.files[self.current_index])
            try:
                pygame.mixer.music.load(track_path)
                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.play()
                self.is_playing = True
                print(f"▶️ Играет: {self.files[self.current_index]}")
                return
            except Exception:
                self.current_index = (self.current_index + 1) % total
                attempts += 1
        self.is_playing = False

    def stop(self):
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        self.is_playing = False

    def next_track(self):
        self.current_index += 1
        self.play()

    def prev_track(self):
        self.current_index -= 1
        self.play()

    def set_volume_low(self):
        if self.is_playing:
            try:
                pygame.mixer.music.set_volume(0.2)
            except Exception:
                pass

    def set_volume_high(self):
        if self.is_playing:
            try:
                pygame.mixer.music.set_volume(1.0)
            except Exception:
                pass


# =========================================================================
# МЕНЕДЖЕР КАМЕРЫ НА PICAMERA2 (ОФИЦИАЛЬНЫЙ СТАНДАРТ RASPBERRY PI)
# =========================================================================
class CameraManager:
    def __init__(self):
        self.picam2 = None
        self.lock = threading.Lock()
        self._init_camera()

    def _init_camera(self):
        print("📷 Инициализация камеры через Picamera2 (Unicam)...")
        try:
            from picamera2 import Picamera2
            self.picam2 = Picamera2()
            config = self.picam2.create_preview_configuration(main={"size": (640, 480)})
            self.picam2.configure(config)
            self.picam2.start()
            time.sleep(0.5)
            print("✅ Камера (Picamera2) успешно запущена и готова к съемке!")
        except Exception as e:
            print(f"❌ Ошибка инициализации Picamera2: {e}")
            self.picam2 = None

    def capture_base64(self):
        """Мгновенно захватывает 1 кадр в формате JPEG через GPU"""
        if not self.picam2:
            return None
        with self.lock:
            try:
                stream = io.BytesIO()
                self.picam2.capture_file(stream, format="jpeg")
                return base64.b64encode(stream.getvalue()).decode('utf-8')
            except Exception as ex:
                print(f"❌ Ошибка захвата кадра: {ex}")
                return None

    def stop(self):
        if self.picam2:
            try:
                self.picam2.stop()
                self.picam2.close()
            except Exception:
                pass
            self.picam2 = None
