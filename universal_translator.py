import os
import emoji
import re
import platform
from PIL import Image
import pytesseract
from deep_translator import GoogleTranslator

# Указываем Python, где лежит Tesseract в зависимости от ОС
if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
else:
    # Стандартный путь в Debian/Ubuntu
    pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'


class TranslationModule:
    def __init__(self, target_lang='ru'):
        self.target_lang = target_lang
        self.translator = GoogleTranslator(source='auto', target=self.target_lang)

    def _clean_text(self, text: str) -> str:
        # Удаляем все эмодзи
        text_no_emoji = emoji.replace_emoji(text, replace='')
        return " ".join(text_no_emoji.split())

    def _is_garbage_text(self, text: str) -> bool:
        """
        Фильтр от мусора: проверяет, является ли текст настоящим.
        Отсеивает узоры на коврах, стежки на ткани и случайные тени.
        """
        clean_str = text.strip()
        # Если текст короче 3 символов, это наверняка шум
        if len(clean_str) < 3:
            return True

        # Считаем количество РЕАЛЬНЫХ букв (кириллица и латиница)
        letters = re.findall(r'[a-zA-Zа-яА-ЯёЁ]', clean_str)

        # Если букв меньше 40% от всей длины строки (остальное - знаки '=' или пробелы), это мусор
        if len(letters) / max(len(clean_str), 1) < 0.4:
            return True

        return False

    def translate_text(self, text: str) -> str:
        if not text or not text.strip():
            return text

        try:
            translated = self.translator.translate(text)

            if translated and "Error 500 (Server Error)" in translated:
                raise Exception("Скрытая ошибка 500 от Google")

            if not translated:
                return text

            return translated

        except Exception as e:
            error_msg = str(e)
            if "500" in error_msg or "Server Error" in error_msg:
                try:
                    clean_text = self._clean_text(text)
                    if not clean_text.strip():
                        return text

                    translated_clean = self.translator.translate(clean_text)

                    if translated_clean and "Error 500 (Server Error)" in translated_clean:
                        return text

                    if not translated_clean:
                        return text

                    return f"{translated_clean}"

                except Exception as fallback_e:
                    return text

            return text

    def extract_text_from_image(self, image_path: str, ocr_langs='eng+rus') -> str:
        try:
            img = Image.open(image_path)
            # psm 3 - автоматический режим сегментации (стандартный, но надежный)
            extracted_text = pytesseract.image_to_string(img, lang=ocr_langs, config='--psm 3')
            return extracted_text.strip()
        except Exception as e:
            print(f"Ошибка OCR при чтении {image_path}: {e}")
            return ""

    def process_image(self, image_path: str) -> str:
        extracted_text = self.extract_text_from_image(image_path)

        if not extracted_text:
            return ""  # Возвращаем пустоту, чтобы не писать "Не удалось распознать..."

        if self._is_garbage_text(extracted_text):
            print(f"Отсеян мусорный текст с картинки: {extracted_text}")
            return ""  # Игнорируем картинку, если там знаки препинания

        return self.translate_text(extracted_text)