#!/bin/bash
# Ждем полной загрузки графической среды
sleep 5

# Экспортируем переменные дисплея
export DISPLAY=:0
export XAUTHORITY=/home/avelgar/.Xauthority

# Запускаем ОБЪЕДИНЕННОЕ приложение
cd /home/avelgar
source /home/avelgar/myenv/bin/activate
# Запускаем наш главный файл, который теперь 
# содержит и камеру, и голос
python /home/avelgar/websocket_client_voice.py