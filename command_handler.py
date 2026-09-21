# -*- coding: utf-8 -*-
import json
import base64
import threading

def process_message(message_json, voice_service):
    try:
        data = json.loads(message_json)
        msg_type = data.get("type")
        user_msg_id = data.get("user_msg_id") # <--- ДОБАВИЛИ ЭТУ СТРОКУ

        if msg_type == "user_transcription":
            text = data.get("text", "")
            if text and "Аудиосообщение" not in text:
                print(f"🗣️ [ВЫ СКАЗАЛИ]: {text}")
                voice_service.message_history.append({"role": "user", "content": text})

        elif msg_type == "new_message":
            actions = data.get("actions", [])
            text = data.get("text", "")

            is_final = not text and not actions

            if actions:
                print(f"⚙️ [ДЕЙСТВИЯ ОТ СЕРВЕРА]: {json.dumps(actions, ensure_ascii=False)}")
            for act in actions:
                # <--- ПЕРЕДАЕМ user_msg_id ТРЕТЬИМ АРГУМЕНТОМ
                voice_service.process_action(act.get("action_type", ""), act.get("action_value", ""), user_msg_id)

            if text:
                print(f"🤖 [ПЯТНИЦА]: {text.strip()}")
                if not voice_service.history_cleared_in_this_turn:
                    if voice_service.message_history and voice_service.message_history[-1]["role"] == "model":
                        voice_service.message_history[-1]["content"] += text
                    else:
                        voice_service.message_history.append({"role": "model", "content": text})

            audio_b64 = data.get("audio_base64", "")
            if audio_b64:
                voice_service.play_audio_chunk(base64.b64decode(audio_b64))

            if is_final:
                print("✅ [СЕРВЕР]: Ход завершен, микрофон разблокирован.")
                voice_service.server_finished_response()

        elif msg_type == "audio_chunk":
            chunk = base64.b64decode(data.get("audio_base64", ""))
            voice_service.play_audio_chunk(chunk)

        elif msg_type == "delete_message":
            print("🗑️ [СЕРВЕР]: Сообщение удалено (пустой ответ).")
            voice_service.server_finished_response()

    except Exception as e:
        print(f"❌ Ошибка обработки JSON: {e}")