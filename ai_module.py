import time
import requests
import base64
from io import BytesIO
from PIL import Image


class AIFallbackModule:
    def __init__(
        self,
        groq_key,
        gemini_key,
        openrouter_key,
        ollama_url="http://192.168.1.36:11434",
        ollama_text_model="qwen2.5:1.5b",
        ollama_vision_model="qwen2.5vl:3b"
    ):
        self.groq_key = groq_key
        self.gemini_key = gemini_key
        self.openrouter_key = openrouter_key
        self.ollama_url = ollama_url
        self.ollama_text_model = ollama_text_model
        self.ollama_vision_model = ollama_vision_model

    def process(self, data, data_type="text"):
        if data_type == "text":
            return self._handle_text_fallback(data)
        elif data_type == "image_analysis":
            return self._handle_vision_fallback(data)
        else:
            raise ValueError(f"Неизвестный тип данных: {data_type}")

    def _handle_text_fallback(self, prompt):
        print(f"-> [Маршрут 1] Пробуем локальную Ollama ({self.ollama_text_model})...")
        try:
            result = self._call_ollama_text(prompt)
            print(f"   [УСПЕХ Ollama] Ответ получен: {result[:50]}...")
            return result
        except Exception as e:
            print(f"   [Ошибка Ollama Text] {e}")

        print("-> [Маршрут 2] Пробуем Groq (Высокая скорость)...")
        try:
            result = self._call_groq(prompt)
            print(f"   [УСПЕХ Groq] Ответ получен: {result[:50]}...")
            return result
        except Exception as e:
            print(f"   [Ошибка Groq] {e}")

        print("-> [Маршрут 3] Пробуем Gemini Flash...")
        try:
            result = self._call_gemini_text(prompt)
            print(f"   [УСПЕХ Gemini] Ответ получен: {result[:50]}...")
            return result
        except Exception as e:
            print(f"   [Ошибка Gemini] {e}")

        print("-> [Маршрут 4] Пробуем OpenRouter...")
        try:
            result = self._call_openrouter_free(prompt)
            print(f"   [УСПЕХ OpenRouter] Ответ получен: {result[:50]}...")
            return result
        except Exception as e:
            return f"Критическая ошибка: Все нейросети недоступны. Подробности: {e}"

    def _handle_vision_fallback(self, data):
        print(f"-> [Vision 1] Пробуем локальную Ollama ({self.ollama_vision_model})...")
        try:
            result = self._call_ollama_vision(data)
            print(f"   [УСПЕХ Ollama Vision] Ответ получен: {result[:50]}...")
            return result
        except Exception as e:
            print(f"   [Ошибка Ollama Vision] {e}")

        print("-> [Vision 2] Пробуем Gemini Vision (Резерв)...")
        try:
            result = self._call_gemini_vision(data)
            print(f"   [УСПЕХ Gemini Vision] Ответ получен: {result[:50]}...")
            return result
        except Exception as e:
            print(f"   [Ошибка Gemini Vision] {e}")
            raise Exception("Обе Vision-модели недоступны.")

    # ================== ЛОКАЛЬНАЯ OLLAMA ==================
    def _call_ollama_text(self, prompt):
        url = f"{self.ollama_url}/api/chat"
        payload = {
            "model": self.ollama_text_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "keep_alive": "24h",
            "options": {
                "temperature": 0.1,
                "num_predict": 400,
                "num_ctx": 2048
            }
        }
        # 180 секунд запаса для тяжелой текстовой модели (4b/7b)
        response = requests.post(url, json=payload, timeout=180)
        response.raise_for_status()
        return response.json()["message"]["content"].strip()

    def _call_ollama_vision(self, data):
        image_path = data["image_path"]
        prompt = data["prompt"]

        # Сжимаем копию для ИИ до 512x512 (на 36% меньше пикселей, чем 640x640 -> на треть быстрее на CPU)
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail((512, 512), Image.LANCZOS)
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=80)
            encoded_string = base64.b64encode(buffer.getvalue()).decode("utf-8")

        url = f"{self.ollama_url}/api/chat"
        payload = {
            "model": self.ollama_vision_model,
            "messages": [{
                "role": "user",
                "content": prompt,
                "images": [encoded_string]
            }],
            "stream": False,
            "think": False,
            "keep_alive": "24h",
            "options": {
                "temperature": 0.1,
                "num_predict": 300,
                "num_ctx": 2048
            }
        }
        # Ставим таймаут 300 секунд (5 минут), чтобы модель гарантированно успела ответить
        response = requests.post(url, json=payload, timeout=300)
        response.raise_for_status()
        return response.json()["message"]["content"].strip()

    # ================== ОБЛАЧНЫЕ ПРОВАЙДЕРЫ ==================
    def _call_groq(self, prompt):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.groq_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "openai/gpt-oss-20b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        }
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: {response.text}")
        return response.json()["choices"][0]["message"]["content"].strip()

    def _call_gemini_text(self, prompt):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.gemini_key}"
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
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: {response.text}")
        return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    def _call_gemini_vision(self, data):
        image_path = data["image_path"]
        prompt = data["prompt"]

        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.gemini_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": encoded_string}}
                ]
            }]
        }
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}")
        return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    def _call_openrouter_free(self, prompt):
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "openrouter/free",  # Автоматический выбор работающей бесплатной модели
            "messages": [{"role": "user", "content": prompt}]
        }
        response = requests.post(url, json=payload, headers=headers, timeout=25)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: {response.text}")
        return response.json()["choices"][0]["message"]["content"].strip()