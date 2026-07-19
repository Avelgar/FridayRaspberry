#!/home/avelgar/myenv/bin/python3
import asyncio
import websockets
import ssl
import json
import time
import base64
import threading
import queue
from vosk import Model, KaldiRecognizer
import os
from datetime import datetime
import cv2
import sys
import subprocess
import requests
import wave
import collections
import numpy as np

# === ИМПОРТ ДЛЯ МОТОРОВ ===
try:
    from adafruit_servokit import ServoKit
except ImportError:
    print("⚠️ Библиотека adafruit_servokit не найдена. Запустите: pip3 install adafruit-circuitpython-servokit")

# === ИМПОРТ PYGAME ДЛЯ МУЗЫКИ ===
import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "1"
import pygame
# ================================

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
HISTORY_URL = "https://friday-assistant.ru/clear_history"
UPLOAD_AUDIO_URL = "https://friday-assistant.ru/upload_audio_command" 

RECONNECT_DELAY = 5
PING_INTERVAL = 30
MODEL_PATH = "/home/avelgar/vosk-model-small-ru-0.22" 
MUSIC_FOLDER = "/home/avelgar/music"

TEMP_WAV_FILENAME = "/tmp/command_record.wav"

config = {
    "bot_name": "пятница",
    "voice_name": "anna"
}

commands_queue = queue.Queue()

try:
    import pyaudio
    PA_AVAILABLE = True
except ImportError:
    PA_AVAILABLE = False

speech_rec_instance = None
player = None
robot = None 

# ===================================================================
# === УПРАВЛЕНИЕ МОТОРАМИ (PCA9685) ===
# ===================================================================
class RobotController:
    def __init__(self):
        self.available = False
        try:
            self.kit = ServoKit(channels=16)
            self.servo_port = 0 
            self.kit.servo[self.servo_port].angle = 90 
            self.available = True
            print("🤖 Моторы подключены и готовы к работе!")
        except Exception as e:
            print(f"⚠️ Ошибка инициализации моторов: {e}")

    def move_forward(self):
        if not self.available:
            return
        try:
            print("⚙️ Выполняю движение: ВПЕРЕД")
            self.kit.servo[self.servo_port].angle = 180
            time.sleep(0.5)
            self.kit.servo[self.servo_port].angle = 0
            time.sleep(0.5)
            self.kit.servo[self.servo_port].angle = 90
        except Exception as e:
            print(f"❌ Ошибка при движении мотора: {e}")

# ===================================================================
# === МУЗЫКАЛЬНЫЙ ПЛЕЕР ===
# ===================================================================
class MusicPlayer:
    def __init__(self, folder):
        self.folder = folder
        self.files =[]
        self.current_index = 0
        self.is_playing = False
        self.volume = 1.0
        
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
            print("🎵 Аудиосистема инициализирована")
        except Exception as e:
            print(f"❌ Ошибка инициализации аудио: {e}")

        self.update_playlist()

    def update_playlist(self):
        if not os.path.exists(self.folder):
            try: os.makedirs(self.folder)
            except: pass
        self.files =[f for f in os.listdir(self.folder) if f.lower().endswith('.mp3')]
        self.files.sort()
        print(f"🎵 Найдено треков: {len(self.files)}")

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
            except Exception as e:
                print(f"⚠️ Файл '{self.files[self.current_index]}' поврежден. Пропуск...")
                self.current_index += 1
                if self.current_index >= total: self.current_index = 0
                attempts += 1
        self.is_playing = False

    def stop(self):
        pygame.mixer.music.stop()
        self.is_playing = False

    def next_track(self):
        self.current_index += 1
        self.play()

    def prev_track(self):
        self.current_index -= 1
        self.play()

    def set_volume_low(self):
        if self.is_playing: pygame.mixer.music.set_volume(0.2)

    def set_volume_high(self):
        if self.is_playing: pygame.mixer.music.set_volume(1.0)

# ===================================================================
# === ФУНКЦИИ ===
# ===================================================================

def speak(text):
    print(f"🗣️ ГОВОРЮ: {text}")
    if player: player.set_volume_low()
    if speech_rec_instance: speech_rec_instance.pause_listening()
    
    try:
        cmd = f'echo "{text}" | RHVoice-test -p {config["voice_name"]} -o - | aplay -q'
        subprocess.run(cmd, shell=True)
    except Exception as e:
        print(f"❌ Ошибка синтеза: {e}")
    
    if speech_rec_instance: speech_rec_instance.resume_listening()
    if player: player.set_volume_high()

def upload_audio_to_server(filepath, mac):
    print(f"☁️ Подготовка АУДИОФАЙЛА для отправки по WS...")
    try:
        with open(filepath, 'rb') as f:
            audio_b64 = base64.b64encode(f.read()).decode('utf-8')
        
        # Передаем словарь вместо текста
        cmd_data = {
            "is_audio": True,
            "audio_base64": audio_b64,
            "mac": mac
        }
        commands_queue.put(cmd_data)
    except Exception as e:
        print(f"❌ Ошибка подготовки аудио: {e}")
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)

def clear_history_api(mac):
    try:
        requests.post(HISTORY_URL, json={"mac": mac}, timeout=10)
        print("✅ История очищена.")
    except Exception: pass

def control_music(command):
    if not player: return
    cmd = command.lower()
    if "включить" in cmd: 
        player.update_playlist()
        player.play(0)
    elif "выключить" in cmd or "стоп" in cmd or "пауза" in cmd: player.stop()
    elif "следующий" in cmd: player.next_track()
    elif "предыдущий" in cmd: player.prev_track()

async def send_command(websocket, command_data, bot_name, mac):
    current_time = datetime.now().isoformat()
    
    # Если пришел словарь с аудио
    if isinstance(command_data, dict) and command_data.get("is_audio"):
        command_message = {
            "command": "", # Текста пока нет
            "audio_base64": command_data["audio_base64"],
            "timestamp": current_time,
            "name": bot_name,
            "command_type": "голосовое сообщение",
            "mac": mac
        }
        print("📤 ОТПРАВЛЕНО (WS): [Аудиофайл]")
    else:
        # Если пришел обычный текст (от Vosk)
        command_message = {
            "command": command_data,
            "timestamp": current_time,
            "name": bot_name,
            "command_type": "голосовое сообщение",
            "mac": mac
        }
        print(f"📤 ОТПРАВЛЕНО (WS): {command_data}")

    json_msg = json.dumps(command_message, ensure_ascii=False)
    encoded = base64.b64encode(json_msg.encode('utf-8')).decode('utf-8')
    await websocket.send(encoded)

def process_message(message_json, mac_address):
    try:
        data = json.loads(message_json)
        msg_type = data.get("type")

        if msg_type == "new_message":
            actions = data.get("actions",[])
            for action in actions:
                if "голосовой ответ|" in action:
                    speak(action.split("|", 1)[1])
                elif "очистка истории" in action:
                     threading.Thread(target=clear_history_api, args=(mac_address,), daemon=True).start()
                elif "музыка|" in action:
                    control_music(action.split("|", 1)[1])
                elif "смена имени|" in action:
                    config["bot_name"] = action.split("|", 1)[1].strip().lower()
                    speak(f"Теперь меня зовут {config['bot_name']}")
                elif "движение|" in action:
                    direction = action.split("|", 1)[1].strip().lower()
                    if "вперед" in direction and robot:
                        threading.Thread(target=robot.move_forward, daemon=True).start()
                elif "разбудить" in action.lower():
                    # Твой реальный MAC-адрес ноутбука
                    target_pc_mac = "54:BF:64:11:95:62" 
                    print(f"🚀 Пробуждаю ноутбук: {target_pc_mac}")
                    
                    try:
                        # Используем именно ту команду, которая сработала вручную
                        # Мы используем subprocess.run для надежности
                        subprocess.run(["sudo", "etherwake", "-i", "eth0", target_pc_mac], check=True)
                        speak("Хорошо, включаю ноутбук")
                    except Exception as e:
                        print(f"❌ Ошибка при отправке пакета: {e}")
                        speak("Произошла ошибка при включении ноутбука")


    except Exception as e:
        print(f"❌ Ошибка JSON: {e}")

# ===================================================================
# === РАСПОЗНАВАНИЕ И ЗАПИСЬ АУДИОКОМАНД (VAD) ===
# ===================================================================
class SpeechRecognizer:
    def __init__(self, commands_queue):
        self.model = None
        self.recognizer = None
        self.audio = None
        self.stream = None
        
        self.is_listening = False
        self.is_paused = False
        self.device_index = None
        self.sample_rate = 16000

        self.audio_queue = queue.Queue()
        self.state = "WAITING_WAKEWORD" # Возможные: WAITING_WAKEWORD, PROCESSING, RECORDING_COMMAND
        
        self.command_buffer =[]
        self.silence_chunks = 0
        
        # НАСТРОЙКИ ДЕТЕКТОРА ТИШИНЫ:
        self.silence_threshold = 500  # Чувствительность к тишине (чем больше число, тем больше шума считается тишиной)
        self.max_silence_chunks = 6   # Сколько чанков (кусочков аудио) нужно для фиксации тишины. При 4000 frames это ~1.5 сек.

        if os.path.exists(MODEL_PATH):
            try:
                self.model = Model(MODEL_PATH)
                print(f"✅ Vosk модель загружена")
            except: pass
        else:
            print(f"❌ Нет модели: {MODEL_PATH}")
    
    def find_input_device(self):
        with no_alsa_err():
            self.audio = pyaudio.PyAudio()
            
        count = self.audio.get_device_count()
        candidate = None
        
        for i in range(count):
            try:
                info = self.audio.get_device_info_by_index(i)
                name = info.get('name', '').lower()
                if info['maxInputChannels'] > 0:
                    if "fifine" in name: candidate = i; break
                    if "usb" in name and "ms2109" not in name and not candidate: candidate = i
            except: continue
            
        if candidate is None:
             for i in range(count):
                 if self.audio.get_device_info_by_index(i).get('maxInputChannels') > 0: candidate = i; break
        
        if candidate is not None:
            self.device_index = candidate
            for rate in[16000, 44100, 48000, 32000, 8000]:
                try:
                    if self.audio.is_format_supported(rate, input_device=candidate, input_channels=1, input_format=pyaudio.paInt16):
                        self.sample_rate = rate
                        print(f"✅ Микрофон [{candidate}] принял частоту: {rate} Hz")
                        return True
                except: pass
            
            self.sample_rate = 16000
            print("⚠️ Не удалось подобрать частоту, пробуем наугад 16000")
            return True
            
        print("❌ Микрофоны не найдены!")
        return False

    def pause_listening(self):
        self.is_paused = True
    
    def resume_listening(self):
        self.is_paused = False
    
    def save_wav_file(self, frames):
        try:
            wf = wave.open(TEMP_WAV_FILENAME, 'wb')
            wf.setnchannels(1)
            wf.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
            wf.setframerate(self.sample_rate)
            wf.writeframes(b''.join(frames))
            wf.close()
            return True
        except Exception as e:
            print(f"❌ Ошибка записи WAV: {e}")
            return False
    
    def audio_callback(self, in_data, frame_count, time_info, status):
        # Если не на паузе - просто кладем аудио в очередь для обработки
        if not self.is_paused:
            self.audio_queue.put(in_data)
        return (None, pyaudio.paContinue)

    def process_audio_loop(self):
        """Отдельный поток, чтобы не блокировать callback микрофона"""
        while self.is_listening:
            try:
                in_data = self.audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if self.state == "WAITING_WAKEWORD" and self.recognizer:
                # 1. ЖДЕМ ИМЯ РОБОТА
                if self.recognizer.AcceptWaveform(in_data):
                    res = json.loads(self.recognizer.Result())
                    self._check_wakeword(res.get('text', '').strip())
                else:
                    partial_res = json.loads(self.recognizer.PartialResult())
                    self._check_wakeword(partial_res.get('partial', '').strip())

            elif self.state == "RECORDING_COMMAND":
                # 2. ИДЕТ ЗАПИСЬ КОМАНДЫ ПОКА НЕ НАСТУПИТ ТИШИНА (Vosk тут отключен)
                self.command_buffer.append(in_data)
                
                # Высчитываем громкость (RMS) конвертируя байты в числа
                audio_np = np.frombuffer(in_data, dtype=np.int16).astype(np.float32)
                rms = np.sqrt(np.mean(np.square(audio_np)))

                if rms < self.silence_threshold:
                    self.silence_chunks += 1
                else:
                    self.silence_chunks = 0  # Кто-то говорит, сбрасываем счетчик тишины

                # Если тишина длится достаточно долго
                if self.silence_chunks >= self.max_silence_chunks:
                    print(f"🛑 Конец записи. Тишина зафиксирована.")
                    self._finish_recording_and_send()

    def _check_wakeword(self, text):
        bot_name = config["bot_name"]
        if bot_name in text.lower():
            print(f"⚡ УСЛЫШАЛ ИМЯ БОТА (во фрагменте): {text}")
            
            # Меняем стейт, чтобы Vosk перестал обрабатывать звук на время диалога
            self.state = "PROCESSING" 
            
            # Говорим "Слушаю вас". Функция сама ставит микрофон на паузу.
            speak("Слушаю вас") 
            
            # Очищаем очередь, чтобы выкинуть эхо от фразы "Слушаю вас"
            while not self.audio_queue.empty():
                try: self.audio_queue.get_nowait()
                except: pass

            print("🎙️ НАЧАЛА ПИСАТЬСЯ АУДИОКОМАНДА...")
            self.command_buffer =[]
            self.silence_chunks = 0
            self.state = "RECORDING_COMMAND"

    def _finish_recording_and_send(self):
        self.pause_listening()
        
        # Сохраняем и отправляем только если сказано больше, чем длилась тишина
        if len(self.command_buffer) > self.max_silence_chunks:
            if self.save_wav_file(self.command_buffer):
                mac = get_mac_address()
                # Запускаем отправку файла на сервер
                threading.Thread(
                    target=upload_audio_to_server, 
                    args=(TEMP_WAV_FILENAME, mac), 
                    daemon=True
                ).start()
        else:
            print("⚠️ Слишком короткая команда, отмена.")

        # Возвращаем систему к ожиданию имени
        self.state = "WAITING_WAKEWORD"
        self.command_buffer =[]
        self.silence_chunks = 0
        
        # Сбрасываем контекст Vosk, чтобы он не помнил старый фрагмент речи
        if self.recognizer:
            try: self.recognizer.Reset()
            except: pass
            
        self.resume_listening()

    def start_listening(self):
        if not self.model: return
        try:
            if not self.find_input_device(): return
    
            self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
            self.is_listening = True
            
            # Запускаем рабочий поток обработки аудио
            threading.Thread(target=self.process_audio_loop, daemon=True).start()
            
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=4000,
                stream_callback=self.audio_callback
            )
            
            self.stream.start_stream()
            print(f"✅ ПОТОК АУДИО ЗАПУЩЕН ({self.sample_rate} Hz)")
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
        print("📷 Инициализация камеры...")
        for idx in [0, -1]: 
            try:
                cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
                if cap.isOpened():
                    cap.set(3, 320)
                    cap.set(4, 240)
                    if cap.read()[0]:
                        self.camera = cap
                        print(f"✅ Камера найдена: {idx}")
                        break
                    cap.release()
            except: pass
        
        if not self.camera:
            print("⚠️ Камера не подключена")
            return

        try:
            cv2.namedWindow("Robot", cv2.WND_PROP_FULLSCREEN)
            cv2.setWindowProperty("Robot", cv2.WND_PROP_FULLSCREEN, 1)
        except: pass
        
        while self.is_running:
            try:
                ret, frame = self.camera.read()
                if ret:
                    cv2.imshow("Robot", frame) 
                    if cv2.waitKey(1) == ord('q'): break
                else: break
            except: break
        if self.camera: self.camera.release()
        cv2.destroyAllWindows()

# ===================================================================
# === MAIN ===
# ===================================================================
def get_mac_address():
    try:
        with open('/sys/class/net/wlan0/address', 'r') as f: return f.read().strip()
    except: return "unknown"

async def websocket_handler():
    mac = get_mac_address()
    ssl_ctx = ssl.create_default_context()
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
                        await send_command(ws, cmd, config["bot_name"], mac)
                    
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
                        decoded = base64.b64decode(msg).decode()
                        process_message(decoded, mac)
                    except asyncio.TimeoutError: pass
                    
        except Exception as e:
            print(f"⚠️ Реконнект {RECONNECT_DELAY}с... ({e})")
            await asyncio.sleep(RECONNECT_DELAY)

def main():
    global speech_rec_instance, player, robot
    print("="*40)
    print(f"🤖 СИСТЕМА ЗАПУСКАЕТСЯ... Имя: {config['bot_name']}")
    print("="*40)
    
    player = MusicPlayer(MUSIC_FOLDER)
    robot = RobotController() 
    speech_rec_instance = SpeechRecognizer(commands_queue)
    
    t_voice = threading.Thread(target=speech_rec_instance.start_listening, daemon=True)
    t_voice.start()
    
    t_ws = threading.Thread(target=lambda: asyncio.run(websocket_handler()), daemon=True)
    t_ws.start()
    
    time.sleep(2)
    display = FastCameraDisplay()
    display.start_display()
    
    print("💤 Режим ожидания...")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()