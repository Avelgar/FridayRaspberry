# -*- coding: utf-8 -*-
import pyaudio
import numpy as np
import base64
import time
import threading
import subprocess
import audioop
import json
from datetime import datetime
from config import state
from ctypes import *
from contextlib import contextmanager

ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
def py_error_handler(filename, line, function, err, fmt): pass
c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)

@contextmanager
def no_alsa_err():
    try:
        asound = cdll.LoadLibrary('libasound.so')
        asound.snd_lib_error_set_handler(c_error_handler)
        yield
        asound.snd_lib_error_set_handler(None)
    except: yield

class VoiceService:
    def __init__(self, ws_queue, loop, mac_address, music_player, robot, camera):
        self.ws_queue = ws_queue
        self.loop = loop
        self.mac = mac_address
        self.player = music_player
        self.robot = robot
        self.camera = camera # <--- ПОДКЛЮЧИЛИ КАМЕРУ
        
        self.message_history = []
        
        with no_alsa_err():
            self.pa = pyaudio.PyAudio()
            
        self.sample_rate = 16000
        self.vad_threshold = 0.015
        
        self.is_recording = False
        self.is_waiting_for_server = False
        self.is_speaking = False
        
        self.last_speech_time = time.time()
        self.last_video_time = 0 # Таймер для 1 FPS
        self.ignore_commands_until = 0
        self.current_msg_id = None
        
        self.pre_buffer = bytearray()
        self.max_pre_buffer_bytes = 32000 
        
        self.audio_out = self.pa.open(format=pyaudio.paInt16, channels=1, rate=24000, output=True)
        self.waiting_timer = None
        self.resample_state = None 
        self.history_cleared_in_this_turn = False

    def put_ws(self, msg):
        self.loop.call_soon_threadsafe(self.ws_queue.put_nowait, msg)

    def get_volume(self, data):
        arr = np.frombuffer(bytes(data), dtype=np.int16).astype(np.float32)
        if len(arr) == 0: return 0
        return np.mean(np.abs(arr)) / 32768.0

    def audio_callback(self, in_data, frame_count, time_info, status):
        if self.is_speaking or self.is_waiting_for_server: return (None, pyaudio.paContinue)
        if time.time() < self.ignore_commands_until: return (None, pyaudio.paContinue)

        # Ресемплинг в 16000 Hz
        if self.sample_rate != 16000:
            try:
                in_data, self.resample_state = audioop.ratecv(in_data, 2, 1, self.sample_rate, 16000, self.resample_state)
            except Exception:
                return (None, pyaudio.paContinue)

        volume = self.get_volume(in_data)
        if volume > self.vad_threshold:
            self.last_speech_time = time.time()

        is_silence = (time.time() - self.last_speech_time) > 1.5
        pcm_16_bytes = np.frombuffer(in_data, dtype=np.int16).tobytes()

        if self.is_recording:
            # 1. Отправляем чанк звука
            self.put_ws({
                "type": "audio_stream_chunk",
                "ui_msg_id": self.current_msg_id,
                "audio_base64": base64.b64encode(pcm_16_bytes).decode('utf-8')
            })

            # 2. Отправляем кадр с камеры раз в 1 секунду
            now = time.time()
            if self.camera and (now - self.last_video_time >= 1.0):
                self.last_video_time = now
                frame_b64 = self.camera.capture_base64()
                if frame_b64:
                    print(f"📸 [КАДР С КАМЕРЫ] Отправлен в Gemini ({len(frame_b64)} байт)!")
                    self.put_ws({
                        "type": "video_stream_chunk",
                        "ui_msg_id": self.current_msg_id,
                        "video_base64": frame_b64
                    })

            if is_silence:
                self.is_recording = False
                self.is_waiting_for_server = True
                print("🛑 Конец диктовки (Тишина). Ждем ответа...")
                
                self.put_ws({
                    "type": "audio_stream_end", 
                    "ui_msg_id": self.current_msg_id
                })
                
                if self.waiting_timer: self.waiting_timer.cancel()
                self.waiting_timer = threading.Timer(10.0, self.server_finished_response)
                self.waiting_timer.start()
        else:
            self.pre_buffer.extend(pcm_16_bytes)
            if len(self.pre_buffer) > self.max_pre_buffer_bytes:
                self.pre_buffer = self.pre_buffer[-self.max_pre_buffer_bytes:]
                
            if volume > self.vad_threshold:
                self.is_recording = True
                self.last_speech_time = time.time()
                self.last_video_time = time.time()
                self.current_msg_id = str(int(time.time() * 1000))
                
                print(f"🎙️ [VAD] Сработал голос. Начало стрима...")
                
                init_audio = base64.b64encode(self.pre_buffer).decode('utf-8')
                self.pre_buffer.clear()
                
                # 1. Стартовый пакет аудио
                msg_payload = {
                    "type": "web_command", 
                    "command": "", 
                    "stream_audio": True,
                    "audio_base64": init_audio, 
                    "mac": self.mac,
                    "timestamp": datetime.now().isoformat(), 
                    "name": state["bot_name"],
                    "voice_type": state["voice_name"], 
                    "command_type": "голосовое сообщение",
                    "message_history": self.message_history[-10:],
                    "ui_msg_id": self.current_msg_id
                }
                self.put_ws(msg_payload)

                # 2. Первый снимок сразу на старте фразы
                if self.camera:
                    first_frame = self.camera.capture_base64()
                    if first_frame:
                        print(f"📸 [ПЕРВЫЙ КАДР] Отправлен стартовый снимок ({len(first_frame)} байт)!")
                        self.put_ws({
                            "type": "video_stream_chunk",
                            "ui_msg_id": self.current_msg_id,
                            "video_base64": first_frame
                        })
                    else:
                        print("⚠️ Не удалось захватить первый кадр с камеры!")
                else:
                    print("⚠️ Камера не подключена к VoiceService!")
                
        return (None, pyaudio.paContinue)

    def play_audio_chunk(self, pcm_data):
        if not pcm_data: return
        self.is_speaking = True
        if self.player: self.player.set_volume_low()
        try: self.audio_out.write(pcm_data)
        except Exception as e: print(f"Ошибка вывода звука: {e}")

    def server_finished_response(self):
        if self.waiting_timer: self.waiting_timer.cancel()
        self.is_speaking = False
        self.is_waiting_for_server = False
        self.pre_buffer.clear()
        self.history_cleared_in_this_turn = False
        if self.player: self.player.set_volume_high()

    # <--- ДОБАВЬ ПАРАМЕТР user_msg_id=None
    def process_action(self, action_type, action_value, user_msg_id=None):
        a_type = str(action_type).lower()
        a_val = str(action_value)
        
        # --- НОВЫЙ БЛОК ДЛЯ ФОТО ---
        if a_type == "request_screenshot":
            print(f"📸 Сервер запросил скриншот (msg_id={user_msg_id})!")
            if self.camera:
                frame = self.camera.capture_base64()
                if frame:
                    # ДОБАВЛЕНЫ КЛЮЧИ type И command ДЛЯ МАРШРУТИЗАЦИИ СЕРВЕРА
                    self.put_ws({
                        "type": "web_command",
                        "command": "device_response",
                        "screenshot_base64_received": frame,
                        "screen_resolution": "640x480",
                        "user_msg_id": user_msg_id
                    })
                    print("✅ Скриншот успешно отправлен на сервер.")
                else:
                    self.put_ws({
                        "type": "web_command", 
                        "command": "device_response",
                        "screenshot_base64_received": "", 
                        "user_msg_id": user_msg_id
                    })
            else:
                self.put_ws({
                    "type": "web_command", 
                    "command": "device_response",
                    "screenshot_base64_received": "", 
                    "user_msg_id": user_msg_id
                })
        # ---------------------------

        elif a_type == "очистка истории":
            self.message_history.clear()
            self.history_cleared_in_this_turn = True
            print("🧹 Локальная история очищена.")
            
        elif a_type == "музыка":
            cmd = a_val.lower()
            if "включить" in cmd: self.player.update_playlist(); self.player.play(0)
            elif "выключить" in cmd or "стоп" in cmd or "пауза" in cmd: self.player.stop()
            elif "следующий" in cmd: self.player.next_track()
            elif "предыдущий" in cmd: self.player.prev_track()
            
        elif a_type == "смена голоса":
            valid_voices = ["Aoede", "Puck", "Kore", "Charon"]
            new_voice = a_val.strip().capitalize()
            if new_voice in valid_voices:
                state["voice_name"] = new_voice
                print(f"🗣️ Установлен новый голос: {state['voice_name']}")
            
        elif a_type == "движение":
            direction = a_val.strip().lower()
            if "вперед" in direction and self.robot:
                threading.Thread(target=self.robot.move_forward, daemon=True).start()

    def find_input_device(self):
        count = self.pa.get_device_count()
        candidate = None
        for i in range(count):
            try:
                info = self.pa.get_device_info_by_index(i)
                name = info.get('name', '').lower()
                if info['maxInputChannels'] > 0:
                    if "fifine" in name: candidate = i; break
                    if "usb" in name and "ms2109" not in name and not candidate: candidate = i
            except: continue
            
        if candidate is None:
             for i in range(count):
                 if self.pa.get_device_info_by_index(i).get('maxInputChannels') > 0: candidate = i; break
        
        if candidate is not None:
            self.device_index = candidate
            for rate in [16000, 44100, 48000, 32000, 8000]:
                try:
                    if self.pa.is_format_supported(rate, input_device=candidate, input_channels=1, input_format=pyaudio.paInt16):
                        self.sample_rate = rate
                        print(f"✅ Микрофон [{candidate}] принял частоту: {rate} Hz")
                        return candidate
                except: pass
            self.sample_rate = 16000
            return candidate
        return None

    def start_listening(self):
        device_index = self.find_input_device()
        if device_index is None:
            print("❌ Микрофоны не найдены!")
            return
            
        self.stream = self.pa.open(
            format=pyaudio.paInt16, channels=1, rate=self.sample_rate,
            input=True, input_device_index=device_index,
            frames_per_buffer=4000, stream_callback=self.audio_callback
        )
        self.stream.start_stream()
        print(f"✅ МИКРОФОН И VAD АКТИВНЫ (Ожидание речи...)")