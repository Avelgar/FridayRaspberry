#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автономный скрипт для управления 18 сервоприводами с помощью Xbox джойстика.
Работает абсолютно автономно: без Wi-Fi, без сервера, без микрофона и камеры.
"""
import time
import sys
from peripherals import RobotController, JoystickController

def main():
    print("=" * 60)
    print("🎮 ТЕСТ И АВТОНОМНОЕ УПРАВЛЕНИЕ 18 СЕРВОПРИВОДАМИ С XBOX ДЖОЙСТИКА")
    print("Режим: 100% Offline (Wi-Fi и интернет не требуются)")
    print("=" * 60)
    
    robot = RobotController()
    if not robot.available:
        print("⚠️ Предупреждение: Платы PCA9685 не ответили. Проверьте подключение I2C.")
    
    joystick = JoystickController(robot)
    joystick.start()
    
    print("\nДля выхода нажмите Ctrl+C\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nОстановка...")
        joystick.stop()
        robot.stop()
        print("Завершено.")

if __name__ == "__main__":
    main()
