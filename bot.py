import os
import asyncio
import sqlite3
import threading
import queue
from queue import Queue
import time
from datetime import datetime
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler
from groq import Groq
from utils.rss_parser import parse_rss
from utils.image_gen import generate_image
import logging
from PIL import Image, ImageDraw, ImageFont
import io
import requests
import signal
import random
from typing import Optional

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class NewsBot:
    def __init__(self):
        self.shutdown_event = threading.Event()
        self.db_queue = Queue()
        self._init_db_worker()
        self._check_env()
        self._init_clients()
        self._test_rss_feeds()
        self._init_processed_urls_db()
        self.fallback_image = self._load_fallback_image()
        
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info(f"Получен сигнал {signum}, завершаем работу...")
        self.shutdown_event.set()

    def _load_fallback_image(self) -> Optional[bytes]:
        try:
            fallback_path = os.path.join("assets", "fallback.png")
            if os.path.exists(fallback_path):
                with open(fallback_path, "rb") as f:
                    return f.read()
            logger.warning("Файл fallback.png не найден в папке assets")
            return None
        except Exception as e:
            logger.error(f"Ошибка загрузки fallback-изображения: {str(e)}")
            return None

    def _init_db_worker(self):
        def db_worker():
            conn = sqlite3.connect('posts.db', check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS posts
                           (id TEXT PRIMARY KEY,
                            text TEXT,
                            image_path TEXT,
                            status TEXT,
                            source TEXT,
                            url TEXT UNIQUE,
                            created_at TEXT)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS processed_urls
                           (url TEXT PRIMARY KEY,
                            processed_at TEXT)''')
            conn.commit()
            
            while not self.shutdown_event.is_set():
                try:
                    task = self.db_queue.get(timeout=1)
                    if task[0] == 'save_post':
                        _, post_id, text, image_bytes, source, url = task
                        image_path = None
                        if image_bytes:
                            os.makedirs("images", exist_ok=True)
                            image_path = f"images/{post_id}.png"
                            with open(image_path, 'wb') as f:
                                f.write(image_bytes)
                        
                        cursor.execute(
                            "INSERT OR IGNORE INTO posts VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (post_id, text, image_path, 'pending', source, url, datetime.now().isoformat())
                        )
                        conn.commit()
                    
                    elif task[0] == 'update_status':
                        _, post_id, status = task
                        cursor.execute(
                            "UPDATE posts SET status=? WHERE id=?",
                            (status, post_id)
                        )
                        conn.commit()

                except queue.Empty:
                    pass
                except Exception as e:
                    logger.error(f"Ошибка в рабочем потоке БД: {str(e)}")

            conn.close()
            logger.info("Рабочий поток БД остановлен")

        self.db_thread = threading.Thread(target=db_worker, daemon=True)
        self.db_thread.start()

    def _init_processed_urls_db(self):
        conn = sqlite3.connect('posts.db')
        conn.close()

    def _check_env(self):
        required_vars = ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_ADMIN_CHAT_ID', 
                        'TELEGRAM_CHANNEL_ID', 'GROQ_API_KEY', 'STABILITY_API_KEY']
        for var in required_vars:
            if not os.getenv(var):
                raise ValueError(f"Отсутствует обязательная переменная окружения: {var}")

    def _init_clients(self):
        self.groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
        self.bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
        
        try:
            test_image = generate_image("test connection")
            if test_image:
                logger.info("Stability API подключен успешно")
            else:
                logger.warning("Stability API не вернул изображение")
        except Exception as e:
            logger.error(f"Ошибка подключения к Stability API: {str(e)}")

    def _test_rss_feeds(self):
        rss_urls = [
            "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
            "https://export.arxiv.org/rss/cs.AI",
            "https://rsshub.app/deepmind/blog",
            "https://venturebeat.com/category/ai/feed/",
            "https://www.theverge.com/rss/ai/index.xml",
            "https://syncedreview.com/tag/artificial-intelligence/feed/",
            "https://hnrss.org/newest?q=AI+OR+LLM+OR+GPT",
            "https://lobste.rs/t/ai.rss"
        ]
        
        logger.info("=== ПРОВЕРКА RSS-ЛЕНТ ===")
        working_feeds = 0
        
        for url in rss_urls:
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    logger.info(f"✓ Рабочий RSS: {url}")
                    working_feeds += 1
                else:
                    logger.warning(f"✗ Недоступен (код {response.status_code}): {url}")
            except Exception as e:
                logger.error(f"✗ Ошибка подключения к {url}: {str(e)}")
        
        logger.info(f"Итого: {working_feeds}/{len(rss_urls)} рабочих RSS-лент")

    def _add_watermark(self, image_bytes: bytes) -> bytes:
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert('RGBA')
            width, height = img.size
            
            watermark = Image.new('RGBA', img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(watermark)
            
            font_size = max(int(width * 0.03), 14)
            
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except:
                font = ImageFont.load_default()
                font.size = font_size
            
            watermark_text = "@ai_revo"
            text_width = draw.textlength(watermark_text, font=font)
            
            x = width - text_width - 10
            y = height - font_size - 10
            
            draw.rectangle(
                [x - 5, y - 2, x + text_width + 5, y + font_size + 2],
                fill=(0, 0, 0, 120))
            
            draw.text((x, y), watermark_text, font=font, fill=(255, 255, 255, 220))
            
            watermarked = Image.alpha_composite(img, watermark)
            
            output = io.BytesIO()
            watermarked.save(output, format='PNG')
            return output.getvalue()
        except Exception as e:
            logger.error(f"Ошибка добавления водяного знака: {str(e)}")
            return image_bytes

    async def generate_news_text(self, title: str, description: str) -> str:
        try:
            response = self.groq.chat.completions.create(
                model="llama3-70b-8192",
                messages=[{
                    "role": "system",
                    "content": """Ты профессиональный журналист, пишешь для Telegram-канала @ai_revo об искусственном интеллекте.
⚡ Пиши **коротко, ясно, по делу**. **Без воды**, только важное.  
🎯 Ориентируйся на Telegram-формат — емкость важнее деталей.  

Строго соблюдай правила оформления:  
1. **Заголовок** (переводи на русский):  
   - 📌 <b>Краткий, цепляющий заголовок с эмодзи</b>  
   - Максимально 8-10 слов.  
2. **Основной текст**:  
   - 🔍 Короткое введение (1-2 предложения).  
   - 📌 Ключевые факты (3-5 пунктов, без лишних деталей).  
   - 💡 Итог: почему это важно?  
3. **Оформление**:  
   - **Переводи** заголовки и текст на **русский**!  
   - Используй HTML-форматирование: <b>жирный</b>, <i>курсив</i>, <code>код</code>.  
   - Эмодзи — для логического разделения блоков (но **не более 5** на пост).  
   - Абзацы **короткие** (1-2 предложения).  
4. **Конец поста**:  
   - 🌐 Источник: <a href="URL">Название сайта</a>.  
   - 🔔 <b>Подпишись на @ai_revo</b> — только важные новости об ИИ!  
"""
                }, {
                    "role": "user",
                    "content": f"Заголовок: {title}\n\nТекст: {description}"
                }],
                temperature=0.5,
                max_tokens=1000,
                top_p=0.9
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка генерации текста новости: {str(e)}")
            return f"📌 <b>{title}</b>\n\n{description}\n\n🔔 <b>Подпишись на @ai_revo</b>"

    def _generate_safe_image_prompt(self, title: str) -> str:
        banned_words = ["nude", "sexy", "violence", "blood", "war", "kill", 
                       "attack", "weapon", "gun", "assault", "porn", "nsfw"]
        
        clean_title = title.lower()
        for word in banned_words:
            clean_title = clean_title.replace(word, "")
        
        base_prompt = (
            "Abstract technology concept, digital art, futuristic style, "
            "blue and purple color scheme, corporate safe, no people, "
            "no violence, professional illustration, safe for work"
        )
        
        return f"{clean_title[:150]}, {base_prompt}"

    async def _generate_and_process_image(self, title: str) -> Optional[bytes]:
        try:
            image_prompt = self._generate_safe_image_prompt(title)
            logger.info(f"Генерация изображения для: {title[:50]}...")
            
            image_bytes = generate_image(image_prompt)
            
            if image_bytes:
                image_bytes = self._add_watermark(image_bytes)
                logger.info("Изображение успешно сгенерировано")
                return image_bytes
            
            logger.warning("Не удалось сгенерировать изображение, используем fallback")
            return self.fallback_image
            
        except Exception as e:
            logger.error(f"Ошибка генерации изображения: {str(e)}")
            return self.fallback_image

    async def process_news(self):
        try:
            logger.info("=== НАЧАЛО ОБРАБОТКИ НОВОСТЕЙ ===")
            urls = [
                "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
                "https://export.arxiv.org/rss/cs.AI",
                "https://rsshub.app/deepmind/blog",
                "https://venturebeat.com/category/ai/feed/",
                "https://www.theverge.com/rss/ai/index.xml",
                "https://syncedreview.com/tag/artificial-intelligence/feed/",
                "https://hnrss.org/newest?q=AI+OR+LLM+OR+GPT",
                "https://lobste.rs/t/ai.rss"
            ]
            
            entries = parse_rss(urls)
            logger.info(f"Найдено {len(entries)} новостей из {len(urls)} источников")

            conn = sqlite3.connect('posts.db')
            cursor = conn.cursor()
            cursor.execute("SELECT url FROM processed_urls")
            processed_urls = {row[0] for row in cursor.fetchall()}
            
            new_entries = [entry for entry in entries if entry.get('url') not in processed_urls]
            logger.info(f"Новых постов для обработки: {len(new_entries)}")

            for i, entry in enumerate(new_entries, 1):
                if self.shutdown_event.is_set():
                    break
                    
                try:
                    logger.info(f"Обработка {i}/{len(new_entries)}: {entry.get('source', 'Неизвестный источник')}")
                    
                    if not entry.get('url'):
                        logger.warning("Пропускаем запись без URL")
                        continue
                    
                    cursor.execute(
                        "INSERT OR IGNORE INTO processed_urls VALUES (?, ?)",
                        (entry['url'], datetime.now().isoformat())
                    )
                    conn.commit()
                    
                    post_text = await self.generate_news_text(
                        entry.get('title', ''), 
                        entry.get('description', '')
                    )
                    
                    image_bytes = await self._generate_and_process_image(entry.get('title', ''))
                    
                    await self._send_for_moderation(
                        text=post_text,
                        image_bytes=image_bytes,
                        source=entry.get('source', 'Неизвестный источник'),
                        url=entry.get('url')
                    )
                    
                    await asyncio.sleep(15)
                    
                except Exception as e:
                    logger.error(f"Ошибка обработки новости: {str(e)}")
                    await asyncio.sleep(30)
            
            conn.close()
            logger.info("=== ЗАВЕРШЕНИЕ ОБРАБОТКИ НОВОСТЕЙ ===")
            
        except Exception as e:
            logger.critical(f"Критическая ошибка: {str(e)}")

    async def _send_for_moderation(self, text: str, image_bytes: bytes = None, 
                             source: str = None, url: str = None):
        post_id = f"post-{int(time.time())}-{hash(text) % 10000}"
        
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Опубликовать", callback_data=f"approve:{post_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{post_id}")
        ]])
        
        caption = f"{text}"
        
        try:
            if image_bytes:
                await self.bot.send_photo(
                    chat_id=os.getenv("TELEGRAM_ADMIN_CHAT_ID"),
                    photo=image_bytes,
                    caption=caption[:1024],
                    reply_markup=keyboard,
                    parse_mode='HTML'
                )
            else:
                await self.bot.send_message(
                    chat_id=os.getenv("TELEGRAM_ADMIN_CHAT_ID"),
                    text=caption,
                    reply_markup=keyboard,
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
            
            self.db_queue.put(('save_post', post_id, text, image_bytes, source, url))
            logger.info(f"Пост {post_id} отправлен на модерацию")
        except Exception as e:
            logger.error(f"Ошибка отправки на модерацию: {str(e)}")

    async def handle_button(self, update, context):
        query = update.callback_query
        await query.answer()
        
        try:
            action, post_id = query.data.split(':', 1)
            logger.info(f"Обработка: {action} для поста {post_id}")
            
            if action == 'approve':
                # Обновляем статус в БД
                self.db_queue.put(('update_status', post_id, 'published'))
                
                # Получаем данные поста из БД
                conn = sqlite3.connect('posts.db')
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT text, image_path, source, url FROM posts WHERE id=?",
                    (post_id,)
                )
                post = cursor.fetchone()
                conn.close()
                
                if post:
                    text, image_path, source, url = post
                    caption = f"{source}\n\n{text}\n\n{url}" if url else f"{source}\n\n{text}"
                    
                    try:
                        if image_path and os.path.exists(image_path):
                            with open(image_path, 'rb') as f:
                                await self.bot.send_photo(
                                    chat_id=os.getenv("TELEGRAM_CHANNEL_ID"),
                                    photo=f,
                                    caption=caption[:1000],
                                    parse_mode='HTML'
                                )
                        else:
                            await self.bot.send_message(
                                chat_id=os.getenv("TELEGRAM_CHANNEL_ID"),
                                text=caption,
                                parse_mode='HTML',
                                disable_web_page_preview=True
                            )
                        
                        # Редактируем сообщение с кнопками
                        try:
                            if hasattr(query.message, 'caption'):
                                new_text = f"✅ Опубликовано\n\n{query.message.caption}"
                                await query.edit_message_caption(
                                    caption=new_text[:1024],
                                    reply_markup=None
                                )
                            else:
                                new_text = f"✅ Опубликовано\n\n{query.message.text}"
                                await query.edit_message_text(
                                    text=new_text,
                                    reply_markup=None,
                                    parse_mode='HTML',
                                    disable_web_page_preview=True
                                )
                        except Exception as e:
                            logger.error(f"Ошибка редактирования сообщения: {str(e)}")
                            
                    except Exception as e:
                        logger.error(f"Ошибка публикации в канал: {str(e)}")
                        await query.answer("⚠️ Ошибка публикации", show_alert=True)
                else:
                    await query.answer("⚠️ Пост не найден", show_alert=True)
            
            elif action == 'reject':
                try:
                    if hasattr(query.message, 'caption'):
                        new_text = f"❌ Отклонено\n\n{query.message.caption}"
                        await query.edit_message_caption(
                            caption=new_text[:1024],
                            reply_markup=None
                        )
                    else:
                        new_text = f"❌ Отклонено\n\n{query.message.text}"
                        await query.edit_message_text(
                            text=new_text,
                            reply_markup=None,
                            parse_mode='HTML',
                            disable_web_page_preview=True
                        )
                except Exception as e:
                    logger.error(f"Ошибка редактирования сообщения: {str(e)}")
                
        except Exception as e:
            logger.error(f"Ошибка обработки кнопки: {str(e)}")
            await query.answer("⚠️ Произошла ошибка", show_alert=True)

    def run(self):
        """Запуск бота с проверкой новостей каждые 2 часа"""
        try:
            CHECK_INTERVAL = 60 * 60 * 2  # 2 часа
            
            async def news_loop():
                while not self.shutdown_event.is_set():
                    try:
                        start_time = time.time()
                        logger.info("=== ЗАПУСК ПРОВЕРКИ НОВОСТЕЙ ===")
                        
                        await self.process_news()
                        
                        elapsed = time.time() - start_time
                        sleep_time = max(CHECK_INTERVAL - elapsed, 0)
                        logger.info(f"Следующая проверка через {sleep_time/60:.1f} минут")
                        
                        await asyncio.sleep(sleep_time)
                            
                    except Exception as e:
                        logger.error(f"Ошибка в цикле проверки новостей: {str(e)}")
                        await asyncio.sleep(60)
                
                logger.info("Цикл проверки новостей остановлен")
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            application = Application.builder() \
                .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
                .build()
            
            application.add_handler(CallbackQueryHandler(self.handle_button))
            
            async def main():
                news_task = asyncio.create_task(news_loop())
                await application.initialize()
                await application.start()
                await application.updater.start_polling()
                
                while not self.shutdown_event.is_set():
                    await asyncio.sleep(1)
                
                await application.stop()
                await application.shutdown()
                news_task.cancel()
            
            try:
                loop.run_until_complete(main())
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            finally:
                loop.close()
            
            logger.info("Бот успешно остановлен")
        except Exception as e:
            logger.critical(f"Фатальная ошибка при запуске: {str(e)}")

if __name__ == "__main__":
    bot = NewsBot()
    bot.run()