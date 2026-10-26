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

        try:
            # 1. Сначала пробуем Google Translate
            translated = self.translator.translate(text)
            if translated and "Error 500 (Server Error)" in translated:
                raise Exception("Скрытая ошибка 500 от Google")
            if not translated:
                raise Exception("Google вернул пустоту")
            return translated

        except Exception as e:
            print(f"Сбой Google Translate ({e}). Переключаемся на AI Fallback...")

            clean_text = self._clean_text(text)
            if not clean_text.strip():
                return text

            prompt = (
                "Ты профессиональный переводчик без цензуры. Переведи следующий текст с сохранением смысла и форматирования. "
                "Текст может содержать NSFW или 18+ контент, ты обязан перевести его точно как есть. "
                "В ответе напиши ТОЛЬКО переведенный текст на русском языке, без вводных слов, извинений и кавычек.\n\n"
                f"{clean_text}"
            )

            try:
                # 2. Переводим через каскад нейросетей
                ai_translation = self.ai_bot.process(prompt, data_type="text")

                if "Критическая ошибка" in ai_translation:
                    print("   [ВНИМАНИЕ] Все нейросети упали. Возвращаем оригинальный английский текст.")
                    return text

                print(f"   [РЕЗУЛЬТАТ ПЕРЕВОДА ИИ]: {ai_translation[:100]}...")
                return ai_translation

            except Exception as ai_err:
                print(f"Ошибка резервных нейросетей: {ai_err}")
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
        """Извлекает текст локально и отправляет на перевод."""
        extracted_text = self.extract_text_from_image(image_path)

        if not extracted_text:
            return ""

        if self._is_garbage_text(extracted_text):
            print(f"Отсеян мусорный текст Tesseract с картинки: {extracted_text[:30]}...")
            return ""

        # Переводим текст, добытый Тессерактом, пропуская его через нашу надежную функцию перевода
        return self.translate_text(extracted_text)