#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import websockets
import ssl
import json
import time
import base64
import threading
import sys

sys.stdout.reconfigure(line_buffering=True)

from config import URI, PING_INTERVAL, MUSIC_FOLDER
from peripherals import RobotController, MusicPlayer, CameraManager, JoystickController
from voice_service import VoiceService
from command_handler import process_message

def get_mac_address():
    try:
        with open('/sys/class/net/wlan0/address', 'r') as f: 
            return f.read().strip()
    except Exception: 
        return "00:11:22:33:44:55"

async def websocket_handler(ws_queue, voice_service, mac):
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE 
    
    while True:
        try:
            print(f"🌐 Подключение к {URI}...")
            async with websockets.connect(URI, ssl=ssl_ctx) as ws:
                print("✅ WebSocket ПОДКЛЮЧЕН!")
                
                # Гостевая авторизация
                reg = json.dumps({"MAC": mac, "DeviceName": "PiBot", "Password": "123"})
                await ws.send(base64.b64encode(reg.encode()).decode())
                
                async def send_loop():
                    while True:
                        data = await ws_queue.get()
                        if data.get("type") == "ping":
                            data["mac"] = mac
                        payload = base64.b64encode(json.dumps(data, ensure_ascii=False).encode()).decode()
                        await ws.send(payload)
                        
                async def ping_loop():
                    while True:
                        await asyncio.sleep(PING_INTERVAL)
                        voice_service.put_ws({"type": "ping", "timestamp": time.time()})
                        
                async def recv_loop():
                    while True:
                        msg = await ws.recv()
                        decoded = base64.b64decode(msg).decode()
                        process_message(decoded, voice_service)

                await asyncio.gather(send_loop(), ping_loop(), recv_loop())

        except Exception as e:
            print(f"⚠️ Разрыв связи / нет Wi-Fi. Реконнект через 5с... ({e})")
            await asyncio.sleep(5)

def main():
    print("=" * 45)
    print("🤖 ПЯТНИЦА PiBot ЗАПУСКАЕТСЯ (18 СЕРВО + XBOX ДЖОЙСТИК)...")
    print("=" * 45)
    
    mac_address = get_mac_address()
    loop = asyncio.new_event_loop()
    ws_queue = asyncio.Queue()
    
    music_player = MusicPlayer(MUSIC_FOLDER)
    robot = RobotController()
    camera = CameraManager()
    
    # Запуск автономного управления с джойстика (работает всегда, даже без Wi-Fi!)
    joystick = JoystickController(robot)
    joystick.start()
    
    # Голосовой сервис
    voice_service = VoiceService(ws_queue, loop, mac_address, music_player, robot, camera)
    
    # Запуск записи микрофона
    voice_service.start_listening()
    
    # Запуск WebSocket в фоновом потоке
    t_ws = threading.Thread(
        target=lambda: loop.run_until_complete(websocket_handler(ws_queue, voice_service, mac_address)),
        daemon=True
    )
    t_ws.start()
    
    print("💤 Пятница активна. Доступно управление голосом и джойстиком!")
    try:
        while True: 
            time.sleep(1)
    except KeyboardInterrupt: 
        print("\nОстановка служб...")
        joystick.stop()
        robot.stop()
        camera.stop()
        print("Выход завершен.")

if __name__ == "__main__":
    main()
