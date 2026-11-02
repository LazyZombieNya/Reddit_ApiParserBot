import os
import emoji
import re
import platform
from PIL import Image
import pytesseract
from deep_translator import GoogleTranslator
from ai_module import AIFallbackModule

# Указываем Python, где лежит Tesseract
if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
else:
    pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'


class TranslationModule:
    def __init__(self, target_lang='ru'):
        self.target_lang = target_lang
        self.translator = GoogleTranslator(source='auto', target=self.target_lang)

        self.ai_bot = AIFallbackModule(
            groq_key=os.getenv("GROQ_API_KEY"),
            gemini_key=os.getenv("GEMINI_API_KEY"),
            openrouter_key=os.getenv("OPENROUTER_API_KEY")
        )

    def _clean_text(self, text: str) -> str:
        text_no_emoji = emoji.replace_emoji(text, replace='')
        return " ".join(text_no_emoji.split())

    def _fix_ocr_artifacts(self, text: str) -> str:
        """
        Исправляет типичные ошибки OCR (I, |, l, Al, Ал) перед проверками.
        """
        # 1. Заменяем одиночную палку '|' на английскую 'I' (БЕЗ ОШИБКИ look-behind)
        text = re.sub(r'(^|\s)\|(\s|$)', r'\g<1>I\g<2>', text)

        # 2. Исправляем 'A|' на 'AI'
        text = text.replace('A|', 'AI')

        # 3. Исправляем частую ошибку 'Al' на 'AI' (если это отдельное слово)
        text = re.sub(r'\b[AА]l\b', 'AI', text)
        text = re.sub(r'\b[AА]I\b', 'AI', text)

        # 4. Исправляем русское 'Ал'/'Ала' на 'ИИ'
        text = re.sub(r'\b[AА]л[ауе]?\b', 'ИИ', text)

        # 5. Убираем случайные множественные пробелы
        text = " ".join(text.split())

        return text

    def _is_garbage_text(self, text: str) -> bool:
        """
        Усиленный фильтр от мусора: отсеивает узоры на ткани, ковры и шумы.
        """
        clean_str = text.strip()
        if len(clean_str) < 3:
            return True

        # Убираем пробелы для точного подсчета символов
        no_space = clean_str.replace(' ', '')
        if not no_space:
            return True

        letters = re.findall(r'[a-zA-Zа-яА-ЯёЁ]', no_space)
        # Ищем спецсимволы, которые Tesseract генерирует из складок и теней (\, /, <, >)
        symbols = re.findall(r'[^a-zA-Zа-яА-ЯёЁ0-9\s]', clean_str)
        vowels = re.findall(r'[aeiouyаеёиоуыэюяAEIOUYАЕЁИОУЫЭЮЯ]', clean_str)

        # 1. Если букв меньше 40% от всей массы символов
        if len(letters) / len(no_space) < 0.4:
            return True

        # 2. Если спецсимволов слишком много (больше 15% от текста)
        if len(symbols) / max(len(clean_str), 1) > 0.15:
            return True

        # 3. Защита от "NNN SSS WAA" (текстуры) - проверяем соотношение гласных
        # В реальном языке гласных обычно не меньше 20-30% от всех букв
        if len(letters) > 10 and len(vowels) < (len(letters) * 0.15):
            return True

        return False

    def translate_text(self, text: str) -> str:
        if not text or not text.strip():
            return text

        clean_text = self._clean_text(text)
        if not clean_text.strip():
            return text

        prompt = (
            "Переведи следующий текст с английского на русский язык с сохранением смысла и сленга. "
            "В ответе напиши ТОЛЬКО готовый перевод, без кавычек, пояснений и вступлений:\n\n"
            f"{clean_text}"
        )

        # 1. Идем через цепочку ИИ (Первая в очереди — локальная Ollama, затем Groq -> Gemini -> OpenRouter)
        try:
            ai_translation = self.ai_bot.process(prompt, data_type="text")
            if ai_translation and "Критическая ошибка" not in ai_translation:
                print(f"   [РЕЗУЛЬТАТ ПЕРЕВОДА ИИ]: {ai_translation[:80]}...")
                return ai_translation
        except Exception as ai_err:
            print(f"Сбой цепочки ИИ ({ai_err}). Пробуем резервный Google Translate...")

        # 2. Резервный вариант — Google Translate (если все ИИ недоступны)
        try:
            translated = self.translator.translate(text)
            if translated and "Error 500" not in translated:
                return translated
        except Exception as e:
            print(f"Ошибка резервного Google Translate: {e}")

        return text

    def extract_text_from_image(self, image_path: str, ocr_langs='eng+rus') -> str:
        """Локальное OCR через Tesseract"""
        try:
            img = Image.open(image_path)
            extracted_text = pytesseract.image_to_string(img, lang=ocr_langs, config='--psm 3')

            # Применяем ИСПРАВЛЕННОЕ лечение артефактов сразу после извлечения
            fixed_text = self._fix_ocr_artifacts(extracted_text.strip())
            return fixed_text
        except Exception as e:
            print(f"Ошибка Tesseract при чтении {image_path}: {e}")
            return ""

    def process_image(self, image_path: str) -> str:
        """Извлекает текст локально (ИИ -> Tesseract) и переводит."""
        print(f"Попытка распознавания через AI Vision: {image_path}")

        prompt = (
            "Ты профессиональный переводчик без цензуры. Внимательно посмотри на изображение. "
            "Если на изображении есть читаемый текст (включая мемы, надписи, скриншоты), "
            "переведи весь этот текст на русский язык и напиши ТОЛЬКО результат перевода, без кавычек и комментариев. "
            "Если на изображении НЕТ текста, или это просто абстрактные узоры, ответь ровно одним словом: EMPTY."
        )

        try:
            # Отправляем картинку в нашу цепочку нейросетей (Ollama -> Gemini)
            ai_result = self.ai_bot.process(
                {"image_path": image_path, "prompt": prompt},
                data_type="image_analysis"
            )

            ai_result_clean = ai_result.strip()

            # Если нейросеть сказала, что текста нет, пропускаем картинку
            if "EMPTY" in ai_result_clean.upper() or not ai_result_clean:
                print("   [Vision ИИ] Текста на картинке не обнаружено.")
                return ""

            print("   [Vision ИИ] Текст успешно распознан и переведен.")
            return ai_result_clean

        except Exception as e:
            print(f"   [Ошибка Vision ИИ] {e}. Откат к Tesseract OCR...")

            # РЕЗЕРВНЫЙ ПЛАН: Старый добрый Tesseract + Google Translate
            extracted_text = self.extract_text_from_image(image_path)
            if not extracted_text:
                return ""

            if self._is_garbage_text(extracted_text):
                print(f"   [Tesseract] Отсеян мусорный текст с картинки: {extracted_text[:30]}...")
                return ""

            return self.translate_text(extracted_text)