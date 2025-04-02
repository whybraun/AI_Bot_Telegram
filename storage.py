import sqlite3
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('posts.db')
        self.create_tables()
        
    def create_tables(self):
        """Создание таблиц с логированием"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS posts
                           (id TEXT PRIMARY KEY,
                            text TEXT,
                            image_path TEXT,
                            status TEXT,
                            created_at TIMESTAMP)''')
            self.conn.commit()
            logger.info("Таблицы БД инициализированы")
        except Exception as e:
            logger.error(f"Ошибка БД: {str(e)}")
            raise

    def save_post(self, post_id, text, image_bytes=None):
        """Сохранение поста с логированием"""
        try:
            image_path = None
            if image_bytes:
                os.makedirs("images", exist_ok=True)
                image_path = f"images/{post_id}.png"
                with open(image_path, 'wb') as f:
                    f.write(image_bytes)
            
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO posts VALUES (?, ?, ?, ?, ?)",
                (post_id, text, image_path, 'pending', datetime.now())
            )
            self.conn.commit()
            logger.info(f"Сохранен пост {post_id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения: {str(e)}")
            raise