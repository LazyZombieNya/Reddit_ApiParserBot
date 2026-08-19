# Используем легковесный образ Python
FROM python:3.11-slim

# Установка системных зависимостей: FFmpeg и Tesseract с русским и английским словарями
RUN apt-get update && apt-get install -y \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Настройка рабочей директории внутри контейнера
WORKDIR /app

# Копируем файл с библиотеками и устанавливаем их
# Убедись, что файл requirements.txt содержит все нужные либы (praw, aiogram, pytesseract и т.д.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь остальной код
COPY . .

# Запускаем скрипт c отключением буферизации логов
CMD ["python", "-u", "main.py"]