# -*- coding: utf-8 -*-
import os
import time
import subprocess
import threading
import base64
import cv2

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "1"
import pygame

try:
    from adafruit_servokit import ServoKit
except ImportError:
    pass

class RobotController:
    def __init__(self):
        self.available = False
        try:
            self.kit = ServoKit(channels=16)
            self.servo_port = 0 
            self.kit.servo[self.servo_port].angle = 90 
            self.available = True
            print("🤖 Моторы подключены и готовы!")
        except Exception:
            pass

    def move_forward(self):
        if not self.available: return
        try:
            print("⚙️ Движение: ВПЕРЕД")
            self.kit.servo[self.servo_port].angle = 180
            time.sleep(0.5)
            self.kit.servo[self.servo_port].angle = 0
            time.sleep(0.5)
            self.kit.servo[self.servo_port].angle = 90
        except Exception as e:
            print(f"❌ Ошибка мотора: {e}")

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
        except: pass
        self.update_playlist()

    def update_playlist(self):
        if not os.path.exists(self.folder):
            try: os.makedirs(self.folder)
            except: pass
        self.files = [f for f in os.listdir(self.folder) if f.lower().endswith('.mp3')]
        self.files.sort()

    def play(self, index=None):
        if not self.files: return
        if index is not None: self.current_index = index
        if self.current_index >= len(self.files): self.current_index = 0
        if self.current_index < 0: self.current_index = len(self.files) - 1

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
            except:
                self.current_index = (self.current_index + 1) % total
                attempts += 1
        self.is_playing = False

    def stop(self):
        pygame.mixer.music.stop()
        self.is_playing = False

    def next_track(self): self.current_index += 1; self.play()
    def prev_track(self): self.current_index -= 1; self.play()
    def set_volume_low(self): 
        if self.is_playing: pygame.mixer.music.set_volume(0.2)
    def set_volume_high(self): 
        if self.is_playing: pygame.mixer.music.set_volume(1.0)

# =========================================================================
# МЕНЕДЖЕР КАМЕРЫ НА PICAMERA2 (ОФИЦИАЛЬНЫЙ СТАНДАРТ RASPBERRY PI)
# =========================================================================
import io

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
            
            # Настройка разрешения 640x480 для оптимальной скорости
            config = self.picam2.create_preview_configuration(main={"size": (640, 480)})
            self.picam2.configure(config)
            self.picam2.start()
            
            # Небольшая пауза для прогрева автоэкспозиции
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