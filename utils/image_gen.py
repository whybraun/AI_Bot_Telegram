import os
import base64
import requests
import logging
from dotenv import load_dotenv
from typing import Optional
import time

load_dotenv()
logger = logging.getLogger(__name__)

class ImageGenerator:
    def __init__(self):
        self.api_key = os.getenv("STABILITY_API_KEY")
        self.api_host = "https://api.stability.ai"
        self.engine_id = "stable-diffusion-xl-1024-v1-0"
        
        if not self.api_key:
            logger.error("STABILITY_API_KEY не найден в .env")
            raise ValueError("API ключ Stability AI не найден")

    def _make_safe_prompt(self, original_prompt: str) -> str:
        """Создает абсолютно безопасный промпт"""
        banned_words = ["nude", "sexy", "violence", "blood", "war", "kill", 
                      "attack", "weapon", "gun", "assault", "porn", "nsfw"]
        
        # Очищаем промпт
        clean_prompt = original_prompt.lower()
        for word in banned_words:
            clean_prompt = clean_prompt.replace(word, "")
        
        # Базовый безопасный промпт
        base_prompt = (
            "Abstract technology concept, digital art, futuristic style, "
            "blue and purple color scheme, corporate safe, no people, "
            "no violence, professional illustration"
        )
        
        return f"{clean_prompt[:200]}, {base_prompt}"

    def generate_image(self, original_prompt: str) -> Optional[bytes]:
        """Генерирует изображение через REST API"""
        if not original_prompt:
            logger.error("Получен пустой промпт")
            return None

        safe_prompt = self._make_safe_prompt(original_prompt)
        logger.info(f"Генерация изображения по промпту: {safe_prompt}")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "text_prompts": [{
                "text": safe_prompt,
                "weight": 1
            }],
            "cfg_scale": 7,
            "height": 1024,
            "width": 1024,
            "samples": 1,
            "steps": 30,
            "style_preset": "digital-art"
        }

        try:
            response = requests.post(
                f"{self.api_host}/v1/generation/{self.engine_id}/text-to-image",
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                for image in data["artifacts"]:
                    return base64.b64decode(image["base64"])
            else:
                error_msg = response.text
                logger.error(f"Ошибка API: {response.status_code} - {error_msg}")
                return None

        except Exception as e:
            logger.error(f"Ошибка запроса: {str(e)}")
            return None

# Глобальный экземпляр генератора
image_generator = ImageGenerator()

def generate_image(prompt: str) -> bytes:
    """Обертка для совместимости"""
    return image_generator.generate_image(prompt)