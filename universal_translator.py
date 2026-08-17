import os
import emoji
from PIL import Image
import pytesseract
from deep_translator import GoogleTranslator


# Указываем Python, где именно лежит установленный Tesseract в Windows
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

class TranslationModule:
    def __init__(self, target_lang='ru'):
        self.target_lang = target_lang
        self.translator = GoogleTranslator(source='auto', target=self.target_lang)

    def _clean_text(self, text: str) -> str:
        # Удаляем все эмодзи
        text_no_emoji = emoji.replace_emoji(text, replace='')
        return " ".join(text_no_emoji.split())

    def translate_text(self, text: str) -> str:
        """
        Пытается перевести текст. В случае любой ошибки возвращает оригинальный текст
        и записывает причину ошибки в лог.
        """
        if not text or not text.strip():
            return text

        try:
            # Попытка №1: Переводим как есть
            translated = self.translator.translate(text)

            print(f"Перевод:{translated}")
            # ЛОВУШКА ДЛЯ СКРЫТОЙ ОШИБКИ:
            # Если Google вернул текст ошибки как успешный перевод
            if translated and "Error 500 (Server Error)" in translated:
                raise Exception("Скрытая ошибка 500 от Google (вернулась в виде текста)")

            # Иногда библиотека может вернуть None вместо ошибки
            if not translated:
                print(f"API вернул пустой результат для текста: '{text[:50]}...'. Возвращаем оригинал.")
                return text

            return translated


        except Exception as e:

            error_msg = str(e)

            # Если словили 500 ошибку

            if "500" in error_msg or "Server Error" in error_msg:

                print(f"Ошибка 500. Пробуем очистить эмодзи в тексте: '{text[:50]}...'")

                try:

                    clean_text = self._clean_text(text)

                    # Если после удаления эмодзи текст стал пустым (были одни смайлики)

                    if not clean_text.strip():
                        print("После очистки от эмодзи текст стал пустым. Возвращаем оригинал.")

                        return text

                    # ПОВТОРНЫЙ ЗАПРОС

                    translated_clean = self.translator.translate(clean_text)

                    # =========================================================

                    # ВОТ ЭТОЙ ПРОВЕРКИ НЕ ХВАТАЛО!

                    # Проверяем, не вернул ли Google ошибку даже после очистки

                    if translated_clean and "Error 500 (Server Error)" in translated_clean:
                        print("Даже после очистки Google возвращает ошибку 500. Возвращаем оригинал.")

                        return text

                    # =========================================================

                    if not translated_clean:
                        print("API вернул пустой результат после очистки. Возвращаем оригинал.")

                        return text

                    return f"{translated_clean}"


                except Exception as fallback_e:

                    print(f"Ошибка перевода даже после очистки: {fallback_e}. Возвращаем оригинал.")

                    return text

            # Для любых других ошибок (нет интернета, таймаут и т.д.)

            print(f"Сбой перевода: {error_msg}. Текст: '{text[:50]}...'. Возвращаем оригинал.")

            return text

    def extract_text_from_image(self, image_path: str, ocr_langs='eng+rus') -> str:
        try:
            img = Image.open(image_path)
            extracted_text = pytesseract.image_to_string(img, lang=ocr_langs)
            return extracted_text.strip()
        except Exception as e:
            print(f"Ошибка OCR при чтении {image_path}: {e}")
            return ""

    def process_image(self, image_path: str) -> str:
        extracted_text = self.extract_text_from_image(image_path)
        if not extracted_text:
            return "❌ Не удалось распознать текст на изображении."

        return self.translate_text(extracted_text)