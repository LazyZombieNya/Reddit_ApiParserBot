import asyncio
import os
import logging
import re
import json
import html
import aiosqlite
import asyncpraw
from dotenv import load_dotenv

from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo, URLInputFile, FSInputFile
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramAPIError

from media_processor import download_media, clear_data_folder
from universal_translator import TranslationModule

# Ограничения Telegram
LIMIT_CAPTION = 1024  # Лимит символов описания поста телеграмм
LIMIT_TEXT_MSG = 4096  # Лимит символов для одного сообщения телеграмм
MAX_MEDIA_PER_GROUP = 10  # Лимит Telegram на медиа-группу

load_dotenv()  # Загружаем переменные из .env
# Настройки
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
# Reddit строго требует уникальный User-Agent, иначе заблокирует запросы
# Формат: платформа:название_приложения:версия (от /u/твой_ник)
USER_AGENT = os.getenv("REDDIT_USER_AGENT")

DB_NAME = "reddit_bot.db"
CONFIG_FILE = "config.json"

translator = TranslationModule(target_lang='ru')

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def format_reddit_text(text: str) -> str:
    """Экранирует опасные символы и превращает Markdown-ссылки в HTML."""
    if not text:
        return ""
    # 1. Экранируем спецсимволы Telegram
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 2. Ищем [Текст](URL) и меняем на <a href="URL">Текст</a>
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', text)
    return text


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
                         CREATE TABLE IF NOT EXISTS processed_posts
                         (
                             post_id
                             TEXT,
                             tg_channel
                             TEXT,
                             status
                             TEXT,
                             PRIMARY
                             KEY
                         (
                             post_id,
                             tg_channel
                         )
                             )
                         ''')
        await db.commit()


async def check_post_status(post_id: str, tg_channel: str) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT status FROM processed_posts WHERE post_id = ? AND tg_channel = ?',
                              (post_id, tg_channel)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def mark_post(post_id: str, tg_channel: str, status: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR REPLACE INTO processed_posts (post_id, tg_channel, status) VALUES (?, ?, ?)',
                         (post_id, tg_channel, status))
        await db.commit()


def split_html_text(text: str, max_length: int = LIMIT_TEXT_MSG) -> list:
    """Разбивает текст на куски, стараясь не разрывать HTML-теги и слова."""
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Ищем последний перенос строки или пробел в пределах лимита, чтобы не порвать тег
        split_at = text.rfind('\n', 0, max_length)
        if split_at == -1:
            split_at = text.rfind(' ', 0, max_length)

        # Если пробелов вообще нет (какая-то сплошная длинная ссылка), рубим жестко
        if split_at == -1:
            split_at = max_length

        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()

    return chunks


async def build_and_send_media(bot: Bot, tg_channel: str, media_items: list, caption: str, is_url: bool):
    if not media_items:
        return

    # 1. Проверяем, помещается ли текст в подпись к медиафайлу
    fits_in_caption = caption and len(caption) <= LIMIT_CAPTION

    # Разбиваем список файлов на пачки по MAX_MEDIA_PER_GROUP (10)
    chunks = [media_items[i:i + MAX_MEDIA_PER_GROUP] for i in range(0, len(media_items), MAX_MEDIA_PER_GROUP)]

    for chunk_idx, chunk in enumerate(chunks):
        media_group = []
        is_last_chunk = (chunk_idx == len(chunks) - 1)

        for item_idx, item in enumerate(chunk):
            is_last_item = (item_idx == len(chunk) - 1)

            # Текст добавляется к медиа только если он влезает в лимит и это последний файл
            cap = caption if (fits_in_caption and is_last_chunk and is_last_item) else None

            # Отрезаем параметры запроса перед проверкой расширения
            item_str = str(item).split('?')[0].lower()

            if item_str.endswith(('.mp4', '.avi', '.mov', '.webm', '.gif')):
                media_type = InputMediaVideo
            else:
                media_type = InputMediaPhoto

            file_obj = URLInputFile(item) if is_url else FSInputFile(item)
            media_group.append(media_type(media=file_obj, caption=cap, parse_mode="HTML"))

        # Отправка куска медиа
        if len(media_group) == 1:
            if isinstance(media_group[0], InputMediaVideo):
                await bot.send_video(tg_channel, media_group[0].media, caption=media_group[0].caption,
                                     parse_mode="HTML")
            else:
                await bot.send_photo(tg_channel, media_group[0].media, caption=media_group[0].caption,
                                     parse_mode="HTML")
        else:
            await bot.send_media_group(tg_channel, media_group)

        # Пауза между отправкой альбомов
        if not is_last_chunk:
            await asyncio.sleep(3)

    # 2. Если текст слишком большой для подписи, шлем его отдельными текстовыми сообщениями
    if caption and not fits_in_caption:
        text_chunks = split_html_text(caption, LIMIT_TEXT_MSG)

        for text_chunk in text_chunks:
            try:
                await bot.send_message(
                    tg_channel,
                    text_chunk,
                    parse_mode="HTML",
                    disable_web_page_preview=True  # Отключаем превью, чтобы не было дублей картинок
                )
                await asyncio.sleep(2)
            except TelegramAPIError as e:
                logger.error(f"Ошибка при отправке длинного текста вдогонку: {e}")


async def send_text_fallback(bot: Bot, tg_channel: str, caption_text: str, submission_id: str):
    try:
        await bot.send_message(tg_channel, caption_text, parse_mode="HTML", disable_web_page_preview=False)
        await mark_post(submission_id, tg_channel, "sent_as_text")
        logger.info(f"[{submission_id}] Успешно отправлен как текст (fallback).")
    except TelegramRetryAfter as e:
        logger.warning(f"[{submission_id}] Флуд-контроль (fallback)! Ожидание {e.retry_after} сек.")
        await asyncio.sleep(e.retry_after)
    except Exception as e:
        logger.error(f"[{submission_id}] Не удалось отправить текст: {e}")
        await mark_post(submission_id, tg_channel, "failed")


async def process_submission(bot: Bot, submission, target_config: dict):
    tg_channel = target_config["tg_channel"]

    status = await check_post_status(submission.id, tg_channel)
    if status in ("sent", "sent_as_text", "failed"):
        return

    logger.info(f"Обработка [{submission.id}]: {submission.title} -> {tg_channel}")

    safe_title = format_reddit_text(submission.title)
    short_url = f"https://redd.it/{submission.id}"

    # Переводим заголовок на русский
    translate_title = translator.translate_text(safe_title)
    caption_text = f"<b>{translate_title}</b>\n\n"

    # Проверяем, есть ли основной текст
    if submission.selftext:
        safe_text = format_reddit_text(submission.selftext[:700])
        translated_text = translator.translate_text(safe_text)
        caption_text += f"{translated_text}...\n\n" if len(submission.selftext) > 700 else f"{translated_text}\n\n"

    if submission.is_self:
        # Добавляем ссылку на сам пост Reddit
        caption_text += f"\n<a href='{short_url}'>Пост на Reddit</a>"
        logger.info(f"[{submission.id}] Это чисто текстовый пост. Отправляем.")
        await send_text_fallback(bot, tg_channel, caption_text, submission.id)
        await asyncio.sleep(3)
        return

    media_urls = []
    is_direct_media = False

    # 1. Проверка на Галерею (с сохранением оригинального порядка и поддержкой видео/гифок)
    if hasattr(submission, "is_gallery") and submission.is_gallery:
        if hasattr(submission, "gallery_data") and hasattr(submission, "media_metadata"):
            for item in submission.gallery_data.get('items', []):
                media_id = item.get('media_id')
                if media_id in submission.media_metadata:
                    media_info = submission.media_metadata[media_id]

                    if media_info['e'] == 'Image':
                        if 'u' in media_info['s']:
                            img_url = media_info['s']['u'].replace('&amp;', '&')
                            media_urls.append(img_url)
                    elif media_info['e'] == 'AnimatedImage':
                        if 'mp4' in media_info['s']:
                            vid_url = media_info['s']['mp4'].replace('&amp;', '&')
                            media_urls.append(vid_url)
                        elif 'gif' in media_info['s']:
                            vid_url = media_info['s']['gif'].replace('&amp;', '&')
                            media_urls.append(vid_url)
                    elif media_info['e'] == 'RedditVideo':
                        if 'fallbackUrl' in media_info:
                            vid_url = media_info['fallbackUrl'].replace('&amp;', '&')
                            media_urls.append(vid_url)
            if media_urls:
                is_direct_media = True

    # 2. Проверка на стандартное Reddit Видео
    elif submission.is_video and hasattr(submission,
                                         "media") and submission.media and "reddit_video" in submission.media:
        video_url = submission.media['reddit_video']['fallback_url']
        media_urls.append(video_url)
        is_direct_media = True

    # 3. Проверка на сторонние видеоплееры (RedGifs, Gfycat и др.), для которых Reddit создал превью
    elif hasattr(submission, 'preview') and 'reddit_video_preview' in submission.preview:
        video_url = submission.preview['reddit_video_preview']['fallback_url']
        media_urls.append(video_url)
        is_direct_media = True

    # 4. Простая прямая ссылка на картинку/гифку
    else:
        media_urls.append(submission.url)
        url_lower = submission.url.lower()
        if url_lower.endswith(('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm')):
            is_direct_media = True

    # Если медиа так и не нашлось, значит это внешняя ссылка (новости, статьи)
    if not is_direct_media:
        if not submission.is_self:
            # Явно прикрепляем оригинальную ссылку к тексту
            caption_text += f"\n🌐 <b>Внешняя ссылка:</b> <a href='{submission.url}'>{submission.url}</a>"

        # Добавляем Reddit ссылку и отправляем
        caption_text += f"\n<a href='{short_url}'>Пост на Reddit</a>"
        logger.info(f"[{submission.id}] Это ссылка на сторонний ресурс: {submission.url}. Отправляем текст.")
        await send_text_fallback(bot, tg_channel, caption_text, submission.id)
        await asyncio.sleep(3)
        return

    # СКАЧИВАНИЕ ФАЙЛОВ ЛОКАЛЬНО
    local_files = []

    # Добавляем цикл для 3-х повторных попыток скачивания
    for attempt in range(1, 4):
        for m_url in media_urls:
            downloaded_paths = await download_media(m_url)
            if downloaded_paths:
                local_files.extend(downloaded_paths)

        if local_files:
            break  # Успешно скачали, выходим из цикла
        else:
            logger.warning(f"[{submission.id}] Попытка скачивания {attempt}/3 не удалась. Пауза 5 сек...")
            await asyncio.sleep(5)

    if not local_files:
        logger.warning(f"[{submission.id}] Файлы не удалось скачать на сервер. Переход к текстовому фоллбэку.")
        caption_text += f"\n<a href='{short_url}'>Пост на Reddit</a>"
        await send_text_fallback(bot, tg_channel, caption_text, submission.id)
        await asyncio.sleep(3)
        return

    # ПЕРЕВОД ТЕКСТА С КАРТИНОК
    for file_path in local_files:
        # Проверяем, что скачанный файл — это картинка, а не видео
        if file_path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            logger.info(f"[{submission.id}] Распознаем текст на картинке: {file_path}")

            # Вызываем твой метод перевода
            img_translation = translator.process_image(file_path)

            # Проверяем, что текст найден и нет ошибок
            if img_translation and not img_translation.startswith("❌"):
                # ИСПРАВЛЕНИЕ: Обязательно экранируем распознанный текст, чтобы Telegram не принял < > за HTML-теги
                safe_img_text = format_reddit_text(img_translation)

                # Добавляем распознанный текст к описанию поста
                caption_text += f"\n📝 <b>Текст с картинки:</b>\n<blockquote><i>{safe_img_text}</i></blockquote>\n"

    # ИСПРАВЛЕНИЕ: Добавляем ссылку на Reddit В САМЫЙ КОНЕЦ (после текста с картинок)
    caption_text += f"\n<a href='{short_url}'>Пост на Reddit</a>"

    # ПОПЫТКА ОТПРАВИТЬ ЛОКАЛЬНЫЕ ФАЙЛЫ
    try:
        await build_and_send_media(bot, tg_channel, local_files, caption_text, is_url=False)
        await mark_post(submission.id, tg_channel, "sent")
        logger.info(f"[{submission.id}] Успешно отправлен локальным файлом.")
        await asyncio.sleep(3)
    except TelegramRetryAfter as e:
        logger.warning(f"[{submission.id}] Флуд-контроль (локальный файл)! Ожидание {e.retry_after} сек.")
        await asyncio.sleep(e.retry_after)
    except TelegramAPIError as e:
        # ИСПРАВЛЕНИЕ: Мы делаем просто return без mark_post().
        # Если API Telegram упадет, бот не отметит пост как "отправленный/сломанный"
        # и попытается отправить его снова при следующем обходе ленты.
        logger.warning(
            f"[{submission.id}] Ошибка Telegram API при отправке скачанного файла ({e}). Возврат в очередь.")
        return
    finally:
        await clear_data_folder()


async def monitor_reddit(bot: Bot):
    reddit = asyncpraw.Reddit(client_id=CLIENT_ID, client_secret=CLIENT_SECRET, user_agent=USER_AGENT)
    await init_db()
    logger.info("Бот запущен. Начинаем мониторинг...")

    while True:
        try:
            target_sources = load_config()
            for target in target_sources:
                sub_name = target["subreddit"]
                feed_type = target["feed_type"]
                min_score = target["min_score"]
                limit = target["limit"]

                subreddit = await reddit.subreddit(sub_name)
                feed_method = getattr(subreddit, feed_type)

                async for submission in feed_method(limit=limit):
                    if submission.score >= min_score:
                        await process_submission(bot, submission, target)
        except Exception as e:
            logger.error(f"Ошибка в основном цикле парсинга: {e}")

        await asyncio.sleep(300)


async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        await monitor_reddit(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Парсер остановлен.")