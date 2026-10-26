import time
import requests
import base64
import urllib.parse

class AIFallbackModule:
    def __init__(self, groq_key, gemini_key, openrouter_key):
        self.groq_key = groq_key
        self.gemini_key = gemini_key
        self.openrouter_key = openrouter_key

    def process(self, data, data_type="text"):
        if data_type == "text":
            return self._handle_text_fallback(data)
        elif data_type == "image_analysis":
            return self._call_gemini_vision(data)
        elif data_type == "generate_image":
            return self._call_pollinations_image(data)
        elif data_type == "generate_audio":
            return self._call_pollinations_audio(data)
        else:
            raise ValueError(f"Неизвестный тип данных: {data_type}")

    def _handle_text_fallback(self, prompt):
        print("-> [Маршрут 1] Пробуем Groq (Высокая скорость)...")
        try:
            result = self._call_groq(prompt)
            print(f"   [УСПЕХ Groq] Ответ получен: {result[:50]}...")
            return result
        except Exception as e:
            print(f"   [Ошибка Groq] {e}")
            time.sleep(2)

        print("-> [Маршрут 2] Пробуем Gemini 2.0 Flash (Глубокий контекст)...")
        try:
            result = self._call_gemini_text(prompt)
            print(f"   [УСПЕХ Gemini] Ответ получен: {result[:50]}...")
            return result
        except Exception as e:
            print(f"   [Ошибка Gemini] {e}")
            time.sleep(2)

        print("-> [Маршрут 3] Пробуем OpenRouter (Универсальный шлюз)...")
        try:
            result = self._call_openrouter_free(prompt)
            print(f"   [УСПЕХ OpenRouter] Ответ получен: {result[:50]}...")
            return result
        except Exception as e:
            return f"Критическая ошибка: Все бесплатные нейросети перегружены. Подробности: {e}"

    def _call_groq(self, prompt):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.groq_key}",
            "Content-Type": "application/json"
        }
        payload = {
            # Актуальная модель на замену Llama 3.1 8B Instant
            "model": "openai/gpt-oss-20b",
            "messages": [{"role": "user", "content": prompt}]
        }
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: {response.text}")
        return response.json()["choices"][0]["message"]["content"]

    def _call_gemini_text(self, prompt):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.gemini_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
            ]
        }
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: {response.text}")
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]

    def _call_gemini_vision(self, data):
        image_path = data["image_path"]
        prompt = data["prompt"]

        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.gemini_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": encoded_string}}
                ]
            }]
        }

        for attempt in range(1, 4):
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 429:
                print(f"   [Gemini Vision] Лимит (429). Ждем 15 секунд (попытка {attempt}/3)...")
                time.sleep(15)
                continue
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}: {response.text}")
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]

        raise Exception("Gemini Vision недоступен (Лимит 429)")

    def _call_openrouter_free(self, prompt):
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "Content-Type": "application/json"
        }
        payload = {
            # Используем отличную бесплатную модель из вашего списка OpenRouter
            "model": "poolside/laguna-s-2.1:free",
            "messages": [{"role": "user", "content": prompt}]
        }
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: {response.text}")
        return response.json()["choices"][0]["message"]["content"]