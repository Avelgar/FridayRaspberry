#!/home/avelgar/myenv/bin/python3
import asyncio
import websockets
import ssl
import json
import time
import logging
import base64
import threading
import queue
from vosk import Model, KaldiRecognizer
import os
from datetime import datetime
import cv2
import sys
import subprocess  # Нужно для запуска RHVoice

# === БЛОК ДЛЯ ОТКЛЮЧЕНИЯ ШУМА ALSA ===
from ctypes import *
from contextlib import contextmanager

try:
    ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
    def py_error_handler(filename, line, function, err, fmt):
        pass
    c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
except:
    pass

@contextmanager
def no_alsa_err():
    try:
        asound = cdll.LoadLibrary('libasound.so')
        asound.snd_lib_error_set_handler(c_error_handler)
        yield
        asound.snd_lib_error_set_handler(None)
    except:
        yield
# =====================================

sys.stdout.reconfigure(line_buffering=True)

# --- КОНФИГУРАЦИЯ ---
URI = "wss://friday-assistant.ru/ws"
RECONNECT_DELAY = 5
PING_INTERVAL = 30

MODEL_PATH = "/home/avelgar/vosk-model-small-ru-0.22" 

BOT_NAME = "пятница"
VOICE_NAME = "anna" # Голоса: anna, aleksandr, elena, irina (зависит от того, что установлено)

commands_queue = queue.Queue()

try:
    import pyaudio
    PA_AVAILABLE = True
except ImportError:
    PA_AVAILABLE = False

# Глобальная переменная для доступа к микрофону
speech_rec_instance = None

# --- ФУНКЦИИ ---

def speak(text):
    """Синтез речи через RHVoice"""
    print(f"🗣️ ГОВОРЮ: {text}")
    
    # 1. Ставим микрофон на паузу, чтобы не слышать самих себя
    if speech_rec_instance:
        speech_rec_instance.pause_listening()
    
    try:
        # Команда: echo "текст" | RHVoice-test -p голос | play -t wav -
        # play - это утилита из пакета sox. Можно заменить на aplay
        cmd = f'echo "{text}" | RHVoice-test -p {VOICE_NAME} -o - | aplay'
        subprocess.run(cmd, shell=True)
    except Exception as e:
        print(f"❌ Ошибка синтеза речи: {e}")
    
    # 2. Включаем микрофон обратно
    if speech_rec_instance:
        speech_rec_instance.resume_listening()


async def send_command(websocket, command, bot_name, mac):
    current_time = datetime.now().isoformat()
    command_message = {
        "command": command, "timestamp": current_time,
        "name": bot_name, "command_type": "голосовое сообщение", "mac": mac
    }
    json_msg = json.dumps(command_message, ensure_ascii=False)
    encoded = base64.b64encode(json_msg.encode('utf-8')).decode('utf-8')
    await websocket.send(encoded)
    print(f"📤 ОТПРАВЛЕНО: {command}")

def process_message(message_json):
    """Разбор ответа от сервера"""
    try:
        data = json.loads(message_json)
        msg_type = data.get("type")

        if msg_type == "new_message":
            actions = data.get("actions", [])
            
            for action in actions:
                if "голосовой ответ|" in action:
                    try:
                        _, text = action.split("|", 1)
                        print("\n" + "="*30)
                        print(f"🔊 БОТ ОТВЕТИЛ: {text}")
                        # ВЫЗЫВАЕМ ФУНКЦИЮ СИНТЕЗА РЕЧИ
                        speak(text)
                        print("="*30 + "\n")
                    except ValueError:
                        print(f"⚠️ Ошибка формата действия: {action}")
                
                elif "очистка истории" in action:
                     print("🧹 Команда очистки истории")

        elif msg_type == "ping":
            pass
            
        else:
            print(f"ℹ️ Сообщение типа {msg_type}: {data}")

    except Exception as e:
        print(f"❌ Ошибка обработки JSON: {e}")

# ===================================================================
# === КЛАСС РАСПОЗНАВАНИЯ ===
# ===================================================================
class SpeechRecognizer:
    def __init__(self, commands_queue):
        self.model = None
        self.recognizer = None
        self.audio = None
        self.stream = None
        self.is_listening = False
        self.is_paused = False  # Флаг паузы для TTS
        self.commands_queue = commands_queue
        self.device_index = None
        self.sample_rate = 16000
        
        if not PA_AVAILABLE:
            print("❌ PyAudio не установлен")
            return
        
        if os.path.exists(MODEL_PATH):
            try:
                self.model = Model(MODEL_PATH)
                print(f"✅ Vosk модель загружена: {MODEL_PATH}")
            except Exception as e:
                print(f"❌ Ошибка модели: {e}")
        else:
            print(f"❌ Нет модели по пути: {MODEL_PATH}")

    def find_input_device(self):
        print("🔍 Поиск микрофона Fifine...")
        with no_alsa_err():
            self.audio = pyaudio.PyAudio()
            
        count = self.audio.get_device_count()
        candidate = None
        
        # Простой поиск USB устройства
        for i in range(count):
            try:
                info = self.audio.get_device_info_by_index(i)
                name = info.get('name', '').lower()
                inputs = info.get('maxInputChannels', 0)
                
                if inputs > 0:
                    if "fifine" in name:
                        candidate = i
                        print(f"   ⭐️ НАШЕЛ FIFINE [{i}]")
                        break
                    if "usb" in name and "ms2109" not in name and candidate is None:
                        candidate = i
            except:
                continue
        
        if candidate is None:
             # Берем первый попавшийся, если спец. микрофон не найден
             for i in range(count):
                 if self.audio.get_device_info_by_index(i).get('maxInputChannels') > 0:
                     candidate = i
                     break

        if candidate is not None:
            self.device_index = candidate
            # Пробуем 16000 для Vosk, это оптимально
            for rate in [16000, 44100, 48000]:
                try:
                    if self.audio.is_format_supported(rate, input_device=candidate, input_channels=1, input_format=pyaudio.paInt16):
                        self.sample_rate = rate
                        print(f"✅ Микрофон index {candidate}. Частота: {rate} Hz")
                        return True
                except: pass
            return True
            
        print("❌ Микрофоны не найдены!")
        return False

    def pause_listening(self):
        """Приостанавливает поток, чтобы освободить аудио для динамика"""
        if self.stream and self.stream.is_active():
            self.is_paused = True
            # Мы не закрываем поток полностью, просто игнорируем данные или ставим на паузу
            # Но для надежности на RPi лучше остановить stream
            try:
                self.stream.stop_stream()
                print("⏸️ Микрофон на паузе...")
            except: pass

    def resume_listening(self):
        """Возобновляет поток"""
        if self.stream and self.stream.is_stopped():
            try:
                self.stream.start_stream()
                self.is_paused = False
                print("▶️ Микрофон активен")
            except: pass

    def audio_callback(self, in_data, frame_count, time_info, status):
        if self.is_paused:
            return (None, pyaudio.paContinue)

        if self.recognizer and self.is_listening:
            if self.recognizer.AcceptWaveform(in_data):
                res = json.loads(self.recognizer.Result())
                text = res.get('text', '').strip()
                if text:
                    print(f"🎤 СЛЫШУ: '{text}'")
                    if BOT_NAME in text.lower():
                        cmd = text.lower().replace(BOT_NAME, '').strip()
                        print(f"⚡ РАСПОЗНАНО: {cmd}")
                        self.commands_queue.put(cmd or "слушаю")
        return (in_data, pyaudio.paContinue)
    
    def start_listening(self):
        if not self.model: return
        try:
            if not self.find_input_device(): return

            self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
            
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=4000, # Немного увеличил буфер для RPi
                stream_callback=self.audio_callback
            )
            self.is_listening = True
            self.stream.start_stream()
            print("✅ ПОТОК АУДИО ЗАПУЩЕН")
            while self.is_listening: time.sleep(1)    
        except Exception as e:
            print(f"❌ Ошибка аудио потока: {e}")

# ===================================================================
# === КАМЕРА ===
# ===================================================================
class FastCameraDisplay:
    def __init__(self):
        self.camera = None
        self.is_running = True
        
    def start_display(self):
        # Оставляем как было, но важно: камера может конфликтовать за USB пропускную способность
        # если и микрофон и камера на одном контроллере USB 2.0
        print("📷 Инициализация камеры...")
        for idx in [0, -1]: # Упростил перебор
            cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
            if cap.isOpened():
                cap.set(3, 320)
                cap.set(4, 240)
                if cap.read()[0]:
                    self.camera = cap
                    break
                cap.release()
        
        if not self.camera:
            return

        # Если работаешь без дисплея (headless), убери imshow
        try:
            # cv2.namedWindow("Robot", cv2.WND_PROP_FULLSCREEN)
            # cv2.setWindowProperty("Robot", cv2.WND_PROP_FULLSCREEN, 1)
            pass
        except: pass
        
        while self.is_running:
            ret, frame = self.camera.read()
            if ret:
                # cv2.imshow("Robot", frame) # Раскомментируй если есть экран
                # if cv2.waitKey(1) == ord('q'): break
                time.sleep(0.05)
            else: break
        if self.camera: self.camera.release()
        cv2.destroyAllWindows()

# ===================================================================
# === WEBSOCKET ===
# ===================================================================
def get_mac_address():
    try:
        with open('/sys/class/net/wlan0/address', 'r') as f: return f.read().strip()
    except: return "unknown"

async def websocket_handler():
    mac = get_mac_address()
    ssl_ctx = ssl.create_default_context()
    # Отключаем проверку сертификатов если они самоподписанные
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE 
    
    while True:
        try:
            print(f"🌐 Подключение к {URI}...")
            async with websockets.connect(URI, ssl=ssl_ctx) as ws:
                print("✅ WebSocket ПОДКЛЮЧЕН!")
                
                reg = json.dumps({"MAC": mac, "DeviceName": "PiBot", "Password": "123"})
                await ws.send(base64.b64encode(reg.encode()).decode())
                
                last_ping = time.time()
                
                while True:
                    if time.time() - last_ping > PING_INTERVAL:
                        ping = json.dumps({"type": "ping", "timestamp": time.time(), "mac": mac})
                        try:
                            await ws.send(base64.b64encode(ping.encode()).decode())
                            last_ping = time.time()
                        except: break
                    
                    while not commands_queue.empty():
                        cmd = commands_queue.get()
                        await send_command(ws, cmd, "Пятница", mac)
                    
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
                        decoded = base64.b64decode(msg).decode()
                        process_message(decoded)
                    except asyncio.TimeoutError:
                        pass
                    
        except Exception as e:
            print(f"⚠️ Ошибка соединения: {e}. Реконнект {RECONNECT_DELAY}с...")
            await asyncio.sleep(RECONNECT_DELAY)

def main():
    global speech_rec_instance
    print("="*40)
    print("🤖 СИСТЕМА ЗАПУСКАЕТСЯ (RPI 3B+)...")
    print("="*40)
    
    # Создаем экземпляр распознавания речи
    speech_rec_instance = SpeechRecognizer(commands_queue)
    
    # Запускаем распознавание в отдельном потоке
    t_voice = threading.Thread(target=speech_rec_instance.start_listening, daemon=True)
    t_voice.start()
    
    # Запускаем WebSocket
    t_ws = threading.Thread(target=lambda: asyncio.run(websocket_handler()), daemon=True)
    t_ws.start()
    
    time.sleep(2)
    # Камеру запускаем в главном потоке или тоже в фоне
    FastCameraDisplay().start_display()

if __name__ == "__main__":
    main()